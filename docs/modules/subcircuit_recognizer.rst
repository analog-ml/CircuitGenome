Subcircuit Recognizer (SR)
==========================

The **Subcircuit Recognizer** is the first structural half of the recognizer
pipeline — the inverse of the synthesizer.  Given a flat SPICE netlist, it
recovers the building blocks that produced it in two layers:

1. **Layer 0 — netlist parsing**
   (:func:`~circuitgenome.recognizer.netlist_parser.parse`) turns flat SPICE
   text back into a :class:`~circuitgenome.recognizer.models.ParsedNetlist`
   (devices plus external ports and internal nets).
2. **Layer 1 — subcircuit recognition**
   (:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize`) matches a
   library of small structural patterns — differential pairs, current mirrors,
   cascode loads, bias legs — against the parsed devices, producing a
   :class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`.

SR reports **all** matching candidates (including overlapping ones) and does not
pick a winner; disambiguation is the job of the
:doc:`Functional Block Recognizer <functional_block_recognizer>`.  Awkward
constraints that resist a declarative pattern are handled by *hooks*.

Entry points
------------

- :func:`~circuitgenome.recognizer.netlist_parser.parse` — flat SPICE → parsed
  netlist.
- :func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize` — parsed
  netlist → recognized structures.

For the pattern library and hook mechanism in detail, see the
:doc:`../overview`.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/recognizer/netlist_parser
   ../api/recognizer/subcircuit_recognizer
   ../api/recognizer/hooks
   ../api/recognizer/models
