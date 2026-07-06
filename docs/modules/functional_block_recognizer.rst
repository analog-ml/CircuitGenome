Functional Block Recognizer (FBR)
=================================

The **Functional Block Recognizer** is the second half of the recognizer
pipeline (Layer 2).  It takes the — possibly overlapping — candidates reported
by the :doc:`Subcircuit Recognizer <subcircuit_recognizer>` and assigns each
recognized structure to its functional role, resolving ambiguity by
connectivity scoring.  It runs in two modes:

- **Topology mode**
  (:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`) —
  with a :class:`~circuitgenome.synthesizer.models.TopologyTemplate`, each
  template slot (``input_pair``, ``load``, …) is assigned its best-matching SR
  candidate, recovering the synthesizer's ``variant_map`` shape.
- **Topology-free mode**
  (:func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`) —
  without a template, structures are grouped by ``circuit_block`` and
  ``category`` using external-port adjacency as the disambiguation signal, for
  recognition of arbitrary netlists with unknown net names.

Together, SR + FBR support round-trip recognition of all seven topology
templates the synthesizer produces.

Entry points
------------

- :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots` —
  topology-guided slot assignment.
- :func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`
  — topology-free grouping.

For the scoring passes and coverage in detail, see the :doc:`../overview`.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/recognizer/functional_block_recognizer
