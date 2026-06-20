"""
Layer 2 — Functional Block Recognizer (FBR).

Two modes:

* **Topology mode** (:func:`assign_slots`): requires a
  :class:`~circuitgenome.synthesizer.models.TopologyTemplate`. Assigns each
  slot to its best-matching SR candidate using expected net connectivity
  (``_connectivity_score``). Handles repeated-category slots (e.g. two
  ``compensation`` slots in a fully-differential topology).

* **Topology-free mode** (:func:`group_by_category`): no topology needed.
  Groups SR structures by :attr:`~.models.RecognizedStructure.circuit_block`
  (outer) and :attr:`~.models.RecognizedStructure.category` (inner). Ranks
  candidates within each category by external-port adjacency
  (``_external_port_score``). Cannot disambiguate repeated-category slots.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import TopologyTemplate

from .models import (
    CategoryGroupResult,
    FunctionalBlockRecognitionResult,
    ParsedNetlist,
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


def _external_port_score(structure: RecognizedStructure, external_ports: set[str]) -> int:
    """Count how many of ``structure``'s pins connect directly to an external port.

    Used by :func:`group_by_category` to rank candidates within a category
    when no topology template is available. Structures whose pins touch more
    external ports (e.g. the input pair's ``in1``/``in2`` pins hit the
    subcircuit's ``in1``/``in2`` ports directly) rank higher than internal
    structures (e.g. a bias mirror that only touches internal nets).

    :param structure: Candidate :class:`~.models.RecognizedStructure`.
    :param external_ports: The subcircuit's external port names, from
                            :attr:`~.models.ParsedNetlist.external_ports`.
    :returns: Number of pins whose net name is in ``external_ports``.
    """
    return sum(1 for net in structure.pins.values() if net in external_ports)


def group_by_category(
    sr_result: SubcircuitRecognitionResult,
    netlist: ParsedNetlist,
) -> CategoryGroupResult:
    """Group SR structures by circuit block and category without a topology.

    Produces a :class:`~.models.CategoryGroupResult` whose ``groups`` dict is
    keyed first by :attr:`~.models.RecognizedStructure.circuit_block` (e.g.
    ``"gain_stage_1"``, ``"gain_stage_2"``, ``"bias"``) then by
    :attr:`~.models.RecognizedStructure.category` (e.g. ``"input_pair"``,
    ``"load"``). Structures with ``circuit_block=None`` (primitives, structural
    composites) are excluded.

    Within each category list, candidates are sorted by descending
    :func:`_external_port_score` — structures whose pins directly touch the
    subcircuit's external ports are ranked first, giving a topology-free best
    guess at the "true" instance when multiple candidates exist.

    For circuits where each ``circuit_block``/``category`` pair has exactly one
    candidate, the output is structurally equivalent to :func:`assign_slots`
    (one winner per category). For repeated-category cases (e.g. two
    ``compensation`` slots), all candidates are returned in the list and the
    caller must disambiguate using domain knowledge or topology.

    :param sr_result: Layer 1 output from
                       :func:`~.subcircuit_recognizer.recognize`.
    :param netlist: The parsed netlist, used to retrieve external port names
                     for :func:`_external_port_score`.
    :returns: :class:`~.models.CategoryGroupResult` with grouped, ranked
              candidates and ``unrecognized_devices`` passed through unchanged.
    """
    ext = set(netlist.external_ports)
    groups: dict[str, dict[str, list[RecognizedStructure]]] = {}
    for s in sr_result.structures:
        if s.circuit_block is None or s.category is None:
            continue
        groups.setdefault(s.circuit_block, {}).setdefault(s.category, []).append(s)
    for cb in groups:
        for cat in groups[cb]:
            groups[cb][cat].sort(key=lambda s: _external_port_score(s, ext), reverse=True)
    return CategoryGroupResult(
        groups=groups,
        unrecognized_devices=sr_result.unrecognized_devices,
    )


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
