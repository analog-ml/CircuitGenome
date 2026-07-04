Overview
========

CircuitGenome is structured around four modules, each addressing a different
direction of the analog circuit design problem.

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Module
     - Status
     - Description
   * - Topology Synthesizer
     - Available
     - Constructs op-amp circuits from modular building blocks and emits
       SPICE netlists.
   * - Subcircuit Recognizer
     - Available
     - Identifies structural subcircuits (differential pairs, cascode
       mirrors, etc.) in a flat SPICE netlist.
   * - Functional Block Recognizer
     - Available
     - Identifies the functional role of each part of a flat SPICE netlist
       (input stage, load, bias generation, etc.).
   * - Initial Sizer
     - Available
     - Computes minimum transistor W/L values that satisfy DC performance
       specifications (gain, GBW, phase margin, slew rate, CMRR) using an
       OR-Tools CP-SAT integer-programming solver.

Topology Synthesizer
--------------------

The synthesizer models an op-amp as a composition of **module slots**.  Each
slot is filled by one **module variant** — a concrete circuit implementation
of a functional category.  The synthesizer iterates over all valid
combinations and wires them together according to a **topology template**.

Module categories
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Category
     - Variants
   * - Input pair
     - PMOS differential pair, NMOS differential pair, PMOS with source
       degeneration, NMOS with source degeneration, inverter-based
   * - Load
     - Resistor (VDD-side / GND-side), PMOS active (current mirror), NMOS
       active (current mirror), PMOS/NMOS current source, folded cascode
       (PMOS/NMOS-input, single-output & differential-output), telescopic
       cascode (PMOS/NMOS)
   * - Tail current
     - Current mirror (PMOS/NMOS), cascode current mirror (PMOS/NMOS),
       resistor (VDD-side / GND-side)
   * - Bias generation
     - No enumerated variants: constructed per combination from consumer
       demands (see "Demand-driven bias construction" below) -- an NMOS
       master reference on ``ibias`` plus one typed leg per consumed rail
       (rails 1-4 for ``load``, rail 5 for ``second_stage``, rail 6 for
       ``third_stage``, rail 7 for ``tail_current``)
   * - CMFB
     - Resistive-sense 5T OTA, differential-difference amplifier (DDA) --
       senses the load's first-stage differential outputs
       (``net_diff1``/``net_diff2``) against an external ``vcm_ref`` and
       drives the differential-output cascode load's ``bias_cmfb`` input.
       Present only when ``load``'s ``output_cardinality`` is
       ``"differential"``; otherwise pruned to an empty placeholder (see
       "CMFB compatibility filter" below) and ``vcm_ref`` is left
       unconnected.
   * - Compensation
     - Miller capacitor, Miller cap with nulling resistor, indirect
       compensation
   * - Second stage
     - Common-source (NMOS/PMOS), common-drain source follower (PMOS/NMOS),
       differential OTA

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

