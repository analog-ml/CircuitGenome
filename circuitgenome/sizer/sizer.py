"""
Initial Sizing module — Layer 3 of the CircuitGenome pipeline.

:func:`size_circuit` is the dispatcher: it routes a parsed netlist plus its
Layer-2 :class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`
and a performance spec to the right sizer and returns a
:class:`~.shared.models.SizingResult`.

* technologies with a gm/Id LUT → the block-based gm/Id pipeline
  (:func:`~.gmid.size_gmid`);
* the card-less ``generic`` technology → the Level-1 CP-SAT sizer
  (:func:`~.analytical.level1.size_level1`);
* a PTM/SPICE-model node **without** a LUT → ``UnsupportedTechError`` (the
  Level-1 square-law numbers are not valid for such nodes).
"""
from __future__ import annotations

from circuitgenome.recognizer.models import (
    FunctionalBlockRecognitionResult,
    ParsedNetlist,
    SubcircuitRecognitionResult,
)
from circuitgenome.synthesizer.models import TopologyTemplate

from .shared.models import SizingResult, SizingSpec, TechParams, UnsupportedTechError


def size_circuit(
    parsed: ParsedNetlist,
    sr_result: SubcircuitRecognitionResult,
    fbr_result: FunctionalBlockRecognitionResult,
    topology: TopologyTemplate,
    tech: TechParams,
    spec: SizingSpec,
    *,
    time_limit_s: float = 30.0,
) -> SizingResult:
    """Compute initial transistor W/L values satisfying ``spec``.

    :param parsed: Layer-0 parsed netlist.
    :param sr_result: Layer-1 subcircuit recognition result (unused directly
        but kept for API symmetry with the pipeline).
    :param fbr_result: Layer-2 FBR result from
        :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`.
        Must use **topology mode** (not group-by-category).
    :param topology: Topology template corresponding to ``fbr_result``.
    :param tech: Technology parameters (from :func:`~.shared.loader.load_tech`).
    :param spec: Performance specification.
    :param time_limit_s: CP-SAT solver time limit in seconds (Level-1 path only).
    :returns: :class:`~.shared.models.SizingResult` with per-transistor sizing,
        compensation cap, computed metrics, and safety margins.
    """
    # gm/Id technologies (LUT present) use the block-based pipeline.
    if getattr(tech, "gmid_lut", None):
        from .gmid import size_gmid
        return size_gmid(parsed, sr_result, fbr_result, topology, tech, spec)

    # A PTM/SPICE-model node without a gm/Id LUT must not silently fall through
    # to the Level-1 square-law sizer — those are the inaccurate numbers gm/Id
    # exists to avoid. Only the card-less generic tech uses the analytical path.
    if getattr(tech, "spice_model", None):
        raise UnsupportedTechError(
            f"Technology '{tech.name}' is a PTM/SPICE-model node without a gm/Id "
            f"LUT. The analytical (Level-1) sizer is not valid for PTM nodes — "
            f"characterize a gm/Id LUT first (see issue #73). Only the 'generic' "
            f"technology uses the analytical sizer.")

    from .analytical.level1 import size_level1
    return size_level1(parsed, sr_result, fbr_result, topology, tech, spec,
                       time_limit_s=time_limit_s)
