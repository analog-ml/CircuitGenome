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
- :ref:`Designer <overview-designer>` — chains synthesis, sizing, and
  verification end to end to return designs that meet a target spec.
- :ref:`Visualizer <overview-visualizer>` — an interactive Streamlit UI for
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

Using it
~~~~~~~~

Recognize a netlist from the command line with ``circuitgenome recognize`` or
from Python with :func:`~circuitgenome.recognizer.netlist_parser.parse`,
:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize`, and
:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`.
See :doc:`usage/cli` and :doc:`usage/python_api` for worked examples, and the
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

Size a circuit from the command line with ``circuitgenome size`` or from Python
with :func:`~circuitgenome.sizer.sizer.size_circuit`.  See :doc:`usage/cli` and
:doc:`usage/python_api` for worked examples, and the
:doc:`Sizer (SZ) module page <modules/sizer>` for the sizing algorithm,
technology configurations, and SPICE verification.

.. _overview-designer:

Designer
--------

The Designer is the spec-driven top layer: it chains the three lower modules
end to end — enumerate every valid circuit for the chosen template(s) with the
synthesizer, size each with the sizer, keep the ones whose ngspice-measured
metrics meet the target spec, and export the survivors as sized SPICE netlists.
:func:`~circuitgenome.designer.designer.design` returns a
:class:`~circuitgenome.designer.models.DesignReport` with per-template
statistics and the best design points.  See the
:doc:`Designer module page <modules/designer>` for details.

.. _overview-visualizer:

Visualizer
----------

``circuitgenome visualize`` launches a Streamlit web UI for browsing topologies
and module variants: pick a topology, swap each slot's module variant, and see
the resulting block diagram (and SPICE netlist, for valid combinations) update
live.  It requires the ``viz`` extra (``pip install circuitgenome[viz]``).

.. figure:: /images/topology_visualizer.png
   :alt: Topology Explorer screenshot
   :width: 100%

   The Topology Explorer tab: pick a topology and module variants in the
   sidebar, and the block diagram updates live.

See the :doc:`Visualizer module page <modules/visualizer>` for the two tabs
(Topology Explorer and Module Browser).
