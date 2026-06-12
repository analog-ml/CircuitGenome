"""
Bias-generation pruning for :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

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

:func:`prune_bias_generation` then strips the unused tail of output rails from a
``bias_generation`` variant, dropping the corresponding ports and the devices
that exist only to drive them. Across all current module variants, the needed
set is always a contiguous prefix starting at 1 (``{}``, ``{1}``, ``{1,2}``, or
``{1,2,3,4}``), so pruning is expressed as "keep everything up to the highest
needed index, drop the rest."

Two structural device layouts are supported:

- **Ladder** (``diode_connected_mosfet_bias``, ``resistor_bias``): a series chain
  ``ibias -> dev1 -> out1 -> dev2 -> out2 -> ... -> dev5 -> gnd`` where each
  device's two bias-rail terminals are consecutive taps. Pruning keeps the first
  ``max_needed + 1`` devices and rewires the last kept device's far terminal from
  the first dropped rail to ``gnd``, so the chain still terminates correctly.
- **Independent legs** (``magic_battery_bias``): a shared reference device plus
  one self-contained mirror leg per output rail. Pruning drops whole legs whose
  rail is unused and keeps the shared reference device untouched.

A variant is classified as a ladder if any single device references two or more
distinct ``out1``..``out4`` rails; this is structural, so new ``bias_generation``
variants following either pattern are pruned correctly without code changes.
"""
from __future__ import annotations
import dataclasses

from .models import Device, ModuleVariant, TopologyTemplate

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


def _is_ladder(devices: list[Device]) -> bool:
    return any(
        len({t for t in dev.terminals.values() if t in _BIAS_RAILS}) >= 2
        for dev in devices
    )


def _prune_ladder(devices: list[Device], max_needed: int) -> list[Device]:
    keep = devices[: max_needed + 1]
    boundary_rail = f"out{max_needed + 1}"
    *head, last = keep
    rewired_last = dataclasses.replace(
        last,
        terminals={
            term: ("gnd" if local_net == boundary_rail else local_net)
            for term, local_net in last.terminals.items()
        },
    )
    return [*head, rewired_last]


def _prune_independent_legs(devices: list[Device], drop_rails: set[str]) -> list[Device]:
    result = []
    for dev in devices:
        refs = {t for t in dev.terminals.values() if t in _BIAS_RAILS}
        if refs and refs.issubset(drop_rails):
            continue
        result.append(dev)
    return result


def prune_bias_generation(variant: ModuleVariant, needed: set[int]) -> ModuleVariant:
    """Return a copy of *variant* with unused ``out1``..``out4`` rails removed.

    *needed* is the set returned by :func:`needed_bias_outputs`. Rails with
    index greater than ``max(needed, default=0)`` are dropped, along with the
    devices that exist only to drive them. If all four rails are needed,
    *variant* is returned unchanged.
    """
    max_needed = max(needed, default=0)
    if max_needed >= 4:
        return variant

    drop_rails = {f"out{i}" for i in range(max_needed + 1, 5)}

    if _is_ladder(variant.devices):
        new_devices = _prune_ladder(variant.devices, max_needed)
    else:
        new_devices = _prune_independent_legs(variant.devices, drop_rails)

    new_ports = [p for p in variant.ports if p.name not in drop_rails]

    return dataclasses.replace(variant, ports=new_ports, devices=new_devices)
