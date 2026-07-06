Visualizer (VIS)
================

The **Visualizer** is a `Streamlit <https://streamlit.io>`_ UI for exploring
CircuitGenome topologies as block diagrams.  It runs via ``circuitgenome
visualize`` (requires the ``viz`` extra: ``pip install circuitgenome[viz]``) and
offers two tabs:

- **Topology Explorer** — pick a topology and a module variant for each of its
  slots; renders the resulting block diagram and, for valid combinations, the
  assembled SPICE netlist.  Invalid combinations show why
  :func:`~circuitgenome.synthesizer.synthesizer.build_circuit` rejected them.
- **Module Browser** — lists every module variant by category with its ports
  and device count.

.. note::

   The Visualizer is an interactive tool rather than a library API.  See
   :doc:`../usage/cli` for the ``circuitgenome visualize`` command.
