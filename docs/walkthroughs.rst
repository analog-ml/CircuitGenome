Code Walkthroughs
=================

Walkthroughs are hand-authored, figure-rich HTML deep dives into
CircuitGenome's internals — companion pages to the module docs.  Where a module
page tells you *what* a module does and its theory page derives the *math*, a
walkthrough teaches *how the code actually works*, function by function, with
worked numeric examples and hand-drawn SVG figures.

Walkthroughs open outside the documentation sidebar (they are standalone
pages); each has a "← CircuitGenome docs" link at the top to bring you back.
They are living documents, updated alongside the code — see :doc:`contributing`.

Sizer
-----

- `CircuitGenome Sizer — full walkthrough <walkthrough/index.html>`__ — the
  entry point: the whole sizing pipeline end to end, linking into the three
  package tours below.
- `Shared sizing core <walkthrough/shared/index.html>`__ — the machinery both
  sizer paths use: technology loader, device model, gm/Id lookup tables,
  metric evaluation, small-signal equations, device taxonomy, netlist
  preprocessing, and the SPICE-simulation harness.
- `Analytical (Level-1) sizer <walkthrough/analytical/index.html>`__ — the
  card-less square-law path: how device constraints linearise into a CP-SAT
  integer program.
- `gm/Id sizer <walkthrough/gmid/index.html>`__ — the SPICE-characterised
  path: sizing plan and intent, block sizing, bias levels, DC-bias
  feasibility, geometry selection, resistors, stage interfaces, and the
  analyze/evaluate loop.

Recognizer
----------

- `Circuit recognizer — a visual tour <walkthrough/recognizer/index.html>`__ —
  the recognizer package: netlist parser, subcircuit recognizer,
  functional-block recognizer, the data models, and the hook system.
