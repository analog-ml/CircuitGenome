Verification API
================

ngspice verification of a *sized* design: re-simulate the circuit with the W/L
the sizer chose and measure what it actually delivers.

This is a peer of the sizers rather than a dependency of them — it consumes a
:class:`~circuitgenome.sizer.models.SizingResult` instead of helping to produce
one, and neither pipeline imports it.  Its entry points are re-exported from
:mod:`circuitgenome.sizer`, so callers write
``from circuitgenome.sizer import simulate_metrics``.

A figure-rich tour of how the bench is built and why each measurement is rigged
the way it is: `ngspice verification walkthrough
<../../walkthrough/verify/index.html>`__.

.. automodule:: circuitgenome.sizer.verify
   :members:
   :undoc-members:
   :show-inheritance:

Deck building
-------------

.. automodule:: circuitgenome.sizer.verify.deck
   :members:
   :private-members:
   :undoc-members:

Testbench rig
-------------

.. automodule:: circuitgenome.sizer.verify.rig
   :members:
   :private-members:
   :undoc-members:

Metric testbenches
------------------

.. automodule:: circuitgenome.sizer.verify.measure
   :members:
   :private-members:
   :undoc-members:

Operating point & bias soundness
--------------------------------

.. automodule:: circuitgenome.sizer.verify.op
   :members:
   :private-members:
   :undoc-members:

Entry point
-----------

.. automodule:: circuitgenome.sizer.verify.simulate
   :members:
   :undoc-members:
