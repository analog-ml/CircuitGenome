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
   :caption: API Reference

   api/synthesizer
   api/compatibility
   api/output_compatibility
   api/cmfb_compatibility
   api/bias_pruning
   api/net_aliasing
   api/models
   api/loader
   api/netlist

.. toctree::
   :maxdepth: 1
   :caption: About

   references
