gm/Id Sizer
===========

The deterministic gm/Id pipeline, used for PTM nodes and foundry PDKs.  With
current fixed by KCL and a ``gm/Id`` target chosen per device, a SPICE-characterised
LUT turns ``IDS/W`` straight into ``W``, so geometry is computed in one forward
pass rather than searched.  :doc:`gmid/gmid_sizer` is the orchestrator
(:func:`~circuitgenome.sizer.gmid.gmid_sizer.size_gmid`) that runs the five phases:
**Analyze** (:doc:`gmid/analyze`) builds the structural view; **Plan**
(:doc:`gmid/plan`) assigns bias currents and per-device ``gm`` requirements;
**Size** places geometry, then repairs the DC bias (:doc:`gmid/bias`); and
**Evaluate** (:doc:`gmid/evaluate`) computes the analytical metrics.  Supporting
these are :doc:`gmid/blocks` and :doc:`gmid/intent` (the role/``gm-Id`` tagging),
:doc:`gmid/geometry` (the LUT → W/L core), and :doc:`gmid/resistors` /
:doc:`gmid/bias_levels` (the resistor network and bias-level tuning).  The full
walk-through is in :doc:`../../theory/gmid_sizing_flow`.

.. toctree::
   :maxdepth: 1

   gmid/gmid_sizer
   gmid/analyze
   gmid/plan
   gmid/bias
   gmid/evaluate
   gmid/blocks
   gmid/intent
   gmid/geometry
   gmid/resistors
   gmid/bias_levels
