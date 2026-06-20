CircuitGenome
=============

A Python toolkit for **analog circuit topology synthesis and recognition**,
focused on op-amp design.

CircuitGenome takes a modular approach: a complete circuit is assembled from
independent *functional building blocks* (differential pair, load, tail
current, bias, compensation, output stage).  By enumerating every valid
combination of block implementations, the tool can quickly generate thousands
of structurally distinct op-amp netlists for dataset generation, automated
design exploration, or topology studies.

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   installation
   overview

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   usage/cli
   usage/python_api
   extending

.. toctree::
   :maxdepth: 2
   :caption: API Reference - Synthesizer

   api/synthesizer
   api/models
   api/loader
   api/netlist
   api/polarity_compatibility
   api/output_compatibility
   api/cmfb_compatibility
   api/tail_current_compatibility
   api/bias_pruning
   api/net_aliasing

.. toctree::
   :maxdepth: 2
   :caption: API Reference - Subcircuit & Functional Block Recognizer

   api/recognizer/models
   api/recognizer/netlist_parser
   api/recognizer/subcircuit_recognizer
   api/recognizer/functional_block_recognizer
   api/recognizer/hooks

.. toctree::
   :maxdepth: 1
   :caption: About

   references
