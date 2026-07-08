Designer
========

Overview
--------

The **Designer (DES)** is the spec-driven top layer that chains the three lower
modules end to end.  :func:`~circuitgenome.designer.designer.design` enumerates
every valid circuit for the chosen template(s) with the
:doc:`synthesizer <synthesizer>`, sizes each with the :doc:`gm/Id sizer <sizer>`,
keeps the circuits whose ngspice-measured metrics meet the target spec, exports
the survivors as sized flat SPICE netlists, and returns a
:class:`~circuitgenome.designer.models.DesignReport` with per-template statistics
and the best design points.

Where the synthesizer answers *"what circuits can I build?"* and the sizer *"what
W/L makes this circuit meet a spec?"*, the Designer answers the end-to-end
question: *"given a spec, which topologies actually deliver it — and what are the
best ones?"*

Entry points
------------

- :func:`~circuitgenome.designer.designer.design` — run the full
  enumerate → size → verify → export flow against a spec.
- :class:`~circuitgenome.designer.models.DesignReport` — the returned report of
  surviving designs and per-template statistics.

How it works
------------

``design`` runs a two-level loop: over the selected **templates**, and within
each, over every enumerated **circuit variant**.  Each candidate passes through a
fixed gate, and the first stage it fails decides which bucket it lands in:

.. code-block:: text

   for each template (--topology NAME | --all):
     enumerate variants ─► for each circuit:
        1. recognize   FBR slot assignment                          ─► (structural)
        2. size        gm/Id sizer against the spec                 ─► sizing✗   if no sizing
        3. bias check  analytical + SPICE DC operating point        ─► bias✗     if it cannot bias
        4. simulate    ngspice-measure gain/GBW/PM/SR/power/swing   ─► unverif✗  if a spec is unmeasurable
        5. spec gate   every constrained metric ≥ its target        ─► spec✗     if any target missed
                                                                     ─► accepted + export sized netlist

Only candidates that clear all five stages are exported (a sized flat SPICE
netlist per survivor) and recorded as a
:class:`~circuitgenome.designer.models.DesignSolution`.  The sizer runs at the
**gm/Id** path, so the Designer needs a technology with a gm/Id LUT (default
``gf180mcu``); see :doc:`sizer`, *Path selection*.

The per-candidate evaluation is independent, so ``--workers N`` fans the circuits
out across a bounded process pool; ``--workers 1`` (the default) runs in-process.
``--limit N`` caps evaluations per template for a quick sweep instead of an
exhaustive one.

The design report
-----------------

:func:`~circuitgenome.designer.designer.design` returns a
:class:`~circuitgenome.designer.models.DesignReport`, and (unless the run finds
nothing) writes it as ``report.json`` alongside the sized netlists.  Two parts
matter most:

**Acceptance accounting** —
:class:`~circuitgenome.designer.models.TemplateStats` per template counts every
candidate into exactly one outcome, so a run is fully reconciled
(``enumerated = sizing_failed + bias_infeasible + spec_failed + unverified +
errors + accepted``):

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Bucket
     - Meaning
   * - ``sizing_failed``
     - The sizer found no sizing for the circuit.
   * - ``bias_infeasible``
     - Sized, but the analytical or SPICE DC bias check rejected it.
   * - ``spec_failed``
     - Biased and simulated, but missed at least one measured spec.
   * - ``unverified``
     - A constrained spec could not be SPICE-measured (reported, not silently passed).
   * - ``errors``
     - Unexpected per-circuit exceptions.
   * - ``accepted``
     - Met every constrained, measured spec — exported.

**Best design points** —
:attr:`~circuitgenome.designer.models.DesignReport.best_points` picks the winning
survivor per criterion (highest gain, highest GBW, highest phase margin, lowest
power, and *most robust* — the design whose ``worst_margin`` across all
constrained specs is largest).  Each accepted
:class:`~circuitgenome.designer.models.DesignSolution` also carries its measured
``metrics``, the normalized ``margins`` per spec, its ``worst_margin``, and the
``netlist_path`` of its exported netlist.

Running it
----------

``circuitgenome design`` exposes the flow on the command line — see
:doc:`../usage/cli` for the full flag list.  A quick sweep:

.. code-block:: bash

   circuitgenome design \
     --topology two_stage_opamp_single_ended \
     --spec examples/two_stage_se_specs/spec_gf180.yaml \
     --tech gf180mcu --limit 12 --workers 4 \
     --output-dir designs/

Example output
--------------

.. code-block:: text

   Design summary (tech: gf180mcu_3v3):
     template                                     evaluated  sizing✗  bias✗  spec✗  unverif✗  error  accepted
     two_stage_opamp_single_ended                        12        0      0      9         0      0         3

     3/12 circuits meet the spec (25.0%)  |  runtime 2.5s

     Top rejection reasons — two_stage_opamp_single_ended:
            9  missed spec(s): gain_db

   Best design points:
     Highest gain           two_stage_opamp_single_ended/circuit_0010  (81.21 dB)
     Highest GBW            two_stage_opamp_single_ended/circuit_0011  (8.13 MHz)
     Highest phase margin   two_stage_opamp_single_ended/circuit_0011  (112.93 °)
     Lowest power           two_stage_opamp_single_ended/circuit_0010  (0.37 mW)
     Most robust            two_stage_opamp_single_ended/circuit_0010  (worst-case margin +10%)

   Sized netlists + report.json written to designs/

Here 9 of the 12 variants biased and simulated cleanly but missed the gain
target, and the 3 survivors were exported.  ``report.json`` holds each survivor's
full record — measured metrics, per-spec margins, and its netlist path:

.. code-block:: text

   "circuit_0010": gain_db 81.21, gbw_hz 7.47e6, phase_margin_deg 83.27,
                   worst_margin +0.098 (output_swing_max_v) → the binding spec

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/designer/designer
   ../api/designer/models
