"""Phase 1 — Analyze: the gm/Id pipeline's structural view.

Extends the shared :class:`~circuitgenome.sizer.physics.circuit_view.CircuitView`
with the two things only this pipeline needs: the typed block decomposition
(load kind, stage count, fully-differential flag) and the cascode device refs.
All of it is read-only from here on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from circuitgenome.recognizer.models import FunctionalBlockRecognitionResult
from circuitgenome.synthesizer.models import TopologyTemplate

from ..physics.circuit_view import CircuitView
from ..physics.circuit_view import analyze_circuit as _analyze_structure
from .blocks import OpAmpBlocks, build_blocks, cascode_device_refs


@dataclass
class GmIdCircuitView(CircuitView):
    """A :class:`CircuitView` plus the gm/Id pipeline's block decomposition.

    :param blocks: the typed :class:`~.blocks.OpAmpBlocks` decomposition.
    :param cascode_refs: refs of stacked (cascode) current-source devices.
    """
    blocks: OpAmpBlocks = field(default_factory=OpAmpBlocks)
    cascode_refs: set[str] = field(default_factory=set)


def analyze_circuit(
    fbr_result: FunctionalBlockRecognitionResult, topology: TopologyTemplate
) -> GmIdCircuitView:
    """Build the :class:`GmIdCircuitView` from the FBR assignments."""
    view = _analyze_structure(fbr_result, topology)
    return GmIdCircuitView(
        slot_transistors=view.slot_transistors,
        slot_resistors=view.slot_resistors,
        all_transistors=view.all_transistors,
        warnings=view.warnings,
        adopted=view.adopted,
        blocks=build_blocks(view.slot_transistors, view.slot_resistors),
        cascode_refs=cascode_device_refs(view.slot_transistors),
    )
