Overview
========

CircuitGenome is structured around several modules, each addressing a different
direction of the analog circuit design problem:

- :ref:`Topology Synthesizer (SYN) <overview-synthesizer>` — constructs op-amp
  circuits from modular building blocks and emits SPICE netlists.
- :ref:`Subcircuit Recognizer (SR) <overview-recognizer>` — identifies
  structural subcircuits (differential pairs, cascode mirrors, etc.) in a flat
  SPICE netlist.
- :ref:`Functional Block Recognizer (FBR) <overview-recognizer>` — identifies
  the functional role of each part of a flat SPICE netlist (input stage, load,
  bias generation, etc.).
- :ref:`Sizer (SZ) <overview-sizer>` — computes transistor W/L values that
  satisfy DC performance specifications (gain, GBW, phase margin, slew rate,
  CMRR), via either an analytical CP-SAT solver or a gm/Id lookup pipeline.
- :doc:`Designer <modules/designer>` — chains synthesis, sizing, and
  verification end to end to return designs that meet a target spec.
- :doc:`Visualizer <modules/visualizer>` — an interactive Streamlit UI for
  browsing topologies and module variants as block diagrams.

.. _overview-synthesizer:

Topology Synthesizer
--------------------

The synthesizer models an op-amp as a composition of **module slots**.  Each
slot is filled by one **module variant** — a concrete circuit implementation
of a functional category.  The synthesizer iterates over all valid
combinations and wires them together according to a **topology template**.

For the full component catalogue with every variant, the enumeration and
compatibility analysis, the demand-driven bias construction, the modular
interface contract, and the SPICE output formats, see the
:doc:`Topology Synthesizer module page <modules/synthesizer>`.

Main components
~~~~~~~~~~~~~~~

An op-amp is assembled from these functional categories:

- **Input pair** — the differential input stage (PMOS/NMOS, optional source
  degeneration).
- **Load** — the first-stage load (resistor, active current-mirror,
  current-source, folded / telescopic cascode).
- **Tail current** — the input pair's bias current source (current mirror,
  cascode current mirror, resistor).
- **Bias generation** — constructed per combination from what the other slots
  consume on each bias rail (not enumerated).
- **CMFB** — common-mode feedback, present only for fully-differential loads.
- **Compensation** — Miller capacitor, Miller cap with nulling resistor, or
  indirect compensation.
- **Amplification stage** — the second / third gain stages (common-source or
  non-inverting current-mirror).
- **Output stage** — a source-follower buffer, used in the ``*_buffered_*``
  templates.

.. rubric:: Input pair

.. figure:: ../gallery/modules-implementations/input_pair+load+tail_current/input_pair.svg
   :alt: Input pair variants
   :width: 100%

.. rubric:: Load

.. figure:: ../gallery/modules-implementations/input_pair+load+tail_current/load.svg
   :alt: Load variants
   :width: 100%

.. rubric:: Tail current

.. figure:: ../gallery/modules-implementations/input_pair+load+tail_current/tail_current.svg
   :alt: Tail current variants
   :width: 100%

.. rubric:: CMFB

.. figure:: ../gallery/modules-implementations/bias_generation+cmfb/cmfb.svg
   :alt: CMFB variants
   :width: 100%

Topology templates
~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 10 20 30

   * - Template name
     - Stages
     - Output type
     - Compensation
   * - ``one_stage_opamp``
     - 1
     - Single-ended
     - —
   * - ``two_stage_opamp_single_ended``
     - 2
     - Single-ended
     - —
   * - ``two_stage_opamp_fully_differential``
     - 2
     - Fully differential
     - —
   * - ``two_stage_opamp_buffered_single_ended``
     - 2
     - Single-ended
     - — (+ follower ``output_stage``)
   * - ``two_stage_opamp_buffered_fully_differential``
     - 2
     - Fully differential
     - — (+ follower ``output_stage``)
   * - ``three_stage_opamp_nmc_single_ended``
     - 3
     - Single-ended
     - Nested Miller (NMC)
   * - ``three_stage_opamp_rnmc_single_ended``
     - 3
     - Single-ended
     - Reversed Nested Miller (RNMC)
   * - ``three_stage_opamp_nmc_fully_differential``
     - 3
     - Fully differential
     - Nested Miller (NMC)
   * - ``three_stage_opamp_rnmc_fully_differential``
     - 3
     - Fully differential
     - Reversed Nested Miller (RNMC)
   * - ``three_stage_opamp_nmc_buffered_single_ended``
     - 3
     - Single-ended
     - Nested Miller (NMC) (+ follower ``output_stage``)
   * - ``three_stage_opamp_rnmc_buffered_single_ended``
     - 3
     - Single-ended
     - Reversed Nested Miller (RNMC) (+ follower ``output_stage``)
   * - ``three_stage_opamp_nmc_buffered_fully_differential``
     - 3
     - Fully differential
     - Nested Miller (NMC) (+ follower ``output_stage``)
   * - ``three_stage_opamp_rnmc_buffered_fully_differential``
     - 3
     - Fully differential
     - Reversed Nested Miller (RNMC) (+ follower ``output_stage``)