Of the 5 × 12 × 6 = 360 possible ``input_pair`` / ``load`` / ``tail_current``
combinations, only 144 have compatible PMOS/NMOS polarities (see "Polarity
compatibility filter" below) — the rest are filtered out by
``enumerate_circuits``. Of those 144, 72 use ``inverter_based_input``, whose
self-biased design never references its ``tail`` port: the "Tail-current
compatibility filter" below collapses those 72 combinations' 6
``tail_current`` choices down to 1 canonical choice (72 -> 12), leaving
**84** effective combinations (the 72 combinations using a
``differential_pair_*`` variant are unaffected). Of those 84, the
"Output-cardinality compatibility filter" below further splits them by which
output type the ``load`` supports: **70** are valid for single-ended
templates (excluding the 14 combinations using a differential-output cascode
load) and **56** are valid for fully-differential templates (excluding the 28
combinations using a single-output cascode or telescopic-cascode load).

The ``bias_generation`` slot contributes no enumeration factor at all: its
variant is *constructed* per combination from what the other slots consume
on each bias rail (see "Demand-driven bias construction" below), so every
core combination carries exactly one, structurally matched bias generator.

The 1-stage template therefore produces **70 distinct circuits**. In the
multi-stage templates, the ``second_stage`` slot that senses the first
stage's output keeps only the level-reachable ``second_stage`` variants
(the "Stage-interface compatibility filter" below): 3 of the 5 for the 30
PMOS-pair combinations (``common_source``,
``differential_ota_second_stage``, ``common_drain``), 2 of the 5 for the 30
NMOS-pair combinations (``common_source_pmos``, ``common_drain_nmos``),
and all 5 for the 10 ``inverter_based_input`` combinations. The 2-stage
single-ended template thus produces **600 circuits**
((30 × 3 + 30 × 2 + 10 × 5) × 3 ``compensation``). The 2-stage
fully-differential template, which has two ``compensation`` slots, two
``second_stage`` slots (one per output path, both sensing the first
stage), and one ``cmfb`` slot, produces **5 760 circuits**: of the 56
fully-differential-compatible ``input_pair``/``load``/``tail_current``
combinations, only the 14 using a ``"differential"``-cardinality load keep
both ``cmfb`` variants (14 × 2 = 28); the other 42 collapse ``cmfb`` to a
single canonical variant (42 × 1 = 42) -- 28 + 42 = 70 effective
load/``cmfb`` combinations (see "CMFB compatibility filter" below) -- (30
× 3² + 30 × 2² + 10 × 5²) × 9 ``compensation`` pairs = 5 760. Each 3-stage
single-ended template adds two more ``second_stage`` slots (gm2, gm3 --
only gm2 senses the first stage; gm3 keeps all 5 variants) and two
``compensation`` slots (Cm1, Cm2) on top of the 1-stage base, producing
**9 000 circuits** ((30 × 3 + 30 × 2 + 10 × 5) × 5 × 9 ``compensation``
pairs). Each 3-stage fully-differential template duplicates those four
slots per output path (and keeps the single ``cmfb`` slot), producing
**1 296 000 circuits** ((30 × 3² + 30 × 2² + 10 × 5²) × 5² × 3⁴).

Polarity compatibility filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A circuit only has a real DC current path if its ``input_pair``, ``load``,
and ``tail_current`` agree on polarity. For example, ``differential_pair_nmos``
draws current out of ``out1``/``out2`` into the tail, so it needs a ``load``
that *sources* current into ``out1``/``out2`` from vdd and a
``tail_current`` that *sinks* the tail node to gnd — pairing it with
``active_load_nmos`` (which also sinks to gnd) or ``current_mirror_tail_pmos``
(which also sources into the tail) leaves a node with no current path.

Each ``input_pair``, ``load``, and ``tail_current`` variant declares a
``polarity`` field in ``opamp_modules.yaml``: ``pmos_input``, ``nmos_input``,
or omitted for variants that work with either polarity
(``inverter_based_input``).
``enumerate_circuits`` skips any combination where ``load``'s or
``tail_current``'s ``polarity`` (if set) doesn't match ``input_pair``'s. To
extend the filter to a new or edited variant, add the matching ``polarity:``
tag in YAML — no code changes needed
(``circuitgenome/synthesizer/polarity_compatibility.py``).

Stage-interface compatibility filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``second_stage`` variant is structurally unbiasable against the first
stage when the gate level its *signal device* (the transistor whose gate is
the ``in`` port) requires falls outside the input pair's reachable output
window: an NMOS pair confines its output node to the upper part of the
supply range (its floor is the tail node, and vdd-referenced loads confine
it further), a PMOS pair mirrors that low — when the required level and
the window are disjoint, no sizing can establish the interface DC level
(mirror-type loads let the feedback loop drag the node to the boundary and
pin the pair in triode; range-limited loads rail outright).

The required level follows from the signal device's *source terminal*:
common-source stages (source on a supply) put the gate one ``V_GS`` from
that supply and suit the **opposite**-polarity pair (an NMOS pair's high
output suits a PMOS-gate CS stage, and vice versa); source followers
(source on the output node) put the gate one ``V_GS`` *beyond* the output,
toward the device's back rail, and suit the **same**-polarity pair (an
NMOS follower's gate is high, a PMOS follower's low — issue #110).

``enumerate_circuits`` therefore skips any combination where a
``second_stage``-category slot whose ``in`` net is one of the load's output
nets (``load.out``/``out1``/``out2``) requires a pair type other than the
``input_pair``'s. The check is structural (which device gates ``in`` and
where its source sits — no YAML tags), so new ``second_stage`` variants
are classified automatically. The 3-stage templates' ``third_stage`` slot
senses the *second* stage's output instead — a wide-swing common-source
node that can meet either gate level — and is deliberately left
unconstrained, as are combinations using the untagged
``inverter_based_input`` (its output level sits near mid-rail, reachable by
either gate type)
(``circuitgenome/synthesizer/second_stage_compatibility.py``).

