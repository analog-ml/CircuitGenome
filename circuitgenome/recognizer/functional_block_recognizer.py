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
  (outer) and :attr:`~.models.RecognizedStructure.category` (inner) using a
  three-pass algorithm: (1) filter pass for single-category ``gain_stage_*``
  blocks — three classes of spurious candidates are dropped: (A) ``in`` pin on
  an external port (bias nmos re-matched as a gain stage); (B) ``bias`` pin on
  an external port (bias mirror pmos re-matched); (C) any nmos device whose
  source is not ``gnd!`` (cascode load devices); (2) multi-category
  ``gain_stage_*`` pass (``gain_stage_1``) — re-sorts ``input_pair``
  candidates by the count of distinct external ports among ``{in1, in2}`` (real
  differential pairs have both signal inputs on distinct external ports; bias
  mirrors and spurious stage-device pairs do not), then uses the top
  ``input_pair`` result to guide ``load`` and ``tail_current`` via signal-chain
  following (``load.in1/in2`` matched against ``input_pair.out1/out2``;
  ``tail_current.out`` matched against ``input_pair.tail``), with external-port
  exclusion filters applied to each category first; (3) split pass —
  single-category ``gain_stage_*`` blocks with more than one remaining candidate
  are split into consecutive ``gain_stage_N`` groups ordered by ascending
  external-port adjacency, enabling disambiguation of three-stage opamp stages
  without a topology.
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

    **Two-pass algorithm**:

    1. *Filter pass* — for every ``gain_stage_*`` candidate that has an ``in``
       pin, check whether that pin's net is an external port. Real gain stages
       receive their input from an internal intermediate net (the previous
       stage's output); spurious matches arise when an input-pair nmos or a
       bias device is re-matched by a gain-stage pattern, putting ``in1``/
       ``in2`` or ``ibias`` on the ``in`` pin. Removing those candidates
       eliminates the majority of SR noise before the split pass.

    2. *Split pass* — ``gain_stage_*`` blocks that have exactly **one**
       remaining category and more than one remaining candidate are split into
       consecutive ``gain_stage_N`` groups ordered by ascending
       :func:`_external_port_score`. The instance whose ``out`` pin connects to
       the external output port scores highest and is promoted to the
       highest-numbered stage. Blocks with multiple categories (e.g.
       ``gain_stage_1`` which holds ``input_pair``, ``load``, and
       ``tail_current``) are never split; their candidates are simply sorted
       descending and the top-1 is the best topology-free guess.

    :param sr_result: Layer 1 output from
                       :func:`~.subcircuit_recognizer.recognize`.
    :param netlist: The parsed netlist, used to retrieve external port names
                     for :func:`_external_port_score`.
    :returns: :class:`~.models.CategoryGroupResult` with grouped, ranked
              candidates and ``unrecognized_devices`` passed through unchanged.
    """
    ext = set(netlist.external_ports)

    # --- Initial grouping, sorted descending by external-port score ---
    groups: dict[str, dict[str, list[RecognizedStructure]]] = {}
    for s in sr_result.structures:
        if s.circuit_block is None or s.category is None:
            continue
        groups.setdefault(s.circuit_block, {}).setdefault(s.category, []).append(s)
    for cb in groups:
        for cat in groups[cb]:
            groups[cb][cat].sort(key=lambda s: _external_port_score(s, ext), reverse=True)

    # --- Filter pass: drop spurious gain_stage_* candidates ---
    # Three classes of spurious gain-stage matches are eliminated:
    #
    # Class A — input-pair nmos / bias-reference nmos re-matched as gain stage:
    #   'in' pin connects to an external port (in1, in2, ibias).
    # Class B — pmos leg of magic_battery_bias re-matched as gain stage:
    #   'bias' pin connects to an external port (ibias).
    # Class C — cascode load devices re-matched as gain stage (e.g. nmos cascode
    #   current source paired with diode-connected pmos cascode reference, drains
    #   meeting at the cascode output node):
    #   applied only to single-category gain_stage_* blocks (second_stage slots)
    #   to avoid incorrectly filtering input-pair transistors (whose nmos source
    #   connects to net_tail, not gnd!).
    #   Any nmos device whose source terminal is not 'gnd!' indicates a cascode
    #   intermediate device, not a rail-to-rail gain stage.
    _GND = "gnd!"
    for cb in list(groups.keys()):
        if not cb.startswith("gain_stage_"):
            continue
        single_cat = len(groups[cb]) == 1
        for cat in groups[cb]:
            filtered = []
            for s in groups[cb][cat]:
                # Class A & B: pin-level external-port check
                if s.pins.get("in") in ext or s.pins.get("bias") in ext:
                    continue
                # Class C: nmos source-terminal check (single-category blocks only)
                if single_cat and any(
                    d.type == "nmos" and d.terminals.get("s") != _GND
                    for d in s.devices
                ):
                    continue
                filtered.append(s)
            groups[cb][cat] = filtered
        groups[cb] = {cat: st for cat, st in groups[cb].items() if st}
    groups = {cb: cats for cb, cats in groups.items() if cats}

    # --- Multi-category gain_stage_* pass (gain_stage_1: input_pair, load, tail_current) ---
    # The global _external_port_score ranking is inverted for these categories because
    # bias-generation devices gate on ibias (external) and share supply rails, outscoring
    # the real functional devices. Apply category-specific corrections in dependency order:
    # input_pair first, then load and tail_current using the input_pair result for
    # signal-chain following.
    #
    # input_pair: re-sort by count of *distinct* external ports among {in1, in2}.
    #   Real differential pair: in1/in2 → two distinct signal ports (score 2).
    #   Bias mirror: in1=in2=ibias (score 1). Spurious stage-pmos pairs: in1/in2 on
    #   internal nets (score 0).
    # load: drop candidates where in1, in2, or bias1 connect to external ports (spurious
    #   bias-gen matches with ibias gate). Among survivors, prefer those whose in1/in2
    #   match the input_pair's out1/out2 (signal-chain connection).
    # tail_current: drop candidates where out is external (spurious match driving the
    #   circuit output instead of the internal tail node). Among survivors, prefer those
    #   whose out matches the input_pair's tail pin.
    for cb, categories in list(groups.items()):
        if not cb.startswith("gain_stage_") or len(categories) == 1:
            continue

        # Pass 1: input_pair
        if "input_pair" in categories:
            categories["input_pair"] = sorted(
                categories["input_pair"],
                key=lambda s: (
                    len(({s.pins.get("in1"), s.pins.get("in2")} - {None}) & ext),
                    _external_port_score(s, ext),
                ),
                reverse=True,
            )

        # Derive expected nets from the top input_pair candidate for chain following
        ip_top = categories["input_pair"][0] if categories.get("input_pair") else None
        ip_outputs: set[str] = (
            {ip_top.pins.get("out1"), ip_top.pins.get("out2")} - {None}
            if ip_top else set()
        )
        ip_tail: str | None = (
            ip_top.pins.get("tail")
            if ip_top and ip_top.pins.get("tail") not in ext else None
        )

        # Pass 2: load
        if "load" in categories:
            structs = [
                s for s in categories["load"]
                if s.pins.get("in1") not in ext
                and s.pins.get("in2") not in ext
                and s.pins.get("bias1") not in ext
            ]
            if ip_outputs:
                structs = sorted(
                    structs,
                    key=lambda s: (
                        sum(1 for p in ("in1", "in2") if s.pins.get(p) in ip_outputs),
                        _external_port_score(s, ext),
                    ),
                    reverse=True,
                )
            categories["load"] = structs

        # Pass 3: tail_current
        if "tail_current" in categories:
            structs = [
                s for s in categories["tail_current"] if s.pins.get("out") not in ext
            ]
            if ip_tail:
                structs = sorted(
                    structs,
                    key=lambda s: (
                        1 if s.pins.get("out") == ip_tail else 0,
                        _external_port_score(s, ext),
                    ),
                    reverse=True,
                )
            categories["tail_current"] = structs

        groups[cb] = {cat: st for cat, st in categories.items() if st}
    groups = {cb: cats for cb, cats in groups.items() if cats}

    # --- Split pass: split single-category gain_stage_* blocks with >1 candidate ---
    to_remove: list[tuple[str, str]] = []
    to_add: list[tuple[str, str, list[RecognizedStructure]]] = []
    for cb, categories in list(groups.items()):
        if not cb.startswith("gain_stage_") or len(categories) != 1:
            continue
        base = int(cb.split("_")[-1])
        (cat, structs) = next(iter(categories.items()))
        if len(structs) <= 1:
            continue
        structs.sort(key=lambda s: _external_port_score(s, ext))
        to_remove.append((cb, cat))
        for i, s in enumerate(structs):
            to_add.append((f"gain_stage_{base + i}", cat, [s]))

    for cb, cat in to_remove:
        del groups[cb][cat]
        if not groups[cb]:
            del groups[cb]
    for cb, cat, structs in to_add:
        groups.setdefault(cb, {})[cat] = structs

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
