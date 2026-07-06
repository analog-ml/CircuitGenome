Topology Synthesizer (SYN)
==========================

The **Topology Synthesizer** constructs op-amp circuits from modular functional
building blocks and emits SPICE netlists.  It models an op-amp as a composition
of **module slots** (input pair, load, tail current, bias, compensation,
amplification/output stage); each slot is filled by a concrete **module
variant**, and :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`
iterates over every valid combination, wiring them together according to a
**topology template**.  Cross-slot *compatibility filters* prune combinations
that are non-functional or electrically invalid, so the enumeration yields only
structurally sound circuits — thousands of them, for dataset generation, design
exploration, or topology studies.

Entry points
------------

- :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits` — generate
  every valid circuit for a topology template.
- :func:`~circuitgenome.synthesizer.synthesizer.synthesize` — build a single
  circuit from an explicit variant map.
- :func:`~circuitgenome.synthesizer.netlist.to_flat_spice` /
  :func:`~circuitgenome.synthesizer.netlist.to_hierarchical_spice` — export a
  :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit` to SPICE.

For the full walkthrough of module categories, topology templates, and
demand-driven bias construction, see the :doc:`../overview`.

Analysis
--------

.. toctree::
   :maxdepth: 1

   ../theory/compatibility_filters

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/synthesizer
   ../api/models
   ../api/loader
   ../api/netlist
   ../api/compatibility/index
   ../api/bias_construction
   ../api/net_aliasing
