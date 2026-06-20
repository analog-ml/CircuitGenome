"""
Core data models for the subcircuit and functional block recognizers.

All structures are plain dataclasses — they carry no logic and can be freely
inspected or passed between pipeline stages. They mirror the 3-layer
pipeline described in the design doc
(``plans/design_doc/subcircuit_and_functional_block_recognizer.md``):

- :class:`ParsedNetlist` is the output of **Layer 0** (netlist parsing).
- :class:`SubcircuitRecognitionResult` (made up of
  :class:`RecognizedStructure` instances) is the output of **Layer 1**, the
  Subcircuit Recognizer (SR).
- :class:`FunctionalBlockRecognitionResult` (made up of
  :class:`SlotAssignment` instances) is the output of **Layer 2**, the
  Functional Block Recognizer (FBR).

:class:`PatternDevice`, :class:`PatternDef`, and :class:`HookMatch` describe
the SR pattern-library schema used by
:mod:`circuitgenome.recognizer.subcircuit_recognizer`.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from circuitgenome.synthesizer.models import Device


@dataclass
class ParsedNetlist:
    """A flat SPICE subcircuit, parsed into devices and net names.

    Output of :func:`~circuitgenome.recognizer.netlist_parser.parse` (Layer
    0) -- the structural inverse of
    :func:`~circuitgenome.synthesizer.netlist.to_flat_spice`.

    :param name: The ``.subckt`` name.
    :param external_ports: Ordered list of port names from the ``.subckt``
                            header line.
    :param devices: Every device line in the subcircuit, as
                     :class:`~circuitgenome.synthesizer.models.Device`
                     instances.
    :param internal_nets: Net names referenced by ``devices`` that are not
                           in ``external_ports``.
    """
    name: str
    external_ports: list[str]
    devices: list[Device]
    internal_nets: set[str]


@dataclass
class RecognizedStructure:
    """A single recognized structure -- one SR pattern match.

    Produced by :func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize`
    (Layer 1). :data:`SubcircuitRecognitionResult.structures` may contain
    multiple overlapping ``RecognizedStructure`` instances covering the same
    devices (see :class:`SubcircuitRecognitionResult`); FBR (Layer 2) is
    responsible for picking, per topology slot, the one whose ``pins`` line
    up with that slot's expected connectivity.

    :param name: The matching :class:`PatternDef`'s name, e.g.
                  ``"differential_pair_nmos"``. For composite patterns (see
                  :class:`PatternDef`) this is also the corresponding
                  ``opamp_modules.yaml`` variant name.
    :param category: The matching pattern's category, e.g. ``"input_pair"``
                      (one of the ``opamp_modules.yaml`` module categories).
                      ``None`` for level-0 primitives and structural
                      composites that have no FBR counterpart.
    :param index: 0-based instance number, distinguishing repeated matches
                   of the same pattern (e.g. a second
                   ``differential_pair_nmos`` candidate elsewhere in the
                   netlist).
    :param tech_type: ``"n"``, ``"p"``, or ``None`` -- the technology of the
                       pattern's :attr:`PatternDef.tech_type_from` template
                       device, or ``None`` if the pattern doesn't declare
                       one.
    :param pins: Maps pin names (from :attr:`PatternDef.pins`, plus any
                  ``extra_pins`` contributed by a :class:`HookMatch`) to the
                  actual net names they resolve to in this netlist.
    :param devices: The actual
                     :class:`~circuitgenome.synthesizer.models.Device`
                     instances matched by this structure (the base template
                     assignment, plus any ``extra_devices`` from a
                     :class:`HookMatch`).
    :param children: Sub-structures from multi-level composition. Empty for
                      level-0 primitives and MVP composite-only patterns;
                      populated for level-1+ patterns.
    """
    name: str
    category: str | None
    index: int
    tech_type: str | None
    pins: dict[str, str]
    devices: list[Device]
    children: list["RecognizedStructure"] = field(default_factory=list)


@dataclass
class SubcircuitRecognitionResult:
    """Output of Layer 1, the Subcircuit Recognizer (SR).

    :param structures: Every :class:`RecognizedStructure` matched by any
                        pattern in the library. **May contain multiple
                        overlapping candidates for the same device-set** --
                        e.g. two patterns with structurally identical
                        templates but different categories/names, or a
                        composite pattern's reference device that also
                        satisfies a smaller, unrelated pattern. SR reports
                        every candidate; it does not pick a winner. Layer 2
                        (FBR) resolves overlaps using topology context.
    :param unrecognized_devices: Devices matched by *no* pattern. For a
                                  netlist produced by
                                  :func:`~circuitgenome.synthesizer.netlist.to_flat_spice`
                                  from a known
                                  :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`,
                                  this should be empty -- a non-empty list
                                  indicates a pattern-library gap.
    """
    structures: list[RecognizedStructure]
    unrecognized_devices: list[Device] = field(default_factory=list)


@dataclass
class PatternDevice:
    """One template device within a :class:`PatternDef`.

    :param ref: Template-local reference used by
                 :attr:`PatternDef.same_net` and :attr:`PatternDef.pins` to
                 refer to this device, e.g. ``"m1"``. Scoped to the
                 enclosing pattern -- unrelated to the actual netlist's
                 device refs.
    :param type: The device type this template slot matches:
                  ``"nmos"``, ``"pmos"``, ``"resistor"``, or ``"capacitor"``.
                  Matches :attr:`~circuitgenome.synthesizer.models.Device.type`.
    """
    ref: str
    type: str


@dataclass
class ChildDef:
    """One required child in a multi-level composite pattern.

    :param pattern: Name of the expected child :class:`PatternDef`, e.g.
                     ``"diode_connected_nmos"``.
    :param devices: Template device refs (from the enclosing
                     :class:`PatternDef`) that together form this child.
                     E.g. ``["m_ref"]`` for a single-device child or
                     ``["m_dr", "m_dl"]`` for a two-device child.
    """
    pattern: str
    devices: list[str]


@dataclass
class PatternDef:
    """A single SR pattern definition, loaded from
    ``config/primitives.yaml``, ``config/structural_patterns.yaml``, or
    ``config/opamp_patterns.yaml``.

    A pattern is a small template graph: a handful of typed
    :class:`PatternDevice` slots, ``same_net`` equality constraints between
    their terminals, and a ``pins`` map exporting named nets. See
    :func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize` for the
    matching algorithm.

    **Level-0 primitive patterns** describe single-device topological facts
    (e.g. ``diode_connected_nmos``). Exclusive primitives claim a device
    for exactly one pattern (higher :attr:`priority` wins).

    **Level-1+ composite patterns** declare :attr:`children` referencing
    lower-level patterns they compose; the SR verifies those children are
    present before accepting a match.

    **MVP composite patterns** (no ``children``, not ``exclusive``) correspond
    1:1 to an ``opamp_modules.yaml`` module variant and run in the same
    single-pass loop as before.

    :param name: Unique pattern name, e.g. ``"differential_pair_nmos"``. For
                  composite patterns, also the ``opamp_modules.yaml`` variant
                  name -- this is the value that ends up in
                  :attr:`RecognizedStructure.name` and
                  :attr:`SlotAssignment.pattern_name`.
    :param category: The ``opamp_modules.yaml`` module category this pattern
                      represents, e.g. ``"input_pair"``. ``None`` for
                      primitives and structural composites without an FBR
                      counterpart. Copied into
                      :attr:`RecognizedStructure.category`.
    :param devices: The pattern's template devices. The matcher searches for
                     an injective assignment from these to actual netlist
                     devices of matching :attr:`PatternDevice.type`.
    :param same_net: Equality constraints on resolved terminals, each written
                      as a list of ``"template_ref.terminal"`` strings, e.g.
                      ``[["m1.s", "m2.s"]]`` means "``m1``'s source and
                      ``m2``'s source must be the same net". These are the
                      *only* required equalities -- terminals not listed are
                      unconstrained, so a more-connected real circuit can
                      still match.
    :param pins: Maps an exported pin name to a ``"template_ref.terminal"``
                  string. Resolved through the matched assignment to produce
                  :attr:`RecognizedStructure.pins`.
    :param tech_type_from: The template device ref (e.g. ``"m1"``) whose
                            matched :attr:`~circuitgenome.synthesizer.models.Device.type`
                            (first character, ``"n"`` or ``"p"``) becomes
                            :attr:`RecognizedStructure.tech_type`. ``None``
                            if the pattern declares no dominant technology.
    :param hook: Optional ``"module:function"`` path to an extra-check
                  function (see :class:`HookMatch`), resolved dynamically by
                  :func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize`.
                  Most patterns have no hook.
    :param children: Required sub-structures for multi-level composition.
                      Empty for level-0 patterns and MVP composites. When
                      non-empty, ``recognize`` verifies each child is present
                      as a matched structure at the previous level before
                      accepting this pattern's match.
    :param exclusive: If ``True``, this pattern claims its matched device(s)
                       exclusively in Pass 0 -- each device is assigned to at
                       most one exclusive pattern. Only level-0 primitives
                       use this.
    :param priority: Tie-breaking priority among ``exclusive`` patterns that
                      match the same device; higher wins. E.g.
                      ``diode_connected_nmos`` (priority 10) beats ``nmos``
                      (priority 0).
    """
    name: str
    category: str | None
    devices: list[PatternDevice]
    same_net: list[list[str]]
    pins: dict[str, str]
    tech_type_from: str | None = None
    hook: str | None = None
    children: list[ChildDef] = field(default_factory=list)
    exclusive: bool = False
    priority: int = 0


@dataclass
class HookMatch:
    """Accept-and-extend result of a :attr:`PatternDef.hook` function.

    A hook is called once per base-template match
    (:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize`'s
    ``(assignment, pins, netlist)``) and returns either:

    - ``None`` -- reject this match entirely (it is dropped, as if the
      pattern hadn't matched).
    - a ``HookMatch`` -- accept the match, merging ``extra_devices`` into
      :attr:`RecognizedStructure.devices` and ``extra_pins`` into
      :attr:`RecognizedStructure.pins` (in addition to the base template's
      devices/pins).

    The motivating example is ``diode_connected_mosfet_bias``
    (:func:`~circuitgenome.recognizer.hooks.diode_connected_mosfet_bias_legs`):
    its base template is just the 1-device diode-connected bias reference,
    and the hook discovers however many output "legs" (1-7, one per needed
    bias rail) are actually present in the netlist and appends their devices
    and ``legN_out`` pins.

    :param extra_devices: Additional
                           :class:`~circuitgenome.synthesizer.models.Device`
                           instances to append to
                           :attr:`RecognizedStructure.devices`, beyond the
                           base template assignment.
    :param extra_pins: Additional ``pin name -> net name`` entries to merge
                        into :attr:`RecognizedStructure.pins`, beyond the
                        base template's :attr:`PatternDef.pins`.
    """
    extra_devices: list[Device] = field(default_factory=list)
    extra_pins: dict[str, str] = field(default_factory=dict)


@dataclass
class SlotAssignment:
    """One FBR decision: a recognized structure assigned to a topology slot.

    :param slot_name: The :attr:`~circuitgenome.synthesizer.models.Slot.name`
                       of the topology slot this assignment fills, e.g.
                       ``"input_pair"``.
    :param pattern_name: The assigned structure's
                          :attr:`RecognizedStructure.name`. For a correct
                          round trip, this equals the
                          :attr:`~circuitgenome.synthesizer.models.ModuleVariant.name`
                          of the variant that was synthesized into this slot.
    :param structure: The assigned :class:`RecognizedStructure` itself.
    """
    slot_name: str
    pattern_name: str
    structure: RecognizedStructure


@dataclass
class FunctionalBlockRecognitionResult:
    """Output of Layer 2, the Functional Block Recognizer (FBR).

    MVP taxonomy: the ``opamp_modules.yaml`` categories plus the matched
    :class:`~circuitgenome.synthesizer.models.TopologyTemplate` (design doc
    section 6.2). Produced by
    :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`.

    :param slot_assignments: Maps each
                              :attr:`~circuitgenome.synthesizer.models.Slot.name`
                              that received a candidate to its
                              :class:`SlotAssignment`. Shaped like
                              :attr:`~circuitgenome.synthesizer.models.SynthesizedCircuit.variant_map`
                              (``{slot_name: ...}``), so the two can be
                              compared directly in round-trip tests.
    :param unassigned_structures: SR candidates that were not assigned to
                                   any slot (e.g. overlapping/spurious
                                   pattern matches that lost to a
                                   better-connected candidate).
    :param unrecognized_devices: Passed through unchanged from
                                  :attr:`SubcircuitRecognitionResult.unrecognized_devices`.
    """
    slot_assignments: dict[str, SlotAssignment]
    unassigned_structures: list[RecognizedStructure] = field(default_factory=list)
    unrecognized_devices: list[Device] = field(default_factory=list)
