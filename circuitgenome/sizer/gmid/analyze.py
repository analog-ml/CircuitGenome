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
    _check_topology_match,
    _deduplicate,
    _extract_slot_resistors,
    _extract_slot_transistors,
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


def analyze_circuit(
    fbr_result: FunctionalBlockRecognitionResult, topology: TopologyTemplate
) -> CircuitView:
    """Build the :class:`CircuitView` from the FBR assignments."""
    slot_transistors = _extract_slot_transistors(fbr_result)
    slot_resistors = _extract_slot_resistors(fbr_result)
    return CircuitView(
        slot_transistors=slot_transistors,
        slot_resistors=slot_resistors,
        all_transistors=_deduplicate(slot_transistors),
        blocks=build_blocks(slot_transistors, slot_resistors),
        cascode_refs=cascode_device_refs(slot_transistors),
        warnings=_check_topology_match(slot_transistors, topology.name),
    )
