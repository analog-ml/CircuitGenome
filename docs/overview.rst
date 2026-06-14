Overview
========

CircuitGenome is structured around three modules, each addressing a different
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
     - Coming soon
     - Identifies structural subcircuits (differential pairs, cascode
       mirrors, etc.) in a flat SPICE netlist.
   * - Functional Block Recognizer
     - Coming soon
     - Identifies the functional role of each part of a flat SPICE netlist
       (input stage, load, bias generation, etc.).

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
     - Diode-connected MOSFET legs, magic battery current mirror, resistor
       legs (all three: shared ibias reference + seven independent mirror
       legs -- rails 1-4 for ``load``, rail 5 for ``second_stage``, rail 6
       for ``third_stage``, rail 7 for ``tail_current``)
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
     - Common-source, common-drain (source follower), differential OTA

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

The 1-stage template therefore produces **210 distinct circuits**
(70 × 3). The 2-stage single-ended template produces **1 890 circuits**
(70 × 3 × 3 × 3); the 2-stage fully-differential template, which has two
``compensation`` slots, two ``second_stage`` slots (one per output path), and
one ``cmfb`` slot, produces **17 010 circuits** (70 × 3\ :sup:`5`). Of the 56
fully-differential-compatible ``input_pair``/``load``/``tail_current``
combinations, only the 14 using a ``"differential"``-cardinality load keep
both ``cmfb`` variants (14 × 2 = 28); the other 42 collapse ``cmfb`` to a
single canonical variant (42 × 1 = 42) -- 28 + 42 = 70 effective
load/``cmfb`` combinations, × 3\ :sup:`5` = 17 010 (see "CMFB compatibility
filter" below). Each 3-stage single-ended template adds two more
``second_stage`` slots (gm2, gm3) and two ``compensation`` slots (Cm1, Cm2) on
top of the 1-stage base, producing **17 010 circuits** (70 × 3\ :sup:`5`).
Each 3-stage fully-differential template duplicates those four slots per
output path (and keeps the single ``cmfb`` slot), producing
**1 377 810 circuits** (70 × 3\ :sup:`9`).

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
(``inverter_based_input``, and currently all ``bias_generation`` variants).
``enumerate_circuits`` skips any combination where ``load``'s or
``tail_current``'s ``polarity`` (if set) doesn't match ``input_pair``'s. To
extend the filter to a new or edited variant, add the matching ``polarity:``
tag in YAML — no code changes needed
(``circuitgenome/synthesizer/polarity_compatibility.py``).

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

Bias-rail pruning
~~~~~~~~~~~~~~~~~

Every ``bias_generation`` variant exposes seven independent output rails
(``out1``..``out7``), one per bias-consuming role: ``out1``..``out4`` feed
``load.bias1``/``bias2``/``bias3``/``bias_cmfb``, ``out5`` feeds
``second_stage*.bias``, ``out6`` feeds ``third_stage*.bias``, and ``out7``
feeds ``tail_current.bias``. Each role's rail is independent of the others --
``load``, ``second_stage``, ``third_stage``, and ``tail_current`` never share
a bias voltage, so each can be sized independently.

Most combinations don't need every rail: simple loads (resistor/active/
current-source) need none of ``out1``..``out4``, telescopic cascode loads need
one, single-output folded-cascode loads need two, and only a
differential-output folded-cascode needs all four. Resistor-tail variants
declare ``bias`` as ``optional`` and never need ``out7``. In a single-stage
topology there is no ``second_stage``/``third_stage`` slot, so ``out5``/
``out6`` are never needed.

In ``fully_differential`` topologies, the ``cmfb`` slot's ``bias`` port is
also wired to ``out4`` (``net_bias4``), but (per the "CMFB compatibility
filter" above) ``cmfb`` is pruned to an empty placeholder unless ``load``'s
``output_cardinality`` is ``"differential"`` -- so rail 4 is needed under
exactly the same condition as the "only a differential-output folded-cascode
needs all four" rule above, whether ``single_ended`` (no ``cmfb`` slot, rail 4
needed via ``load.bias_cmfb`` directly) or ``fully_differential`` (rail 4
needed via ``cmfb.bias``).

``enumerate_circuits`` computes which of ``out1``..``out7`` are actually
consumed by the other slots in each combination (any subset of ``{1..7}``, not
necessarily contiguous) and prunes the ``bias_generation`` variant down to
just those rails, dropping the now-unused output ports and the devices that
exist only to drive them (e.g. an unused mirror leg's diode-connected MOSFET
or load resistor). This reduces the device count of the assembled circuit
without changing which combinations are enumerated -- see
:mod:`circuitgenome.synthesizer.bias_pruning`.

Every ``bias_generation`` variant shares one structural layout: a *shared
reference device* that mirrors ``ibias`` onto an internal reference node
(never touching ``out1``..``out7``), plus one self-contained *leg* per output
rail that mirrors the reference and delivers that rail via its own complete
current path. Pruning drops each leg (and its output port) whose rail is not
needed, leaving the shared reference device and the needed legs untouched.

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
       declare it ``optional`` and leave it unconnected)*, ``vdd``, ``gnd``
   * - ``bias_generation``
     - ``ibias``, ``out1``, ``out2``, ``out3``, ``out4``, ``out5``, ``out6``,
       ``out7`` (seven independent mirror legs off a shared ``ibias``
       reference: ``out1``-``out4`` feed ``load``'s
       ``bias1``/``bias2``/``bias3``/``bias_cmfb``, ``out5`` feeds
       ``second_stage.bias``, ``out6`` feeds ``third_stage.bias``, ``out7``
       feeds ``tail_current.bias``), ``vdd``, ``gnd``. Each combination's
       :func:`~circuitgenome.synthesizer.bias_pruning.prune_bias_generation`
       drops whichever subset of ``out1``..``out7`` isn't needed
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
   input_pair_m1 net_diff1 in1 net_tail net_tail pmos
   input_pair_m2 net_mid in2 net_tail net_tail pmos
   load_r1 vdd! net_diff1 1k
   load_r2 vdd! net_mid 1k
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
