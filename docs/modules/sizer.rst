Sizer (SZ)
==========

The **Sizer** takes an FBR slot assignment plus a performance specification and
returns minimum transistor W/L values for every device in the circuit.  It
supports all seven op-amp topology templates (one-stage, two-stage
single-ended/fully-differential, and the four three-stage NMC/RNMC variants) and
targets DC specs — gain, GBW, phase margin, slew rate, CMRR, power, and output
swing.

The sizer has two paths, selected by technology:

- The card-less ``generic`` tech uses a **Level-1 square-law model**, which
  linearises the ``gm`` constraints into an integer program solved with
  **OR-Tools CP-SAT**.
- PTM nodes and foundry PDKs (e.g. GF180MCU) use the **gm/Id pipeline**, which
  chooses geometry deterministically from a SPICE-characterised gm/Id lookup
  table, capturing moderate/weak-inversion and short-channel behaviour the
  square law misses.

Sized designs are verified with **ngspice** — measured directly for real device
models, and cross-checked against the analytical formulas for the ``generic``
tech.

Entry points
------------

- :func:`~circuitgenome.sizer.sizer.size_circuit` — size a circuit against a
  :class:`~circuitgenome.sizer.shared.models.SizingSpec`.
- :func:`~circuitgenome.sizer.shared.loader.load_tech` /
  :func:`~circuitgenome.sizer.shared.loader.load_spec` — load a technology
  config or a performance spec.

Analysis
--------

.. toctree::
   :maxdepth: 1

   ../theory/sizing_flow
   ../theory/gmid_sizing_flow

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/sizer/sizer
   ../api/sizer/shared/models
   ../api/sizer/shared/loader
   ../api/sizer/shared/device_model
   ../api/sizer/shared/equations
   ../api/sizer/shared/gmid_lut
   ../api/sizer/shared/spice_sim
   ../api/sizer/shared/taxonomy
   ../api/sizer/shared/preprocess
   ../api/sizer/shared/metrics
   ../api/sizer/analytical/level1
   ../api/sizer/analytical/constraints
   ../api/sizer/gmid/gmid_sizer
   ../api/sizer/gmid/analyze
   ../api/sizer/gmid/blocks
   ../api/sizer/gmid/plan
   ../api/sizer/gmid/intent
   ../api/sizer/gmid/geometry
   ../api/sizer/gmid/bias
   ../api/sizer/gmid/resistors
   ../api/sizer/gmid/bias_levels
   ../api/sizer/gmid/evaluate
