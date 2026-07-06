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
- :ref:`Designer (DES) <overview-designer>` — chains synthesis, sizing, and
  verification end to end to return designs that meet a target spec.
- :ref:`Visualizer (VIS) <overview-visualizer>` — an interactive Streamlit UI
  for browsing topologies and module variants as block diagrams.

.. _overview-synthesizer:

Topology Synthesizer (SYN)
--------------------------

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

Subcircuit & Functional Block Recognizer (SR / FBR)
---------------------------------------------------

The recognizer is the structural inverse of the synthesizer: given a flat SPICE
netlist, it recovers the modular building blocks that produced it.  It is
organized as a 3-layer pipeline:

1. **Netlist parsing (Layer 0)** — reads the flat SPICE text back into a
   structured netlist of devices, external ports, and internal nets.
2. **Subcircuit Recognizer (SR, Layer 1)** — matches a library of structural
   patterns (differential pairs, current mirrors, cascode loads, bias legs)
   against the parsed devices and reports every matching candidate, leaving the
   disambiguation to the next layer.
3. **Functional Block Recognizer (FBR, Layer 2)** — assigns each recognized
   structure to its functional role, either against a known topology template
   (recovering the exact set of module variants) or topology-free (grouping the
   structures by functional block for a netlist of unknown origin).

Together, SR and FBR support round-trip recognition of all seven topology
templates the synthesizer produces.

Using it
~~~~~~~~

Recognize a netlist from the command line with ``circuitgenome recognize`` or
from Python.  See :doc:`usage/cli` and :doc:`usage/python_api` for worked
examples, and the
:doc:`Subcircuit Recognizer (SR) <modules/subcircuit_recognizer>` and
:doc:`Functional Block Recognizer (FBR) <modules/functional_block_recognizer>`
module pages for the netlist parser, pattern library, hooks, and the
topology-free disambiguation algorithm.

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

Size a circuit from the command line with ``circuitgenome size`` or from
Python.  See :doc:`usage/cli` and :doc:`usage/python_api` for worked examples,
and the :doc:`Sizer (SZ) module page <modules/sizer>` for the sizing algorithm,
technology configurations, and SPICE verification.

.. _overview-designer:

Designer (DES)
--------------

The Designer is the spec-driven top layer: it chains the three lower modules
end to end.  Given a target performance specification, it enumerates every valid
circuit for the chosen template(s), sizes each one, keeps only those whose
SPICE-measured metrics meet the spec, and exports the survivors as sized
netlists — turning a one-line specification into a set of ready-to-simulate
op-amp designs.

What it does
~~~~~~~~~~~~

- **End-to-end search** — runs the full synthesize → size → SPICE-verify →
  export flow for every candidate, so you start from a spec rather than a
  hand-written netlist.
- **Spec-driven acceptance** — keeps only the designs whose SPICE-measured
  metrics satisfy the target, and records why each rejected candidate failed.
- **Multi-template** — searches across one or several topology templates in a
  single run, in parallel.
- **Ranked results** — returns per-template statistics and the best design
  points, with the sized netlists written out for further simulation.

Using it
~~~~~~~~

Run the full flow from the command line with ``circuitgenome design``.  See the
:doc:`Designer module page <modules/designer>` for the Python API and the
report structure.

.. _overview-visualizer:

Visualizer (VIS)
----------------

The Visualizer is an interactive Streamlit web UI for exploring the topology
space by hand.  It renders any topology and module-variant combination as a
block diagram — and, for valid combinations, the assembled SPICE netlist — so
you can see how the modular building blocks fit together without writing any
code.

Two tabs
~~~~~~~~

- **Topology Explorer** — pick a topology and swap each slot's module variant;
  the block diagram and netlist update live.  Invalid combinations show why the
  synthesizer rejected them.
- **Module Browser** — lists every module variant by category, with its ports
  and device count.

.. figure:: /images/topology_visualizer.png
   :alt: Topology Explorer screenshot
   :width: 100%

   The Topology Explorer tab: pick a topology and module variants in the
   sidebar, and the block diagram updates live.

Using it
~~~~~~~~

Launch the Visualizer from the command line — see :doc:`usage/cli` for how to
run it and the ``viz`` extra it needs.