.. rubric:: Circuits generated per template

The table below is a snapshot of ``enumerate_circuits`` run on each template
with the **default** configuration (``unsupported`` and ``bias_infeasible``
variants excluded).  It is generated by ``tools/gen_topology_counts.py`` and
refreshed whenever the enumeration changes.  For how each count arises — the
polarity / output-cardinality compatibility filtering, the parked-variant
tags, and the per-template derivation — see the :doc:`Topology Synthesizer
module page <modules/synthesizer>`.

.. include:: topology_counts.rst

Using it
~~~~~~~~

Generate circuits from the command line with ``circuitgenome synthesize`` or
from Python with :func:`~circuitgenome.synthesizer.synthesizer.synthesize` and
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.  See
:doc:`usage/cli` and :doc:`usage/python_api` for worked examples.


.. _overview-recognizer:

Subcircuit & Functional Block Recognizer
-----------------------------------------

The recognizer (:mod:`circuitgenome.recognizer`) is the structural inverse of
the synthesizer: given a flat SPICE netlist produced by
:func:`~circuitgenome.synthesizer.netlist.to_flat_spice`, it recovers the
:attr:`~circuitgenome.synthesizer.models.SynthesizedCircuit.variant_map` that
produced it. It is organized as a 3-layer pipeline:

1. **Layer 0 -- netlist parsing**
   (:func:`~circuitgenome.recognizer.netlist_parser.parse`) turns the flat
   SPICE text back into a
   :class:`~circuitgenome.recognizer.models.ParsedNetlist` -- a list of
   :class:`~circuitgenome.synthesizer.models.Device` plus the external port
   and internal net names.
