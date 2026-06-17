"""
Layer 2 — Functional Block Recognizer (FBR).

MVP taxonomy: the ``opamp_modules.yaml`` categories (``input_pair``,
``load``, ``tail_current``, ``bias_generation``, ``cmfb``, ``compensation``,
``second_stage``) plus the matched
:class:`~circuitgenome.synthesizer.models.TopologyTemplate` (design doc
section 6.2). :func:`assign_slots` consumes a
:class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult` (Layer
1's output) and the topology that the netlist is known to have been
synthesized from, and produces a
:class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`
shaped like :attr:`~circuitgenome.synthesizer.models.SynthesizedCircuit.variant_map`,
so the two can be compared directly in round-trip tests.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import TopologyTemplate

from .models import (
    FunctionalBlockRecognitionResult,
    RecognizedStructure,
    SlotAssignment,
    SubcircuitRecognitionResult,
)


def _connectivity_score(structure: RecognizedStructure, slot_connections: dict[str, str]) -> int:
    """Count how many of ``structure``'s pins agree with a slot's expected wiring.

    :param structure: A candidate
                       :class:`~circuitgenome.recognizer.models.RecognizedStructure`,
                       whose :attr:`~circuitgenome.recognizer.models.RecognizedStructure.pins`
                       maps pin names to the actual net names they resolve to
                       in the recognized netlist.
    :param slot_connections: A topology slot's expected wiring, as returned by
                              :meth:`~circuitgenome.synthesizer.models.TopologyTemplate.slot_connections`
                              (``port name -> expected global net name``).
    :returns: The number of pin names present in both ``structure.pins`` and
              ``slot_connections`` whose net names also match -- i.e. how well
              ``structure`` fits in this slot. ``0`` if no pin agrees (or the
              two dicts share no keys).
    """
    return sum(1 for pin, net in structure.pins.items() if slot_connections.get(pin) == net)


def assign_slots(
    sr_result: SubcircuitRecognitionResult,
    topology: TopologyTemplate,
) -> FunctionalBlockRecognitionResult:
    """Assign each :class:`~circuitgenome.synthesizer.models.Slot` in
    ``topology.slots`` to its best-matching SR candidate.

    For each slot, candidates are filtered by
    :attr:`~circuitgenome.recognizer.models.RecognizedStructure.category` ==
    :attr:`~circuitgenome.synthesizer.models.Slot.category`, then ranked by
    :func:`_connectivity_score` against
    :meth:`~circuitgenome.synthesizer.models.TopologyTemplate.slot_connections`
    for that slot. The highest-scoring candidate is assigned (design doc
    section 6.3, steps 2-3). A slot with no candidates of its category is
    silently omitted from
    :attr:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult.slot_assignments`.

    Connectivity scoring is applied even when a category has only one slot:
    SR's patterns can structurally overlap (e.g. ``current_mirror_tail_nmos``
    also matches the bias-generation reference's diode-connected pair), so a
    category can have multiple candidates regardless of how many slots need
    it. Once a candidate is assigned to a slot its id is added to
    ``assigned_ids`` so it cannot be double-assigned to a second slot of the
    same category (e.g. ``comp_p``/``comp_n`` in
    ``two_stage_opamp_fully_differential``).

    :param sr_result: Layer 1's output -- every candidate
                       :class:`~circuitgenome.recognizer.models.RecognizedStructure`,
                       possibly with multiple overlapping candidates per
                       category (see
                       :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`).
    :param topology: The :class:`~circuitgenome.synthesizer.models.TopologyTemplate`
                      that the recognized netlist is known to have been
                      synthesized from -- supplies both the slots to fill
                      (:attr:`~circuitgenome.synthesizer.models.TopologyTemplate.slots`)
                      and each slot's expected wiring
                      (:meth:`~circuitgenome.synthesizer.models.TopologyTemplate.slot_connections`).
    :returns: A :class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`
              with one :class:`~circuitgenome.recognizer.models.SlotAssignment`
              per filled slot
              (:attr:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult.slot_assignments`),
              every unassigned candidate structure in
              ``unassigned_structures``, and ``unrecognized_devices`` passed
              through unchanged from ``sr_result``.
    """
    slot_assignments: dict[str, SlotAssignment] = {}
    assigned_ids: set[int] = set()

    for slot in topology.slots:
        candidates = [s for s in sr_result.structures
                      if s.category == slot.category and id(s) not in assigned_ids]
        if not candidates:
            continue
        slot_connections = topology.slot_connections(slot.name)
        best = max(candidates, key=lambda s: _connectivity_score(s, slot_connections))
        slot_assignments[slot.name] = SlotAssignment(
            slot_name=slot.name, pattern_name=best.name, structure=best,
        )
        assigned_ids.add(id(best))

    unassigned = [s for s in sr_result.structures if id(s) not in assigned_ids]
    return FunctionalBlockRecognitionResult(
        slot_assignments=slot_assignments,
        unassigned_structures=unassigned,
        unrecognized_devices=sr_result.unrecognized_devices,
    )
