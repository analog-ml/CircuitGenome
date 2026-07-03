"""Phase 1 — Analyze: build the structural view of the recognised circuit.

Everything the later phases need to know about the circuit's *structure* is
derived here, once: the per-slot device lists, the deduplicated ref→(device,
slot) map, the typed block view (load kind, stage count, fully-differential
flag), the cascode devices, and the topology-mismatch warnings.  All of it is
read-only from here on.
"""
from __future__ import annotations

from dataclasses import dataclass

from circuitgenome.recognizer.models import FunctionalBlockRecognitionResult
from circuitgenome.synthesizer.models import Device, TopologyTemplate

from ..shared.preprocess import (
    check_topology_match,
    deduplicate_devices,
    extract_slot_resistors,
    extract_slot_transistors,
)
from .blocks import OpAmpBlocks, build_blocks, cascode_device_refs


@dataclass
class CircuitView:
    """Structural view of the circuit (Phase 1 output).

    :param slot_transistors: FBR slot name → MOSFET devices in that slot.
    :param slot_resistors: FBR slot name → resistor devices in that slot.
    :param all_transistors: deduplicated ref → (Device, owning slot); a device
        appearing in several slots is attributed to the highest-priority one.
    :param blocks: the typed :class:`~.blocks.OpAmpBlocks` decomposition.
    :param cascode_refs: refs of stacked (cascode) current-source devices.
    :param warnings: topology-mismatch advisories from the structural check.
    """
    slot_transistors: dict[str, list[Device]]
    slot_resistors: dict[str, list[Device]]
    all_transistors: dict[str, tuple[Device, str]]
    blocks: OpAmpBlocks
    cascode_refs: set[str]
    warnings: list[str]


def _adopt_orphan_mosfets(
    slot_transistors: dict[str, list[Device]],
    fbr_result: FunctionalBlockRecognitionResult,
    topology: TopologyTemplate,
) -> None:
    """Attribute slot-suffixed MOSFETs the FBR left unassigned to their slot.

    Pattern matching can leave a device out of every slot when its structure
    is ambiguous (two identical parallel diodes on one net) or incomplete (a
    bias leg whose expected partner lives in another slot, e.g. a cascode
    tail's reference diode).  Such a device would silently go **unsized** and
    run at the simulator's default W/L.  Synthesized netlists name every
    device ``{ref}_{slot_name}``, so orphans are attributed -- and therefore
    sized -- by their ref suffix; devices whose ref matches no slot (external
    netlists) are left alone.
    """
    assigned = {d.ref for devs in slot_transistors.values() for d in devs}
    slot_names = sorted((s.name for s in topology.slots), key=len, reverse=True)
    candidates = [d for s in fbr_result.unassigned_structures for d in s.devices]
    candidates += list(fbr_result.unrecognized_devices)
    for dev in candidates:
        if dev.type not in ("nmos", "pmos") or dev.ref in assigned:
            continue
        slot = next((s for s in slot_names if dev.ref.endswith("_" + s)), None)
        if slot is not None:
            assigned.add(dev.ref)
            slot_transistors.setdefault(slot, []).append(dev)


def analyze_circuit(
    fbr_result: FunctionalBlockRecognitionResult, topology: TopologyTemplate
) -> CircuitView:
    """Build the :class:`CircuitView` from the FBR assignments."""
    slot_transistors = extract_slot_transistors(fbr_result)
    _adopt_orphan_mosfets(slot_transistors, fbr_result, topology)
    slot_resistors = extract_slot_resistors(fbr_result)
    return CircuitView(
        slot_transistors=slot_transistors,
        slot_resistors=slot_resistors,
        all_transistors=deduplicate_devices(slot_transistors),
        blocks=build_blocks(slot_transistors, slot_resistors),
        cascode_refs=cascode_device_refs(slot_transistors),
        warnings=check_topology_match(slot_transistors, topology.name),
    )
