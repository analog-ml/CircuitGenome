"""
Bias-generation pruning and tail-current bias-rail assignment for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``bias_generation`` variant exposes four output rails (``out1``..``out4``)
so it can feed the most demanding ``load`` (a differential-output folded-cascode,
which needs all four). Most loads need fewer: simple loads (resistor/active/
current-source) need none, telescopic cascode loads need one, and single-output
folded-cascode loads need two. In multi-stage topologies, ``second_stage`` /
``third_stage`` slots also tap ``out1`` for their own gate bias, so ``out1`` is
mandatory whenever such a slot exists, regardless of the load.

:func:`needed_bias_outputs` inspects the topology and the chosen variants (other
than ``bias_generation`` itself) to find which of ``out1``..``out4`` are actually
consumed -- by checking real device-terminal references, not just declared ports
(many loads declare ``bias2``/``bias3``/``bias_cmfb`` as ``optional`` ports that
no internal device wires up).

Independently, some ``tail_current`` variants (the current-mirror and
cascode-current-mirror flavors) need their own bias voltage on their local
``bias`` port to set up the mirror reference; the resistor-tail flavors do not.
:func:`tail_current_needs_bias` detects this structurally, and
:func:`assign_tail_bias_rail` picks a *dedicated* rail for it -- never shared
with ``load``/``second_stage``/``third_stage`` -- immediately after the highest
rail those slots need. If that would be rail 5 (i.e. ``load_needed`` already
uses all of ``out1``..``out4``), :func:`extend_bias_generation` grows the
``bias_generation`` variant with a fifth leg (``out5``) before pruning.

Every ``bias_generation`` variant shares one structural layout: a *shared
reference device* (mirrors ``ibias`` onto an internal reference node, and never
touches ``out1``..``out4``) plus, for each output rail ``i``, one self-contained
*leg* of one or more devices that mirrors the reference and delivers ``out_i``
via its own complete current path. :func:`prune_bias_generation` drops whole
legs whose rail index exceeds ``max(needed)`` (and the corresponding output
port), leaving the shared reference device and any remaining legs untouched.
:func:`extend_bias_generation` is the structural inverse: it clones the
highest-indexed existing leg onto a new rail (``out{max+1}``), incrementing each
cloned device's reference designator.

A device belongs to a leg for rail ``outN`` if any of its terminals reference
``outN`` -- this is structural, so new ``bias_generation`` variants following the
same shared-reference-plus-legs pattern are pruned/extended correctly without
code changes, as long as no single device's terminals reference two different
``out1``..``out4`` rails (true for all current variants).
"""
from __future__ import annotations
import dataclasses

from .models import Device, ModuleVariant, PortDef, TopologyTemplate

_BIAS_RAILS = ("out1", "out2", "out3", "out4")
_BIAS_NET_INDEX = {f"net_bias{i}": i for i in range(1, 5)}


def needed_bias_outputs(
    topology: TopologyTemplate,
    variant_map: dict[str, "ModuleVariant"],
) -> set[int]:
    """Return the set of bias-rail indices (1-4) actually consumed.

    For every slot other than ``bias_generation``, checks whether the variant
    has a device terminal referencing a port that the topology wires to
    ``net_bias1``..``net_bias4``. Declared-but-unwired ``optional`` ports (e.g.
    an unused ``bias3`` on a telescopic cascode load) are ignored.
    """
    needed: set[int] = set()
    for slot in topology.slots:
        if slot.category == "bias_generation":
            continue
        variant = variant_map[slot.name]
        used_local_nets = {
            local_net
            for dev in variant.devices
            for local_net in dev.terminals.values()
        }
        for port_name, net in topology.slot_connections(slot.name).items():
            idx = _BIAS_NET_INDEX.get(net)
            if idx is not None and port_name in used_local_nets:
                needed.add(idx)
    return needed


def _prune_independent_legs(devices: list[Device], drop_rails: set[str]) -> list[Device]:
    """Drop every device belonging to a leg in *drop_rails*.

    A device "belongs to" rail ``outN`` if any of its terminals reference
    ``outN``. The shared reference device (whose terminals reference only
    ``ibias``/``vdd``/``gnd``, never ``out1``..``out4``) always has an empty
    ``refs`` set and is therefore never dropped.
    """
    result = []
    for dev in devices:
        refs = {t for t in dev.terminals.values() if t in _BIAS_RAILS}
        if refs and refs.issubset(drop_rails):
            continue
        result.append(dev)
    return result


