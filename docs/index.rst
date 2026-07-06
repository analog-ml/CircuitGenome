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

The documentation is organised by **module**.  Each module has a landing page
that introduces the module, points to its analysis/theory, and links its API
reference — start there and drill down as needed.

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   installation
   overview

.. toctree::
   :maxdepth: 1
   :caption: User Guide

   usage/cli
   usage/python_api

.. toctree::
   :maxdepth: 1
   :caption: Modules

   modules/synthesizer
   modules/subcircuit_recognizer
   modules/functional_block_recognizer
   modules/sizer
   modules/designer
   modules/visualizer

.. toctree::
   :maxdepth: 1
   :caption: About

   extending
   references
