Analytical Sizer
================

The Level-1 square-law path, used for the card-less ``generic`` technology.
Because each device's ``IDS`` is fixed by KCL before geometry is chosen, the
``gm`` requirements linearise into an integer program over the discrete W/L grid
and are solved for minimum gate area with OR-Tools CP-SAT.  :doc:`analytical/level1`
is the driver that runs the solve and returns the sizing; :doc:`analytical/constraints`
builds the CP-SAT model — the linearised ``gm``/``VDS,sat`` constraints, the
matched-pair symmetry, and the area objective.  The derivation and a worked
example are in :doc:`../../theory/sizing_flow`.

.. toctree::
   :maxdepth: 1

   analytical/level1
   analytical/constraints