2. **Layer 1 -- Subcircuit Recognizer (SR)**
   (:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize`) matches
   a library of structural patterns (differential pairs, current mirrors,
   ...) against the parsed devices, producing a
   :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`.
3. **Layer 2 -- Functional Block Recognizer (FBR)**
   (:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`
   or :func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`)
   assigns each recognized structure to a functional role. With a topology
   template, structures are assigned to named slots (``input_pair``, ``load``,
   ...) recovering the ``variant_map`` shape. Without one, structures are
   grouped by ``circuit_block`` (``gain_stage_1``, ``gain_stage_2``, ``bias``,
   ``compensation``, ``cmfb``) and ``category`` for topology-free recognition.

The recognizer supports round-trip recognition of all seven topology templates
synthesized by :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`,
verified across 73 test combinations covering every pattern variant.

Netlist parsing (Layer 0)
~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.recognizer.netlist_parser.parse` is the structural
inverse of :func:`~circuitgenome.synthesizer.netlist.to_flat_spice`: it reads
a ``.subckt <name> <port...>`` / ``.ends`` block with one MOSFET device line
per device,

.. code-block:: text

   {ref} {d} {g} {s} {b} {nmos|pmos}

and produces a :class:`~circuitgenome.recognizer.models.ParsedNetlist`. Net
and ref names are treated as arbitrary strings -- the parser makes no
assumptions about ``to_flat_spice``'s own naming conventions. Resistor lines
(``r<ref> <t1> <t2> <value>``) and capacitor lines (``c<ref> <p> <m>
<value>``) are also handled; the leading character of ``ref`` determines the
device type (``r`` → ``resistor``, ``c`` → ``capacitor``).

Subcircuit recognition (Layer 1)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The SR pattern library
(``circuitgenome/recognizer/config/subcircuit_patterns.yaml``, loaded by
:func:`~circuitgenome.recognizer.subcircuit_recognizer.load_patterns`) is a
list of small template graphs. Each pattern declares:

- ``devices`` -- typed template slots (``nmos``/``pmos``), e.g. ``m1``, ``m2``.
- ``same_net`` -- terminal-equality constraints between slots, e.g.
  ``[m1.s, m2.s]`` ("``m1``'s source and ``m2``'s source must be the same
  net"); unlisted terminals are unconstrained.
- ``pins`` -- named nets exported by the pattern, e.g. ``in1: m1.g``.
- ``tech_type_from`` -- which template device's matched type (``"n"``/``"p"``)
  becomes the recognized structure's ``tech_type``.
- an optional ``hook`` -- a ``"module:function"`` extra-check for constraints
  too awkward to express declaratively.

Composite patterns correspond 1:1 to an ``opamp_modules.yaml`` module variant
and reuse its name, so a successful match's
:attr:`~circuitgenome.recognizer.models.RecognizedStructure.name` is directly
comparable to a
:attr:`~circuitgenome.synthesizer.models.SynthesizedCircuit.variant_map`
entry's variant name. The library covers every reachable ``one_stage_opamp``
and ``two_stage_opamp_single_ended`` variant -- 36 patterns across seven
categories:

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Category
     - Patterns (count)
     - Notes
   * - ``input_pair``
     - 5
     - ``differential_pair_{nmos,pmos}``, degenerated variants (NMOS+NMOS /
       PMOS+PMOS transistors + 2 source-degeneration resistors),
       ``inverter_based_input`` (2 CMOS inverters: 2 PMOS + 2 NMOS).
   * - ``load``
     - 14
     - Resistor (VDD-side / GND-side), active current mirror (PMOS / NMOS),
       current-source (PMOS / NMOS), single-output folded cascode (NMOS-input /
       PMOS-input, 8 devices each), telescopic cascode (PMOS / NMOS, 6 devices
       each) in self-biased and wide-swing/Sooch flavours (the latter drive
       the mirror cascode gates from a ``bias2`` level rail, dropping the
       output floor from ``Vgs+Vdsat`` to ``2*Vdsat`` -- issue #129). Plus 2
       differential-output folded-cascode variants
       (``folded_cascode_load_{nmos,pmos}_input_differential_output``, 8 devices
       each) used exclusively by ``two_stage_opamp_fully_differential``.
   * - ``tail_current``
     - 6
     - Current mirror (PMOS / NMOS, 2 devices each), cascode current mirror
       (PMOS / NMOS, 4 devices each), resistor (VDD-side / GND-side, each using
       a hook to reject resistors whose supply terminal isn't the global rail).
   * - ``bias_generation``
     - 4
     - ``constructed_bias`` (the synthesizer's constructed multi-reference
       generator; its hook discovers NMOS-referenced legs, the ``pref``
       branch, and every PMOS-referenced leg), plus the historical
       ``diode_connected_mosfet_bias`` (NMOS reference + NMOS/PMOS leg
       pairs), ``magic_battery_bias`` (PMOS reference + PMOS/NMOS leg
       pairs), and ``resistor_bias`` (PMOS reference + PMOS/resistor leg
       pairs) for external/legacy netlists. All four use hooks (below) to
       discover however many output legs are present.
   * - ``cmfb``
     - 2
     - ``resistive_sense_cmfb`` (2 resistors + 5T OTA: resistive averager feeds
       a differential pair whose output mirrors onto ``out``),
       ``dda_cmfb`` (differential-difference amplifier: 4 NMOS + 2 PMOS + 2 NMOS
       tails, two input pairs sharing a diode-connected PMOS mirror). Both use
       ``{in1, in2, vref, bias, out}`` pins. Present only when ``load`` has
       ``output_cardinality: "differential"``; otherwise pruned to ``cmfb_absent``.
   * - ``compensation``
     - 3
     - ``miller_cap`` (1 capacitor across ``in``→``out``),
       ``miller_cap_with_nulling_resistor`` (series resistor + capacitor, sharing
       an internal ``cn`` node), ``indirect_compensation`` (capacitor to an
       internal ``ind`` node + series resistor to ``out``). Connectivity scoring
       naturally disambiguates overlapping 1-device subsets without hooks.
   * - ``second_stage``
     - 7
     - ``common_source`` (NMOS input + PMOS load, drains shorted to ``out``),
       ``common_source_pmos`` (the mirror image, PMOS input + NMOS sink;
       structurally identical to ``common_source`` with the ``in``/``bias``
       gate roles swapped, so both patterns match the same device pair and
       FBR's connectivity scoring picks the one whose ``in`` pin lands on
       the stage-input net), ``common_drain`` (PMOS source follower + PMOS
       current source; the follower's source/bulk tie and the source's
       bulk-on-vdd keep it disjoint from the CS and OTA shapes),
       ``common_drain_nmos`` (NMOS source follower + NMOS sink; the
       follower's source is the sink's drain, all bulks on gnd),
       ``differential_ota_second_stage`` (2 PMOS + 2 NMOS, cross-coupled via
       an internal ``d1`` node; parked as ``unsupported`` for synthesis,
       issue #114 -- the pattern still serves external netlists),
       ``noninverting_stage_nmos``/``noninverting_stage_pmos`` (2 PMOS + 2
       NMOS non-inverting current-mirror gain stages, issue #139; the
       diode-connected mirror master on the internal mirror node makes each
       non-isomorphic to the OTA shape).

:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize` matches
every pattern against the netlist's devices via a small backtracking search
(patterns are 1-4 devices, so no graph library is needed), filtering
candidates by device type and checking ``same_net``. A pattern's ``hook``, if
any, runs once per base-template match and may reject the match (return
``None``) or accept it with extra devices/pins merged in (a
:class:`~circuitgenome.recognizer.models.HookMatch`).

Six hooks are implemented in :mod:`circuitgenome.recognizer.hooks`:

- :func:`~circuitgenome.recognizer.hooks.constructed_bias_legs` discovers
  the constructed generator's legs (NMOS-referenced pairs, the ``pref``
  branch, gnd-referenced / current / resistor legs off the PMOS-side
  reference), rejecting purely NMOS-referenced shapes so they resolve to
  the historical pattern below.
- :func:`~circuitgenome.recognizer.hooks.diode_connected_mosfet_bias_legs`,
  :func:`~circuitgenome.recognizer.hooks.magic_battery_bias_legs`, and
  :func:`~circuitgenome.recognizer.hooks.resistor_bias_legs` each handle a
  historical single-flavor generator: the shared reference device is always
  present, but the number of output "legs" (0-7) varies per netlist. The
  base template matches only the reference device; the hook walks the
  netlist to find however many legs are actually present and appends their
  devices and ``legN_out`` pins to
  :class:`~circuitgenome.recognizer.models.HookMatch`.
- :func:`~circuitgenome.recognizer.hooks.resistor_tail_vdd_check` and
  :func:`~circuitgenome.recognizer.hooks.resistor_tail_gnd_check` each accept a
  single-resistor ``tail_current`` match only if the resistor's supply-side
  terminal is the global ``vdd!``/``gnd!`` rail, preventing the unconstrained
  1-device template from spuriously matching every resistor in the netlist.

The result, a
:class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`, may
contain **multiple overlapping candidates** for the same device(s) -- SR does
not pick a winner. For example, ``current_mirror_tail_nmos`` and
``diode_connected_mosfet_bias`` share the same 2-terminal diode-connected
shape, so a single diode-connected NMOS may match the base template of both
patterns; disambiguation is FBR's job.
``unrecognized_devices`` lists any device matched by no pattern -- for a
netlist produced from a known ``SynthesizedCircuit`` with full pattern
coverage, this should be empty.

Functional block recognition (Layer 2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FBR operates in two modes depending on whether a topology template is available:

**Topology mode** (:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`):
takes SR's output plus a
:class:`~circuitgenome.synthesizer.models.TopologyTemplate` and assigns each
:class:`~circuitgenome.synthesizer.models.Slot` in ``topology.slots`` to its
best-matching SR candidate:

1. Filter SR's candidates to those whose ``category`` matches the slot's
   ``category``.
2. Score each remaining candidate by how many of its resolved ``pins`` agree
   with
   :meth:`~circuitgenome.synthesizer.models.TopologyTemplate.slot_connections`
   for that slot (the topology's static ``{port: expected global net}``
   wiring).
3. Assign the highest-scoring candidate.

Connectivity scoring runs even for categories with only one slot, since SR may
report multiple overlapping candidates per category (as above) regardless of
how many slots need that category. The output,
:class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`, is
shaped like ``variant_map`` (``{slot_name: SlotAssignment}``), plus any
unassigned candidate structures and ``unrecognized_devices`` passed through
from SR.

**Topology-free mode** (:func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`):
works on any netlist with arbitrary net names without a topology template.
Each opamp pattern carries a ``circuit_block`` annotation (``gain_stage_1``,
``gain_stage_2``, ``bias``, ``compensation``, ``cmfb``) alongside its
``category`` (``input_pair``, ``load``, ...). The ``gain_stage_N`` prefix is
distinct from category names like ``second_stage``, so the two fields never
clash. The function groups SR structures by ``circuit_block`` then
``category``, ranking candidates within each category by external-port
adjacency (count of pins that connect directly to a subcircuit external port)
as a topology-free disambiguation signal. The output,
:class:`~circuitgenome.recognizer.models.CategoryGroupResult`, gives a
``circuit_block → category → [candidates]`` mapping where the first candidate
per category is the best topology-free guess.

The topology-free algorithm runs three passes:

**Pass 1 — Filter (single-category gain_stage_* blocks only)**

Removes three classes of spurious candidates in ``gain_stage_2``, ``gain_stage_3``,
etc. (blocks with exactly one category, i.e. ``second_stage`` slots):

- **Class A** — ``in`` pin on an external port: bias-reference nmos re-matched
  with gate on ``ibias``.
- **Class B** — ``bias`` pin on an external port: pmos leg of a bias mirror
  re-matched with gate on ``ibias``.
- **Class C** — any nmos device whose source is not ``gnd!``: cascode load
  devices (source tied to an intermediate folding node) that survive the
  pin-level checks.

**Pass 2 — Multi-category ranking (gain_stage_1)**

``gain_stage_1`` holds three categories simultaneously (``input_pair``,
``load``, ``tail_current``), and the simple external-port score heuristic is
inverted for all three: bias-generation devices score higher than the real
functional devices because they connect to ``ibias`` (external bias port) and
supply rails. Pass 2 corrects this in dependency order:

1. *input_pair* — re-sorted by the count of **distinct** external ports among
   ``{in1, in2}`` as the primary key. The real differential pair has both signal
   inputs on distinct external ports (score 2); bias mirror pairs have
   ``in1 = in2 = ibias`` (score 1); spurious second/third-stage device pairs have
   ``in1``, ``in2`` on internal nets (score 0).

2. *load* — candidates with ``in1``, ``in2``, or ``bias1`` on external ports are
   dropped (spurious bias-gen matches). Among survivors, those whose ``in1``/
   ``in2`` match the top ``input_pair`` candidate's ``out1``/``out2`` are
   promoted via **signal-chain following**. The real load always receives its
   differential inputs from the input pair's drain nodes.

3. *tail_current* — candidates whose ``out`` connects to an external port are
   dropped (spurious matches driving the circuit output instead of the internal
   tail node). Among survivors, those whose ``out`` matches the top
   ``input_pair`` candidate's ``tail`` pin are promoted via signal-chain
   following.

**Pass 3 — Split (single-category gain_stage_* blocks)**

``gain_stage_*`` blocks with exactly one remaining category and more than one
candidate are split into consecutive ``gain_stage_N`` groups ordered by ascending
external-port adjacency. This enables disambiguation of a three-stage opamp's
second and third gain stages: the intermediate stage (``out`` on an internal net)
stays in ``gain_stage_2``; the final stage (``out`` connecting to the external
output port) is promoted to ``gain_stage_3``.

SR pattern coverage
~~~~~~~~~~~~~~~~~~~~

The pattern library covers all 36 patterns spanning all seven topologies:

- **one_stage_opamp**: 24 patterns (5 ``input_pair`` × 10 ``load`` × 6 real
  ``tail_current`` × 3 ``bias_generation``). The round-trip test is
  parametrized over 11 representative combinations covering every variant.
- **two_stage_opamp_single_ended**: adds 8 new patterns (3 ``compensation`` +
  5 ``second_stage``). The round-trip test adds 11 further combinations
  covering all 5 ``second_stage`` variants against every stage-interface-
  compatible ``input_pair`` polarity, all 3 ``compensation`` variants, and
  all 5 ``input_pair`` variants.
- **two_stage_opamp_fully_differential**: adds 4 new patterns — 2
  differential-output ``load`` variants
  (``folded_cascode_load_{nmos,pmos}_input_differential_output``) and 2
  ``cmfb`` variants (``resistive_sense_cmfb``, ``dda_cmfb``). FBR's
  ``assign_slots`` was also fixed to exclude already-assigned candidates when
  processing same-category slot pairs (``comp_p``/``comp_n`` and
  ``second_stage_p``/``second_stage_n``). The round-trip test adds 11 further
  combinations covering both ``cmfb`` variants, all ``compensation`` and
  ``second_stage`` pairings, and both differential input-pair polarities.
- **three_stage_opamp_nmc_single_ended** and
  **three_stage_opamp_rnmc_single_ended**: the plain NMC scheme adds 2 new
  ``amplification_stage`` patterns — the non-inverting current-mirror stages
  ``noninverting_stage_{nmos,pmos}`` (issue #139) that fill the NMC gm2 slot
  — which the recognizer tells apart from the CS gm3 stage (and from
  ``differential_ota_second_stage``, whose mirror node differs) by graph
  structure. The ``third_stage`` slot otherwise reuses the
  ``amplification_stage`` category; ``comp1``/``comp2`` reuse the
  ``compensation`` category. FBR's ``assigned_ids`` mechanism correctly
  disambiguates 2 same-category gain slots and 2 same-category
  ``compensation`` slots via connectivity scoring on the distinct
  intermediate nets (``net_mid1``/``net_mid2``).
- **three_stage_opamp_nmc_fully_differential** and
  **three_stage_opamp_rnmc_fully_differential**: no new patterns needed.
  Each path (p/n) has independent ``second_stage``/``third_stage`` and
  ``comp1``/``comp2`` slots — 4 slots per category. FBR correctly assigns
  all 4 same-category ``compensation`` slots and all 4 same-category
  ``second_stage`` slots via connectivity scoring on per-path distinct nets
  (``net_loadout1``/``net_loadout2``, ``net_mid2_p``/``net_mid2_n``,
  ``outp``/``outn``). 11 round-trip combos each.

All 73 test combos assert ``unrecognized_devices == []`` and full
``variant_map`` recovery. Combos are chosen so every variant appears in at
least one and every selected combo is structurally unambiguous for the SR/FBR
pipeline. Known structural ambiguities -- ``resistor_bias`` paired with
``current_mirror_tail_{nmos,pmos}`` (the tail's diode-connected reference
transistor spuriously satisfies the ``magic_battery_bias`` NMOS leg template)
and any ``magic_battery_bias`` or ``resistor_bias`` combination where
bias-rail pruning reduces the ``bias_generation`` slot to 0 legs (making the
two variants structurally identical) -- are avoided by careful combo selection
rather than additional code. Primitive/multi-level pattern composition and topology identification from an
arbitrary netlist are deferred to later milestones.

.. _overview-sizer:

Sizer (SZ)
----------

The sizer takes the FBR result (slot assignments) plus a performance
specification and returns W/L values for every transistor in the circuit.  It
has two sizing paths — an **analytical** Level-1 CP-SAT solver and a **gm/Id**
lookup pipeline, selected by technology.  It
supports all seven op-amp topology templates — one-stage,
two-stage (single-ended and fully differential), and the four three-stage
NMC/RNMC variants (single-ended and fully differential):

- ``one_stage_opamp``
- ``two_stage_opamp_single_ended``
- ``two_stage_opamp_fully_differential``
- ``three_stage_opamp_nmc_single_ended``
- ``three_stage_opamp_rnmc_single_ended``
- ``three_stage_opamp_nmc_fully_differential``
- ``three_stage_opamp_rnmc_fully_differential``

Supported performance specs
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The sizer solves against these DC performance targets, each a bound the sized
circuit must satisfy:

- Open-loop DC gain (minimum)
- Gain–bandwidth product, GBW (minimum)
- Phase margin (minimum)
- Slew rate (minimum)
- CMRR (minimum)
- Quiescent power (maximum)
- Output swing (minimum / maximum)

Alongside these targets you also give the sizer an operating point — supply
rails, tail bias current, and load capacitance.  See the
:doc:`Sizer (SZ) module page <modules/sizer>` for the complete input
specification.

Using it
~~~~~~~~

Size a circuit from the command line with ``circuitgenome size`` or from Python
with :func:`~circuitgenome.sizer.sizer.size_circuit`.  See :doc:`usage/cli` and
:doc:`usage/python_api` for worked examples, and the
:doc:`Sizer (SZ) module page <modules/sizer>` for the sizing algorithm,
technology configurations, and SPICE verification.
