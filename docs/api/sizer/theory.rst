Theory
======

The design derivations behind the two sizing paths — read these to understand
*why* the sizer computes what it does, not just the API surface.
:doc:`../../theory/sizing_flow` covers the analytical **Level-1** path: the
square-law device equations, the CMRR → SR → GBW → gain → PM constraint order,
the CP-SAT integer linearisation, and a worked numerical example.
:doc:`../../theory/gmid_sizing_flow` covers the **gm/Id** path: the five-phase
procedural pipeline and the role vs functional-building-block tagging that fixes
each device's ``gm/Id``.  :doc:`../../theory/rnmc_stability` explains how a
reversed-nested-Miller loop is judged — poles, damping ζ and phase margin from
a small nodal model.  Which path runs for a given technology is decided in
:doc:`../../modules/sizer`, *Path selection*.

.. toctree::
   :maxdepth: 1

   ../../theory/sizing_flow
   ../../theory/gmid_sizing_flow
   ../../theory/rnmc_stability
