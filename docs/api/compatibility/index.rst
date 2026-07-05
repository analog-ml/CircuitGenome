Compatibility Filters — API Reference
=====================================

The ``compatibility`` subpackage holds the cross-slot filters that
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits` applies to
reject non-functional or duplicate variant combinations — one slot-level rule
per submodule. Import them from the subpackage itself; the per-module split is
an internal detail::

    from circuitgenome.synthesizer.compatibility import is_cmfb_compatible, prune_cmfb

Most filters are a single ``is_*_compatible`` predicate; the ``cmfb`` and
``tail_current`` filters additionally provide a paired ``prune_*`` transform.

For the electrical rationale behind each filter and how it narrows the
enumeration, see :doc:`../../theory/compatibility_filters`.

Per-module reference
--------------------

.. toctree::
   :maxdepth: 1

   polarity
   second_stage
   compensation
   output
   load_branch
   cmfb
   tail_current