Output-cardinality compatibility filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``load.in1``/``in2`` (the folding nodes fed by ``input_pair.out1``/``out2``)
and ``load.out``/``out1``/``out2`` (the load's actual output node(s)) are
wired to *separate* nets by every topology template. Whether the output-side
ports get a net at all depends on the topology's ``output_type``:

- ``load.out1``/``out2`` are wired to ``net_loadout1``/``net_loadout2`` only
  in ``fully_differential`` topologies (sensed by ``cmfb``/
  ``second_stage*``/``comp*``).
- ``load.out``/``out2`` are wired to the stage's single output node only in
  ``single_ended`` topologies.

Some ``load`` variants declare a *mandatory* port on one side of that
conditional wiring:

- ``folded_cascode_load_*_input_single_output`` and
  ``telescopic_cascode_load_{pmos,nmos}`` declare ``out`` as mandatory. In a
  ``fully_differential`` topology, ``out`` is never wired, leaving that
  device terminal floating (disconnected).
- ``folded_cascode_load_*_input_differential_output`` declare ``out1``/
  ``out2`` as mandatory cascode-output nodes. In a ``single_ended`` topology,
  ``net_loadout1``/``net_loadout2`` aren't defined, so ``out1``/``out2`` are
  never wired, leaving the cascode device's drain floating (disconnected).

These 6 ``load`` variants declare an ``output_cardinality`` field in
``opamp_modules.yaml``: ``"single"`` (compatible only with
``output_type: single_ended``) or ``"differential"`` (compatible only with
``output_type: fully_differential``). The other 6 ``load`` variants
(resistor/active/current-source) declare ``out1``/``out2`` as ``alias_of:
in1``/``in2`` — a net-merge pass (``net_aliasing.py``) collapses their
``out1``/``out2`` net back onto ``in1``/``in2``'s after assembly, restoring a
single shared in/out node regardless of ``output_type``. They're untagged
(``output_cardinality: None``) and compatible with either output type.
``enumerate_circuits`` skips any combination where ``load``'s
``output_cardinality`` (if set) doesn't match the topology's ``output_type``.
To extend the filter to a new or edited ``load`` variant, add the matching
``output_cardinality:`` tag in YAML — no code changes needed
(``circuitgenome/synthesizer/output_compatibility.py``).

CMFB compatibility filter
~~~~~~~~~~~~~~~~~~~~~~~~~~

``fully_differential`` topologies have a ``cmfb`` slot, wired
``cmfb.out -> net_cmfb_out -> load.bias_cmfb``. Of the 12 ``load`` variants,
only the 2 tagged ``output_cardinality: "differential"``
(``folded_cascode_load_*_input_differential_output``) declare ``bias_cmfb`` as
a real ``role: input`` consumer (gating ``mn3``/``mn4`` or ``mp1``/``mp2``);
the other 10 declare it ``role: optional`` and never reference it, so
``net_cmfb_out`` would drive nothing.

For a ``load`` whose ``output_cardinality`` isn't ``"differential"``, only the
canonical ``resistive_sense_cmfb`` variant is allowed through -- the
``dda_cmfb`` choice would otherwise be enumerated as a duplicate no-op
circuit. That canonical variant is then pruned to an empty placeholder (no
ports, no devices), so it contributes no devices to the assembled circuit and
``cmfb.bias`` is no longer counted as a needed bias rail. The
``vcm_ref`` external port (statically present on every ``fully_differential``
topology) is left unconnected for these circuits. To extend: tag a new or
edited ``load`` variant with ``output_cardinality: "differential"`` (and give
it a real ``bias_cmfb: role: input`` consumer) to make it a genuine ``cmfb``
consumer -- no code changes needed
(``circuitgenome/synthesizer/cmfb_compatibility.py``).

Tail-current compatibility filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every topology has a ``tail_current`` slot, wired ``input_pair.tail ->
net_tail <- tail_current.out``. Of the 5 ``input_pair`` variants, only the 4
``differential_pair_*`` variants reference their ``tail`` port from a device
terminal (``s``/``b: tail`` on the tail transistor, or ``t2: tail`` on the
degenerated variants' tail resistor). ``inverter_based_input`` -- two
back-to-back CMOS inverters -- is self-biased by design and never references
``tail``, so without this filter ``net_tail`` would be a floating,
single-terminal node and ``tail_current`` would drive nothing.

For an ``input_pair`` that doesn't reference ``tail``, only the canonical
``current_mirror_tail_pmos`` variant is allowed through -- the other 5
``tail_current`` choices would otherwise be enumerated as duplicate no-op
circuits. That canonical variant is then pruned to an empty placeholder (no
ports, no devices), so it contributes no devices to the assembled circuit,
``net_tail`` is no longer floating, and ``tail_current.bias`` is no longer
counted as a needed bias rail. To extend: wire a new or edited ``input_pair``
variant's tail-side device terminal(s) to ``tail`` to make it a genuine
``tail_current`` consumer -- no code changes needed
(``circuitgenome/synthesizer/tail_current_compatibility.py``).

Demand-driven bias construction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The bias generator is not an enumerated module: ``enumerate_circuits``
*constructs* it per combination from what the other slots actually consume
on each of the eight bias rails (``out1``..``out4`` feed
``load.bias1``/``bias2``/``bias3``/``bias_cmfb``, ``out5`` feeds
``second_stage*.bias``, ``out6`` feeds ``third_stage*.bias``, ``out7`` feeds
``tail_current.bias``, ``out8`` feeds ``tail_current.bias_casc``; each
role's rail is independent, so the roles never share a bias voltage and can
be sized independently).

Each consumed rail is classified structurally (no YAML tags) into a *kind*:

- ``gate_vdd`` / ``gate_gnd`` -- a consumer MOSFET gate whose source sits on
  a supply needs a voltage one ``V_GS`` from that supply. The leg is an
  ``ibias``-derived mirror ending in a diode-connected device on the rail,
  which doubles as the mirror *master* of its consumers -- the sizer sets
  consumer currents by W/L ratio instead of matching voltages.
- ``current_source`` / ``current_sink`` -- the consumer brings its own
  reference diode (the current-mirror tails' mirror diode): the rail is a
  *current* interface and the leg is a bare
  mirror with no diode of its own. A bias-side diode here would either sit
  in parallel with the tail's reference (splitting the current) or fight it
  (issue #99's measured rail-7 contention) -- both are now unconstructable.
- ``cascode_gnd`` / ``cascode_vdd`` -- a cascode gate (consumer source on an
  internal node) needs its ``V_GS`` plus the saturation floor of the stack
  toward its back supply. The leg is a mirror into a diode-connected device
  riding a small floor resistor (``out = V_GS + I × R`` from that supply):
  the diode covers the large, Vth-dependent ``V_GS`` part -- tracking the
  consumer over process and temperature -- and the resistor covers only the
  small Vdsat floor; both are sized per rail by the sizer from the consumer
  stack (issue #99's parked cascode class).
- ``tunable`` -- no structurally implied level (conflicting demands on a
  shared rail): a mirror into a resistor, ``out = I_leg × R``, per-rail
  tunable by the sizer.

The constructed variant (name ``constructed_bias``) always carries an NMOS
master reference on the ``ibias`` pin; a ``pref`` branch deriving the
PMOS-side mirror reference is emitted only when some leg needs it. The pref
branch is *cascoded*: a wide-swing ``ncasc`` level (PMOS mirror into a
narrow diode) pins the branch mirror's Vds near the master's instead of at
``vdd - |V_GSP|`` -- closing most of the extra-mirror-hop λ error that
issue #103's A/B measured against the retired ``magic_battery_bias``. Only
consumed rails get a port and a leg -- unconsumed rails simply don't exist.
The leg templates live in ``config/bias_legs.yaml``; the demand analysis and
assembly in :mod:`circuitgenome.synthesizer.bias_construction`.

Because every rail gets exactly the leg its consumer requires, the
structurally unbiasable flavor mismatches that issue #99 measured (and that
previously had to be filtered out) can no longer be expressed, and
mixed-flavor consumer sets -- e.g. every real-cmfb fully-differential
circuit, whose rail 4 is gnd-referenced while rails 1/5 are vdd-referenced
-- get correct per-rail legs instead of being routed to an all-resistor
generator.

In ``fully_differential`` topologies, the ``cmfb`` slot's ``bias`` port is
wired to ``out4`` (``net_bias4``), but (per the "CMFB compatibility filter"
above) ``cmfb`` is pruned to an empty placeholder unless ``load``'s
``output_cardinality`` is ``"differential"`` -- construction runs after that
prune, so placeholder slots demand nothing and rail 4 gets a leg exactly
when a real cmfb consumes it.

Three-stage compensation schemes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The 3-stage templates reuse the existing ``second_stage`` modules for the
second (gm2) and third (gm3) gain stages, and the existing ``compensation``
modules for the two Miller capacitors Cm1/Cm2 — no new module variants are
required.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Scheme
     - Cm1 / Cm2 connections
   * - Nested Miller (NMC)
     - Cm1 spans gm2+gm3 (gm1's output → final output, the outer loop);
       Cm2 spans gm3 only (gm2's output → final output, the inner loop).
       Both capacitors return to the final output node.
   * - Reversed Nested Miller (RNMC)
     - Cm1 spans gm3 only (gm2's output → final output); Cm2 spans gm2 only
       (gm1's output → gm2's output) instead of returning to the final
       output. This reduces loading on the output node, which is useful
       when gm3 is a low-gain buffer stage.

Modular interface contract
~~~~~~~~~~~~~~~~~~~~~~~~~~

Each module category defines a **canonical port signature** shared by all its
variants.  The topology template wires ports to global nets by name; the
internal device structure is invisible to the template.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Category
     - Canonical ports
   * - ``input_pair``
     - ``in1``, ``in2``, ``out1``, ``out2``, ``tail``, ``vdd``, ``gnd``
   * - ``load``
     - ``in1``, ``in2`` (folding nodes, driven by ``input_pair.out1`` /
       ``out2``), ``out1``, ``out2`` (differential output nodes — wired to
       dedicated ``net_loadout1``/``net_loadout2`` nets in
       ``fully_differential`` topologies for distinct cascode-output devices,
       or merged back onto ``in1``/``in2`` via ``alias_of`` for simple
       resistor/active/current-source loads), ``out`` *(mandatory only for
       single-output cascode loads, wired to the stage's single output node
       in ``single_ended`` topologies; optional/unused otherwise)*,
       ``bias1``, ``bias2``, ``bias3``, ``bias_cmfb`` *(optional bias inputs;
       each variant declares only as many as it needs)*, ``vdd``, ``gnd``.
       Whichever of ``out``/``out1``/``out2`` is mandatory is declared via
       ``output_cardinality: "single" | "differential" | None``, checked
       against the topology's ``output_type`` by the output-cardinality
       compatibility filter
   * - ``tail_current``
     - ``out``, ``bias`` *(current-mirror / cascode-current-mirror variants
       wire this to the dedicated ``net_bias7`` rail; resistor-tail variants
       declare it ``optional`` and leave it unconnected)*, ``bias_casc``
       *(cascode-current-mirror variants only: the wide-swing cascode-gate
       level, wired to ``net_bias8``)*, ``vdd``, ``gnd``
   * - ``bias_generation``
     - ``ibias``, ``out1``..``out8`` -- consumed rails only (``out1``-``out4``
       feed ``load``'s ``bias1``/``bias2``/``bias3``/``bias_cmfb``, ``out5``
       feeds ``second_stage.bias``, ``out6`` feeds ``third_stage.bias``,
       ``out7`` feeds ``tail_current.bias``, ``out8`` feeds
       ``tail_current.bias_casc``), ``vdd``, ``gnd``. The variant
       is constructed per combination by
       :func:`~circuitgenome.synthesizer.bias_construction.construct_bias_generation`,
       with one typed leg per consumed rail
   * - ``cmfb``
     - ``in1``, ``in2`` (differential sense inputs, wired to
       ``net_loadout1``/``net_loadout2`` -- the ``load``'s cascode-output
       nodes), ``vref`` (common-mode reference, wired to the external
       ``vcm_ref`` port), ``bias`` (tail-current bias, reuses ``net_bias4``
       from ``bias_generation.out4``), ``out`` (drives ``load.bias_cmfb`` via
       ``net_cmfb_out``), ``vdd``, ``gnd``. Two variants:
       ``resistive_sense_cmfb`` (resistive averager + 5T OTA) and
       ``dda_cmfb`` (differential-difference amplifier). Present only when
       ``load``'s ``output_cardinality`` is ``"differential"`` (see "CMFB
       compatibility filter" above); otherwise pruned to an empty placeholder
       and ``vcm_ref`` is left unconnected
   * - ``compensation``
     - ``in``, ``out``
   * - ``second_stage``
     - ``in``, ``out``, ``bias``, ``vdd``, ``gnd``

Supply ports (``vdd``, ``gnd``) are automatically connected to the global
rails ``vdd!`` / ``gnd!`` unless explicitly overridden in the topology
template.

SPICE output formats
~~~~~~~~~~~~~~~~~~~~

**Flat** — every device inlined in one ``.subckt`` block.  Maximally
portable.

.. code-block:: spice

   .subckt circuit_0001 ibias in1 in2 out vdd! gnd!
   m1_input_pair net_diff1 in1 net_tail net_tail pmos
   m2_input_pair net_mid in2 net_tail net_tail pmos
   r1_load vdd! net_diff1 1k
   r2_load vdd! net_mid 1k
   ...
   .ends

**Hierarchical** — one ``.subckt`` per module variant, top-level uses ``X``
instances.  Shared variants are defined only once.

.. code-block:: spice

   .subckt differential_pair_pmos in1 in2 out1 out2 tail vdd gnd
   m1 out1 in1 tail tail pmos
   m2 out2 in2 tail tail pmos
   .ends

   .subckt circuit_0001 ibias in1 in2 out vdd! gnd!
   Xinput_pair in1 in2 net_diff1 net_mid net_tail vdd! gnd! differential_pair_pmos
   ...
   .ends

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
     - 12
     - Resistor (VDD-side / GND-side), active current mirror (PMOS / NMOS),
       current-source (PMOS / NMOS), single-output folded cascode (NMOS-input /
       PMOS-input, 8 devices each), telescopic cascode (PMOS / NMOS, 6 devices
       each). Plus 2 differential-output folded-cascode variants
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
     - 5
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
       an internal ``d1`` node).

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
  **three_stage_opamp_rnmc_single_ended**: no new patterns needed. The
  ``third_stage`` slot reuses the ``second_stage`` category (same five
  pattern variants); ``comp1``/``comp2`` reuse the ``compensation`` category.
  FBR's ``assigned_ids`` mechanism correctly disambiguates 2 same-category
  ``second_stage`` slots and 2 same-category ``compensation`` slots via
  connectivity scoring on the distinct intermediate nets
  (``net_mid1``/``net_mid2``). 9 round-trip combos each.
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

Initial Sizer
-------------

The sizer takes the FBR result (slot assignments) plus a performance
specification and returns minimum W/L values for every transistor in the
circuit.  It supports all seven op-amp topology templates — one-stage,
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

The ``SizingSpec`` dataclass accepts:

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Field
     - Unit
     - Description
   * - ``vdd`` / ``vss``
     - V
     - Supply rails
   * - ``ibias``
     - A
     - Tail bias current (each input device carries ``ibias/2``)
   * - ``cl``
     - F
     - Output load capacitance
   * - ``second_stage_current_ratio``
     - —
     - ``iDS_2 = ratio × ibias`` (default 2.0)
   * - ``third_stage_current_ratio``
     - —
     - ``iDS_3 = ratio × ibias`` (three-stage only; default 5.0)
   * - ``gain_min_db``
     - dB
     - Minimum open-loop DC voltage gain
   * - ``gbw_min_hz``
     - Hz
     - Minimum unity-gain bandwidth
   * - ``phase_margin_min_deg``
     - °
     - Minimum phase margin (dominant-pole model)
   * - ``slew_rate_min_vps``
     - V/s
     - Minimum slew rate (``ibias / Cc``)
   * - ``cmrr_min_db``
     - dB
     - Minimum common-mode rejection ratio
   * - ``power_max_w``
     - W
     - Maximum quiescent power
   * - ``output_swing_max_v`` / ``output_swing_min_v``
     - V
     - Output voltage swing limits

Sizing algorithm
~~~~~~~~~~~~~~~~

.. note::

   The requirement-derivation order below is shown for the **two-stage** case
   as an illustration. The complete derivation for all seven topologies —
   including the three-stage inner-pole and :math:`g_{m3}` steps — is covered
   in :doc:`theory/sizing_flow`.

The sizer has two paths, selected by technology.  The **card-less ``generic``
tech** uses a Level-1 MOSFET model where ``gm = √(2·µCox·(W/L)·IDS)`` and
``gd = λ·|IDS|``.  Because ``IDS`` is topology-determined by KCL and the bias
current, the ``gm ≥ gm_req`` constraint linearises to
``2·µCox·IDS·W ≥ gm_req²·L``, a linear integer constraint once W and L are
discrete grid variables — solved with CP-SAT.

**PTM nodes use the gm/Id pipeline instead**: geometry is chosen deterministically
from a SPICE-characterised gm/Id lookup table (no CP-SAT search), which captures
moderate/weak inversion and short-channel behaviour the square law misses.  A
PTM/SPICE-model node without a gm/Id LUT raises ``UnsupportedTechError``.  The
requirement-derivation order below is shared by both paths.

The required transconductances are derived in a fixed order to ensure mutual
consistency after the integer grid rounds values up:

1. **CMRR** — sets ``gm1`` lower bound from the tail's output conductance
   (independent of ``Cc``; computed first so the bound propagates correctly).
2. **SR → Cc** — ``Cc ≥ ibias / SR_min`` (initial upper bound on ``Cc``).
3. **GBW + gm1 → Cc** — ``Cc ≥ gm1 / (2π · GBW_min)``; ``Cc`` may grow if
   CMRR pushes ``gm1`` up.
4. **Gain → gm2** — open-loop gain ``A0 = gm1·Rout1·gm2·Rout2``; gain drives
   ``gm2`` (not ``gm1``) to keep ``gm1`` small and preserve the SR bound.
5. **PM (worst-case gm1) → gm2** — the integer grid ceiling-rounds ``W1`` up,
   increasing the actual ``gm1``; ``gm2`` is computed from the ceiling-rounded
   value so the phase margin holds on the discrete grid.

CP-SAT integer solver
~~~~~~~~~~~~~~~~~~~~~

W and L for each transistor are integer variables (in units of the
technology grid step).  The solver minimises total gate width (proxy for
power and area) subject to the linearised ``gm`` and ``VDS_sat`` constraints,
plus symmetry constraints (matched pairs within ``input_pair``, ``load``, and
``tail_current`` slots).  The branching heuristic prioritises ``bias_gen``
transistors first, then all others.

Spec compatibility notes
~~~~~~~~~~~~~~~~~~~~~~~~

The three specs ``CMRR``, ``GBW``, and ``SR`` share the same variables
(``ibias``, ``Cc``, ``gm1``) and can be **mutually exclusive** for small bias
currents.  Specifically, ``CMRR_min + GBW_min`` together fix ``Cc ≥ gm1_cmrr /
(2π · GBW_min)``; if that ``Cc`` exceeds ``ibias / SR_min``, the slew-rate
spec cannot be met.  In that case the solver returns ``INFEASIBLE``.  The
recommended approach is to specify at most two of the three, or relax ``ibias``.

Technology configurations
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The sizer reads its device parameters from a technology YAML, selected with
``circuitgenome size --tech <file>`` (default: the built-in
``tech_generic``).  Built-in configs live in
``circuitgenome/sizer/shared/config/``:

.. list-table::
   :header-rows: 1
   :widths: 32 16 52

   * - Config
     - Node
     - Notes
   * - ``tech_generic``
     - ~0.25 µm
     - Illustrative defaults; the built-in fallback.
   * - ``tech_ptm45`` / ``tech_ptm32`` / ``tech_ptm22``
     - 45 / 32 / 22 nm
     - Planar-bulk BSIM4 from the ASU Predictive Technology Model.  Sizing uses
       the gm/Id pipeline driven by a SPICE-characterised gm/Id LUT — currently
       only ``ptm45`` ships one; the others carry the BSIM4 card but need a LUT
       (characterize one with ``tools/extract_tech.py`` or sizing raises
       ``UnsupportedTechError``).
   * - ``tech_ptm16``
     - 16 nm
     - PTM 16 nm **bulk** — a *predictive planar extrapolation* (real 16 nm
       silicon is FinFET); for exploration only.
   * - ``tech_gf180mcu``
     - 180 nm
     - GlobalFoundries **GF180MCU** open PDK, 3.3 V core (``nmos_3p3``/``pmos_3p3``).
       A foundry PDK: devices are subcircuits and a process corner is selected with
       ``.lib <file> <corner>``.  Sizes from a gm/Id LUT (characterized at the
       ``typical`` corner); ships ``models/gf180mcu_gmid.npz``.

A PTM node or foundry PDK sizes from its gm/Id LUT (LUT-accurate
``gm``/``gds``/``Vdsat`` from the BSIM4 device), while the card-less ``generic``
tech uses *effective* Level-1 square-law fits.  FinFET nodes (≤16 nm in silicon)
need a different device model and are not covered.  Regenerate the configs or add a
node with ``tools/extract_tech.py`` (requires ngspice).
Source / citation: ASU Predictive Technology Model, https://ptm.asu.edu
(W. Zhao, Y. Cao, "New Generation of Predictive Technology Model for Sub-45nm
Design Exploration," ISQED 2006).

SPICE verification
~~~~~~~~~~~~~~~~~~

ngspice runs in two roles, using the model from the tech: a BSIM4 ``.pm`` card for
the PTM nodes (``spice_model``), a foundry corner library for a PDK
(``spice_lib`` → ``.lib "<file>" <corner>``, e.g. GF180MCU), or a synthesised
Level-1 ``.model`` from ``mu_cox``/``vth``/``lam`` for ``generic``:

* **PTM and foundry PDKs (default report).**  For a node with a real device model,
  ``circuitgenome size`` reports ngspice-**measured** metrics directly (BSIM4),
  grounded by a SPICE DC bias-soundness check that yields the INFEASIBLE /
  MARGINAL / FEASIBLE verdict.  ngspice is **required** here — the command errors
  if it is missing.  A foundry PDK additionally re-measures the sized design across
  its configured process corners (``{typical, ss, ff, sf, fs}`` for GF180MCU) and
  prints a corner-verification table; sizing itself stays at the nominal corner.
* **``--simulate`` (generic cross-check).**  On the Level-1 ``generic`` tech,
  ``circuitgenome size --simulate`` prints the analytical metrics next to the
  SPICE-measured ones with the delta — a sanity check on the formulas.  It is
  redundant for PTM / PDK techs (already SPICE-measured).

Measurement is **best-effort**, not sign-off.  Gain/GBW/PM come from an open-loop
AC-coupled-feedback testbench; power from the DC operating point; slew rate from a
unity-gain pulse (the min of the rising and falling edges); output swing from a
unity-buffer DC sweep; CMRR and PSRR+ from the same feedback loop with the AC
stimulus riding on the input common mode / the positive supply.  Single-ended
op-amps are the most robust; fully-differential AC metrics (which depend on the
on-chip CMFB operating point), the single-ended-only swing/slew benches on FD
circuits, and any non-converging measurement are reported as ``n/a`` rather than
as wrong numbers.
