"""
Bias-generation pruning for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``bias_generation`` variant exposes seven output rails
(``out1``..``out7``), one per bias-consuming role in the topology:

- ``out1``..``out4`` feed ``load.bias1``/``bias2``/``bias3``/``bias_cmfb``
  (a differential-output folded-cascode load needs all four; simpler loads
  need fewer or none).
- ``out5`` feeds ``second_stage*.bias`` (shared by ``second_stage_p``/
  ``second_stage_n`` in fully-differential topologies -- both are wired to
  the same ``net_bias5``).
- ``out6`` feeds ``third_stage*.bias`` (shared the same way, via
  ``net_bias6``).
- ``out7`` feeds ``tail_current.bias`` (current-mirror / cascode-current-
  mirror tails only; resistor-tail variants declare ``bias`` as ``optional``
  and never reference it).

Each role's rail is independent of the others -- ``load``, ``second_stage``,
``third_stage``, and ``tail_current`` never share a bias voltage, so each can
be sized independently later.

:func:`needed_bias_outputs` inspects the topology and the chosen variants
(other than ``bias_generation`` itself) to find which of ``out1``..``out7``
are actually consumed -- by checking real device-terminal references, not
just declared ports (many loads declare ``bias2``/``bias3``/``bias_cmfb`` as
``optional`` ports that no internal device wires up, and resistor-tail
variants never reference ``bias``). The result can be any subset of
``{1..7}``, not necessarily contiguous (e.g. ``{1, 5, 7}``).

Every ``bias_generation`` variant shares one structural layout: a *shared
reference device* (mirrors ``ibias`` onto an internal reference node, and
never touches ``out1``..``out7``) plus, for each output rail ``i``, one
self-contained *leg* of two devices that mirrors the reference and delivers
``out_i`` via its own complete current path. :func:`prune_bias_generation`
drops every leg whose rail is not in ``needed`` (and its output port),
leaving the shared reference device and the needed legs untouched -- e.g.
``needed == {5, 7}`` keeps the shared reference plus legs 5 and 7 only,
regardless of legs 1-4 and 6.

A device belongs to a leg for rail ``outN`` if any of its terminals reference
``outN`` -- this is structural, so new ``bias_generation`` variants following
the same shared-reference-plus-legs pattern are pruned correctly without code
changes, as long as no single device's terminals reference two different
``out1``..``out7`` rails (true for all current variants).
"""
from __future__ import annotations
import dataclasses

from .bias_compatibility import rail_flavor_from_diode
from .models import Device, ModuleVariant, TopologyTemplate

_BIAS_RAILS = tuple(f"out{i}" for i in range(1, 8))
_BIAS_NET_INDEX = {f"net_bias{i}": i for i in range(1, 8)}


def needed_bias_outputs(
    topology: TopologyTemplate,
    variant_map: dict[str, "ModuleVariant"],
) -> set[int]:
    """Return the set of bias-rail indices (1-7) actually consumed.

    For every slot other than ``bias_generation``, checks whether the variant
    has a device terminal referencing a port that the topology wires to
    ``net_bias1``..``net_bias7``. Declared-but-unwired ``optional`` ports
    (e.g. an unused ``bias3`` on a telescopic cascode load, or ``bias`` on a
    resistor-tail ``tail_current`` variant) are ignored. The result can be any
    subset of ``{1..7}``, not necessarily contiguous.
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
    ``ibias``/``vdd``/``gnd``, never ``out1``..``out7``) always has an empty
    ``refs`` set and is therefore never dropped.
    """
    result = []
    for dev in devices:
        refs = {t for t in dev.terminals.values() if t in _BIAS_RAILS}
        if refs and refs.issubset(drop_rails):
            continue
        result.append(dev)
    return result


def prune_bias_generation(variant: ModuleVariant, needed: set[int]) -> ModuleVariant:
    """Return a copy of *variant* with every rail not in *needed* removed.

    *needed* (from :func:`needed_bias_outputs`) is the set of rail indices --
    a subset of ``{1..7}``, not necessarily contiguous -- that must be kept.
    Every other rail's leg is dropped, along with its output port (see
    :func:`_prune_independent_legs`) -- e.g. ``needed == {1, 5}`` drops legs
    2, 3, 4, 6, 7 and keeps the shared reference device plus legs 1 and 5. If
    *needed* covers all of ``{1..7}``, *variant* is returned unchanged.
    """
    drop_rails = {f"out{i}" for i in range(1, 8) if i not in needed}
    if not drop_rails:
        return variant

    new_devices = _prune_independent_legs(variant.devices, drop_rails)
    new_ports = [p for p in variant.ports if p.name not in drop_rails]

    return dataclasses.replace(variant, ports=new_ports, devices=new_devices)


def prune_redundant_tail_diode(
    bias_variant: ModuleVariant, tail_variant: ModuleVariant
) -> ModuleVariant:
    """Drop the rail-7 diode when the tail brings its own reference diode.

    Current-mirror tails convert the rail-7 current into their mirror-gate
    voltage with their own diode-connected reference device on ``bias``
    (``d == g == bias``).  A bias-generation leg whose ``out7`` device is a
    diode-connected MOSFET of the same *flavor* (see
    :func:`~circuitgenome.synthesizer.bias_compatibility.rail_flavor_from_diode`)
    then sits **in parallel** with that reference: the two identical diodes
    split the leg current, so the tail mirror no longer sees the full
    reference current (and the recognizer cannot tell them apart, leaving one
    unassigned and unsized).  Dropping the bias-side diode leaves rail 7 as a
    clean current source/sink into the tail's own diode.  Gate-driven mirror
    legs and resistor legs (no rail-7 diode) are untouched, as is everything
    when the tail has no diode on ``bias`` (resistor tails, pruned
    placeholders).  Cross-flavor pairings -- where the leg's diode would
    *fight* the tail's reference instead of duplicating it -- are rejected
    upstream by
    :func:`~circuitgenome.synthesizer.bias_compatibility.is_bias_flavor_compatible`
    and never reach this prune.
    """
    tail_flavor = rail_flavor_from_diode(tail_variant.devices, "bias")
    if tail_flavor is None:
        return bias_variant
    if rail_flavor_from_diode(bias_variant.devices, "out7") != tail_flavor:
        return bias_variant
    new_devices = [
        dev for dev in bias_variant.devices
        if not (dev.type in ("nmos", "pmos")
                and dev.terminals.get("d") == "out7"
                and dev.terminals.get("g") == "out7")
    ]
    return dataclasses.replace(bias_variant, devices=new_devices)