def _extend_independent_legs(devices: list[Device], old_rail: str, new_rail: str) -> list[Device]:
    """Clone every device of the leg for *old_rail* onto *new_rail*.

    For each device whose terminals reference *old_rail*, append a copy with
    *old_rail* rewired to *new_rail* and its reference designator incremented
    (e.g. ``mp5`` -> ``mp6``), so the new leg's devices have refs distinct from
    every existing device in *devices*.
    """
    new_devices = list(devices)
    for dev in devices:
        if old_rail in dev.terminals.values():
            prefix = dev.ref.rstrip("0123456789")
            num = int(dev.ref[len(prefix):])
            new_devices.append(dataclasses.replace(
                dev,
                ref=f"{prefix}{num + 1}",
                terminals={
                    term: (new_rail if local_net == old_rail else local_net)
                    for term, local_net in dev.terminals.items()
                },
            ))
    return new_devices


def prune_bias_generation(variant: ModuleVariant, needed: set[int]) -> ModuleVariant:
    """Return a copy of *variant* with unused ``out1``..``out4`` (or ``out5``,
    after :func:`extend_bias_generation`) rails removed.

    *needed* is the set of rail indices that must be kept -- the union of
    :func:`needed_bias_outputs` and (if applicable) the rail assigned by
    :func:`assign_tail_bias_rail`. Rails with index greater than
    ``max(needed, default=0)`` are dropped, along with the leg devices that
    exist only to drive them (see :func:`_prune_independent_legs`). If
    ``max(needed) >= 4``, *variant* is returned unchanged -- this also covers
    the rail-5 case, since a variant extended by :func:`extend_bias_generation`
    is only ever pruned with ``needed`` containing both 4 and 5.
    """
    max_needed = max(needed, default=0)
    if max_needed >= 4:
        return variant

    drop_rails = {f"out{i}" for i in range(max_needed + 1, 5)}
    new_devices = _prune_independent_legs(variant.devices, drop_rails)
    new_ports = [p for p in variant.ports if p.name not in drop_rails]

    return dataclasses.replace(variant, ports=new_ports, devices=new_devices)


def tail_current_needs_bias(variant: ModuleVariant) -> bool:
    """Return ``True`` if *variant* has a device terminal wired to its local
    ``bias`` port.

    The current-mirror and cascode-current-mirror ``tail_current`` variants
    use ``bias`` as the diode-connected mirror-reference node and need it
    driven by ``bias_generation``. The resistor-tail variants declare ``bias``
    as ``role: optional`` and never reference it -- they need no bias rail.
    """
    return any(
        local_net == "bias"
        for dev in variant.devices
        for local_net in dev.terminals.values()
    )


def assign_tail_bias_rail(load_needed: set[int]) -> int:
    """Return the dedicated bias-rail index (1-5) for ``tail_current``.

    ``tail_current``'s bias reference must never share a rail with
    ``load``/``second_stage``/``third_stage`` (each rail's voltage is tuned
    for its own consumer). The dedicated rail is the next index after the
    highest one *load_needed* already uses -- i.e. ``max(load_needed) + 1``,
    or rail 1 if *load_needed* is empty. If that would exceed 4 (i.e.
    *load_needed* is already ``{1, 2, 3, 4}``), rail 5 is returned instead,
    requiring :func:`extend_bias_generation` to add a fifth leg.
    """
    candidate = max(load_needed, default=0) + 1
    return candidate if candidate <= 4 else 5


def extend_bias_generation(variant: ModuleVariant) -> ModuleVariant:
    """Return a copy of *variant* with one additional output rail and leg.

    Structural inverse of :func:`prune_bias_generation`: finds the
    highest-indexed ``out1``..``out4`` rail still present on *variant*, clones
    every device of that rail's leg onto a new rail one higher (incrementing
    each cloned device's reference designator -- see
    :func:`_extend_independent_legs`), and appends a matching output
    :class:`~circuitgenome.synthesizer.models.PortDef`.

    Used only for the rail-5 overflow case: when ``tail_current`` needs its
    own dedicated bias rail but ``load``/``second_stage``/``third_stage``
    already need all four of ``out1``..``out4``. The new ``out5`` port is
    appended after ``vdd``/``gnd`` (port order becomes
    ``[ibias, out1..out4, vdd, gnd, out5]``); this is harmless since
    ``_build_port_net_map``/``_resolve_devices`` look up ports by name.
    """
    max_rail = max(
        int(local_net[len("out"):])
        for dev in variant.devices
        for local_net in dev.terminals.values()
        if local_net in _BIAS_RAILS
    )
    old_rail, new_rail = f"out{max_rail}", f"out{max_rail + 1}"

    new_devices = _extend_independent_legs(variant.devices, old_rail, new_rail)
    new_ports = [*variant.ports, PortDef(name=new_rail, role="output")]

    return dataclasses.replace(variant, ports=new_ports, devices=new_devices)
