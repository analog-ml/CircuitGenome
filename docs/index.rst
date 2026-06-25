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
   :caption: Theory

   theory/sizing_flow

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
   :maxdepth: 2
   :caption: API Reference - Sizer

   api/sizer/sizer
   api/sizer/shared/models
   api/sizer/shared/loader
   api/sizer/shared/device_model
   api/sizer/shared/equations
   api/sizer/shared/gmid_lut
   api/sizer/shared/spice_sim
   api/sizer/shared/preprocess
   api/sizer/shared/metrics
   api/sizer/analytical/level1
   api/sizer/analytical/constraints
   api/sizer/gmid/gmid_sizer
   api/sizer/gmid/blocks
   api/sizer/gmid/dc_op
   api/sizer/gmid/intent
   api/sizer/gmid/resistors
   api/sizer/gmid/geometry
   api/sizer/gmid/headroom

.. toctree::
   :maxdepth: 1
   :caption: About

   references
