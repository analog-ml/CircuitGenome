Shared API
==========

The modules both sizing paths depend on, regardless of technology.
:doc:`sizer` is the entry point (:func:`~circuitgenome.sizer.sizer.size_circuit`),
which dispatches to the analytical or gm/Id path.  Around it sit the shared
building blocks: :doc:`shared/loader` reads the technology and spec YAML into
:doc:`shared/models` dataclasses; :doc:`shared/device_model` wraps the two device
models (Level-1 vs :doc:`shared/gmid_lut`) behind one interface;
:doc:`shared/equations` holds the closed-form small-signal formulas; and
:doc:`shared/taxonomy` classifies each device by its functional role.
:doc:`shared/preprocess` derives the per-device requirements consumed by both
paths, :doc:`shared/metrics` evaluates the sized design analytically, and
:doc:`shared/spice_sim` runs the ngspice verification and bias-soundness check.

.. toctree::
   :maxdepth: 1

   sizer
   shared/models
   shared/loader
   shared/device_model
   shared/equations
   shared/gmid_lut
   shared/spice_sim
   shared/taxonomy
   shared/preprocess
   shared/metrics
