CircuitGenome
=============

A Python toolkit for **analog circuit topology synthesis and recognition** —
built for design-space exploration, dataset generation, and analog-design
research.

Why CircuitGenome
-----------------

CircuitGenome began as a Python re-implementation of
`ACST <https://github.com/inga000/acst>`_ — the C++ analog structural-synthesis
tool from I. Abel et al. at the Chair of Electronic Design Automation, Technical
University of Munich (TUM) (see :doc:`references`) — with three goals:

- **Work, test, and integrate in Python.**  A pure-Python toolkit is far easier
  to script, unit-test, and wire into machine-learning and design-exploration
  workflows than a C++ codebase — the primary motivation for the port.
- **Compose modules, not devices.**  CircuitGenome models a circuit as a
  composition of **module slots** (input pair, load, tail current, bias,
  compensation, output/gain stages), each filled by an interchangeable variant,
  rather than assembling designs at the device level.  This makes topologies easy
  to enumerate, reason about, and extend — adding a new variant or template is
  usually a YAML edit, not new code (see :doc:`extending`).
- **Document for practitioners.**  Every module ships a landing page, its theory
  or algorithm, and its API reference, so the *why* is written down, not just the
  *what*.

What it does today
------------------

The current release targets **op-amps** end to end.  From a pool of independent
functional building blocks it can:

- **Synthesize** thousands of structurally distinct op-amp netlists by enumerating
  every valid module combination for a topology template;
- **Recognize** the functional blocks in a flat SPICE netlist;
- **Size** a circuit to a performance spec (a card-less Level-1 CP-SAT path and a
  SPICE-characterized gm/Id path); and
- **Design** — chain the above against a target spec, SPICE-verify each candidate,
  and export the designs that meet it.

Browse these in the sidebar; each module has its own landing page.

.. _roadmap:

Roadmap
-------

CircuitGenome currently synthesizes op-amps.  The modular, slot-based design is
meant to generalize, and the direction is to support **more analog circuit
classes** — voltage regulators, comparators, integrators, data converters (DACs),
and beyond — reusing the same enumerate → recognize → size → verify machinery.
Longer term, the aim is to reach **analog-computing building blocks**.  These are
planned directions, not shipped features; the current version is op-amp-focused.

Contributing
------------

CircuitGenome is open and community-driven, and contributions of every kind are
welcome — code, documentation, ideas, and especially reports that *something is
electrically wrong or does not physically work*.  If you hit a bug, want a
feature, or have a new module or circuit class in mind:

- Read the :doc:`contributing` guide, then
- open an `issue <https://github.com/analog-ml/CircuitGenome/issues>`_ (bug
  reports, feature requests, correctness feedback) or a
  `pull request <https://github.com/analog-ml/CircuitGenome/pulls>`_ on
  `GitHub <https://github.com/analog-ml/CircuitGenome>`_.

Where to start
--------------

New here?  Try :doc:`installation`, then the :doc:`overview` for the high-level
tour, or jump straight to :doc:`usage/cli` to run the tools.

.. toctree::
   :hidden:
   :caption: Getting Started

   installation
   overview

.. toctree::
   :hidden:
   :caption: User Guide

   usage/cli
   usage/python_api

.. toctree::
   :hidden:
   :caption: Modules

   modules/synthesizer
   modules/subcircuit_recognizer
   modules/functional_block_recognizer
   modules/sizer
   modules/designer
   modules/visualizer

.. toctree::
   :hidden:
   :caption: About

   extending
   contributing
   references
