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
       legs (all three: shared ibias reference + four independent mirror legs)
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
combinations, only 144 have compatible PMOS/NMOS polarities (see "Module
compatibility filter" below) — the rest are filtered out by
``enumerate_circuits``. The 2-stage single-ended template therefore produces
**3 888 distinct circuits** (144 × 3 × 3 × 3). Each 3-stage single-ended
template adds two more ``second_stage`` slots (gm2, gm3) and two
``compensation`` slots (Cm1, Cm2), producing **34 992 circuits**
(144 × 3 × 3 × 3 × 3 × 3). Each 3-stage fully-differential template
duplicates those four slots per output path, producing **2 834 352
circuits** (144 × 3\ :sup:`9`).

Module compatibility filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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
(``circuitgenome/synthesizer/compatibility.py``).

Bias-rail pruning and tail-current bias assignment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every ``bias_generation`` variant exposes four output rails (``out1``..``out4``)
so it can feed the most demanding load -- a differential-output folded-cascode,
which needs all four. Most loads need fewer: simple loads (resistor/active/
current-source) need none, telescopic cascode loads need one, and
single-output folded-cascode loads need two. In multi-stage topologies, the
``second_stage``/``third_stage`` slots also tap ``out1`` for their own gate
bias, so ``out1`` is mandatory whenever such a slot exists, regardless of the
load.

``enumerate_circuits`` computes which of ``out1``..``out4`` are actually
consumed by the other slots in each combination and prunes the
``bias_generation`` variant down to just those rails, dropping the now-unused
output ports and the devices that exist only to drive them (e.g. an unused
mirror leg's diode-connected MOSFET or load resistor). This reduces the device
count of the assembled circuit without changing which combinations are
enumerated -- see :mod:`circuitgenome.synthesizer.bias_pruning`.

Every ``bias_generation`` variant shares one structural layout: a *shared
reference device* that mirrors ``ibias`` onto an internal reference node
(never touching ``out1``..``out4``), plus one self-contained *leg* per output
rail that mirrors the reference and delivers that rail via its own complete
current path. Pruning drops whole legs (and their output port) above the
highest needed rail, leaving the shared reference device and any remaining
legs untouched. Separately, some ``tail_current`` variants (the current-mirror
and cascode-current-mirror flavors) need their own bias voltage on a local
``bias`` port to set up their mirror reference --
:func:`~circuitgenome.synthesizer.bias_pruning.tail_current_needs_bias` detects
this structurally, and
:func:`~circuitgenome.synthesizer.bias_pruning.assign_tail_bias_rail` picks a
*dedicated* rail for it, immediately after the highest rail the
load/second_stage/third_stage slots need -- never shared with them. If those
slots already need all four of ``out1``..``out4``, the dedicated rail would be
a fifth (``out5``), so
:func:`~circuitgenome.synthesizer.bias_pruning.extend_bias_generation` clones
the existing fourth leg onto ``out5`` before pruning. Resistor-tail variants
declare ``bias`` as ``optional`` and need no dedicated rail.

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
     - ``in1``, ``in2`` (differential signal nodes, driven by
       ``input_pair.out1`` / ``out2``), ``out1``, ``out2`` (differential
       output nodes — alias ``in1``/``in2`` for simple loads, or distinct
       cascode-output nodes for differential-output cascode loads), ``out``
       *(mandatory only for single-output cascode loads; optional/unused
       otherwise)*, ``bias1``, ``bias2``, ``bias3``, ``bias_cmfb`` *(optional
       bias inputs; each variant declares only as many as it needs)*,
       ``vdd``, ``gnd``
   * - ``tail_current``
     - ``out``, ``bias`` *(current-mirror / cascode-current-mirror variants
       wire this to a dedicated ``net_bias{N}`` rail, N = 1-5, assigned by
       :func:`~circuitgenome.synthesizer.bias_pruning.assign_tail_bias_rail`;
       resistor-tail variants declare it ``optional`` and leave it
       unconnected)*, ``vdd``, ``gnd``
   * - ``bias_generation``
     - ``ibias``, ``out1``, ``out2``, ``out3``, ``out4`` (four independent
       mirror legs off a shared ``ibias`` reference, so cascode loads can
       receive distinct ``bias1``/``bias2``/``bias3``/``bias_cmfb`` voltages),
       ``vdd``, ``gnd``. A fifth rail (``out5``) is added dynamically by
       :func:`~circuitgenome.synthesizer.bias_pruning.extend_bias_generation`
       when ``tail_current`` needs a dedicated bias rail but ``out1``..``out4``
       are already fully used
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
