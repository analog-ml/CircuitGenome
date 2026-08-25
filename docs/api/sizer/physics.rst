Physics API
===========

The op-amp physics both sizing paths stand on, regardless of technology.  The
two pipelines disagree about *how* to choose a geometry, but not about the
relationships that geometry has to satisfy — those are properties of the
circuit, not of the transistor model, so they live here once.

:doc:`physics/circuit_view` turns an FBR result into the structural view both
sizers start from, using the slot and net conventions in
:doc:`physics/taxonomy`.  :doc:`physics/device_model` wraps the two device
backends — square-law (:doc:`physics/equations`) and measured LUT
(:doc:`physics/gmid_lut`) — behind one interface, so everything above it is
written once.  :doc:`physics/preprocess` derives the per-device currents, gm
targets and compensation caps from the spec; :doc:`physics/stage_chain`
extracts the per-stage ``gm``/``Rout`` a *solved* sizing presents; and
:doc:`physics/metrics` turns that chain into predicted metrics and spec
margins.

Nothing here decides a width, and nothing here runs a simulator — see
:doc:`analytical`, :doc:`gmid` and :doc:`verify`.

.. toctree::
   :maxdepth: 1

   physics/circuit_view
   physics/taxonomy
   physics/device_model
   physics/equations
   physics/gmid_lut
   physics/preprocess
   physics/stage_chain
   physics/metrics
