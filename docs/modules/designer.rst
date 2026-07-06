Designer
========

The **Designer** is the spec-driven top layer that chains the three lower
modules end to end.  :func:`~circuitgenome.designer.designer.design` enumerates
every valid circuit for the chosen template(s) with the
:doc:`synthesizer <synthesizer>`, sizes each with the :doc:`gm/Id sizer <sizer>`,
keeps the circuits whose ngspice-measured metrics meet the target spec, exports
the survivors as sized flat SPICE netlists, and returns a
:class:`~circuitgenome.designer.models.DesignReport` with per-template
statistics and the best design points.

Entry points
------------

- :func:`~circuitgenome.designer.designer.design` — run the full
  enumerate → size → verify → export flow against a spec.
- :class:`~circuitgenome.designer.models.DesignReport` — the returned report of
  surviving designs and per-template statistics.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/designer/designer
   ../api/designer/models
