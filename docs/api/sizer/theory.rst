Theory
======

The design derivations behind the two sizing paths — read these to understand
*why* the sizer computes what it does, not just the API surface.
:doc:`../../theory/sizing_flow` covers the analytical **square-law** path: the
square-law device equations, the CMRR → SR → GBW → gain → PM constraint order,
the CP-SAT integer linearisation, and a worked numerical example.
:doc:`../../theory/gmid_sizing_flow` covers the **gm/Id** path: the five-phase
procedural pipeline and the role vs functional-building-block tagging that fixes
each device's ``gm/Id``.  Three pages derive the closed-form phase margins step by step, starting
from one RC pole: :doc:`../../theory/single_stage_phase_margin`,
:doc:`../../theory/two_stage_phase_margin` (Miller) and
:doc:`../../theory/three_stage_phase_margin` (nested Miller).
:doc:`../../theory/compensation_design_rules` derives the RNMC second-stage
floor and the nulling resistor.  :doc:`../../theory/rnmc_stability` explains how a
reversed-nested-Miller loop is judged — poles, damping ζ and phase margin from
a small nodal model, and :doc:`../../theory/rnmc_buffered_oscillation` gives
the analog background for one case of it: why buffered RNMC op-amps
oscillated (issue #266) and how the sizer now avoids it.  Which path runs for a given technology is decided in
:doc:`../../modules/sizer`, *Path selection*.

.. toctree::
   :maxdepth: 1

   ../../theory/sizing_flow
   ../../theory/gmid_sizing_flow
   ../../theory/single_stage_phase_margin
   ../../theory/two_stage_phase_margin
   ../../theory/three_stage_phase_margin
   ../../theory/compensation_design_rules
   ../../theory/rnmc_stability
   ../../theory/rnmc_buffered_oscillation
