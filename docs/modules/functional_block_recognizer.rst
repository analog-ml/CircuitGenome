Functional Block Recognizer
===========================

The **Functional Block Recognizer (FBR)** is the second half of the recognizer
pipeline (Layer 2).  It takes the — possibly overlapping — candidates reported
by the :doc:`Subcircuit Recognizer <subcircuit_recognizer>` and assigns each
recognized structure to its functional role, resolving ambiguity by
connectivity scoring.  It runs in two modes:

- **Topology mode**
  (:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`) —
  with a :class:`~circuitgenome.synthesizer.models.TopologyTemplate`, each
  template slot (``input_pair``, ``load``, …) is assigned its best-matching SR
  candidate, recovering the synthesizer's ``variant_map`` shape.
- **Topology-free mode**
  (:func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`) —
  without a template, structures are grouped by ``circuit_block`` and
  ``category`` using external-port adjacency as the disambiguation signal, for
  recognition of arbitrary netlists with unknown net names.

Together, SR + FBR support round-trip recognition of every topology template
the synthesizer produces.

Entry points
------------

- :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots` —
  topology-guided slot assignment.
- :func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`
  — topology-free grouping.

Functional block recognition (Layer 2)
---------------------------------------

FBR operates in two modes depending on whether a topology template is available.

Topology mode
~~~~~~~~~~~~~~

:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots` takes
SR's output plus a
:class:`~circuitgenome.synthesizer.models.TopologyTemplate` and assigns each
:class:`~circuitgenome.synthesizer.models.Slot` in ``topology.slots`` to its
best-matching SR candidate:

1. Filter SR's candidates to those whose ``category`` matches the slot's
   ``category``.
2. Score each remaining candidate by how many of its resolved ``pins`` agree
   with
   :meth:`~circuitgenome.synthesizer.models.TopologyTemplate.slot_connections`
   for that slot (the topology's static ``{port: expected global net}``
   wiring).
3. Assign the highest-scoring candidate.

Connectivity scoring runs even for categories with only one slot, since SR may
report multiple overlapping candidates per category regardless of how many slots
need that category. The output,
:class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`, is
shaped like ``variant_map`` (``{slot_name: SlotAssignment}``), plus any
unassigned candidate structures and ``unrecognized_devices`` passed through from
SR:

.. code-block:: python

   FunctionalBlockRecognitionResult(
       slot_assignments={
           "input_pair":   SlotAssignment("input_pair",   "differential_pair_pmos",   ip_struct),
           "load":         SlotAssignment("load",         "active_load_nmos",         load_struct),
           "tail_current": SlotAssignment("tail_current", "current_mirror_tail_pmos", tail_struct),
           "bias_gen":     SlotAssignment("bias_gen",     "constructed_bias",         bias_struct),
       },
       unassigned_structures=[...],   # overlapping SR candidates that lost the scoring
       unrecognized_devices=[],       # empty on a clean round trip
   )

Each ``SlotAssignment`` carries the slot name, the winning pattern's name (equal
to the synthesized variant's name on a correct round trip), and the
:class:`~circuitgenome.recognizer.models.RecognizedStructure` itself (the
``*_struct`` placeholders above).

Topology-free mode
~~~~~~~~~~~~~~~~~~~

Without a template, FBR knows neither the circuit's net names nor which structure
fills which role — it has only SR's candidates, each tagged with a
``circuit_block`` and a ``category``. ``category`` says *what kind* of block a
structure is (``input_pair``, ``load``, ...); ``circuit_block`` says *which part
of the op-amp* it belongs to — the first stage (``gain_stage_1``), a later gain
stage (``gain_stage_2``), the bias network (``bias``), compensation
(``compensation``), CMFB (``cmfb``), or the output buffer
(``output_stage_block``). Both tags are authored statically on the SR pattern in
``opamp_patterns.yaml`` (the input-pair/load/tail patterns carry
``gain_stage_1``, the second-stage patterns ``gain_stage_2``) and copied onto
every match, so they are available with no template. Stage numbers beyond the
YAML tags — ``gain_stage_3`` and up — aren't authored; Pass 3 below derives them
by splitting a block that holds several stages.

:func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`
buckets the candidates ``circuit_block`` → ``category`` and ranks the candidates
in each category so the first one is the best guess. The ranking signal is
**external-port adjacency** — how many of a structure's pins connect directly to
a subcircuit external port — which works because real functional blocks touch the
circuit's I/O and bias ports in predictable ways.

That raw signal misranks a few cases, so three passes correct it before the
result is returned.

**Pass 1 — filter out spurious gain-stage matches.** SR's patterns overlap, so a
bias transistor or an input-pair device can be re-matched by a gain-stage
pattern. FBR drops any ``gain_stage_*`` candidate whose ``in`` or ``bias`` pin
lands on an external port — a real gain stage takes its input from an internal
net (the previous stage's output), not from ``ibias`` or a signal input. In
single-category gain-stage blocks it additionally drops any candidate containing
an NMOS whose source isn't ``gnd!``, which marks a cascode intermediate device
rather than a rail-to-rail stage.

.. admonition:: Example

   A bias-reference NMOS is re-matched by the ``common_source`` pattern with its
   gate (the ``in`` pin) on the external ``ibias`` port. A real gain stage's
   ``in`` is an internal net, so the candidate is dropped.

**Pass 2 — rank the multi-category block (gain_stage_1).** ``gain_stage_1`` holds
three categories at once — ``input_pair``, ``load``, ``tail_current`` — and here
the raw external-port score is *inverted*: bias devices gate on ``ibias`` and sit
on the supply rails, so they outscore the real functional devices. FBR corrects
this in dependency order:

1. **input_pair** — ranked by the number of *distinct* external ports its
   ``in1``/``in2`` touch. The true pair has both signal inputs on two distinct
   ports (score 2); a bias mirror has ``in1 = in2 = ibias`` (score 1); a spurious
   stage pair has them on internal nets (score 0).
2. **load** — candidates with ``in1``, ``in2``, or ``bias1`` on an external port
   are dropped; among the rest, FBR prefers those whose ``in1``/``in2`` match the
   winning input pair's ``out1``/``out2`` (following the signal chain).
3. **tail_current** — candidates whose ``out`` is an external port are dropped;
   among the rest, FBR prefers the one whose ``out`` matches the input pair's
   ``tail`` net.

.. admonition:: Example

   A bias mirror pair (both gates on ``ibias``) scores 1, while the true
   differential pair (``in1``/``in2`` on two distinct signal ports) scores 2 — so
   ranking by distinct external ports lifts the real pair above the bias mirror.

**Pass 3 — split a block that holds several stages.** A multi-stage op-amp lands
its gain stages in one single-category block. FBR splits a ``gain_stage_*`` block
that still has more than one candidate into consecutive ``gain_stage_N`` groups
ordered by ascending external-port adjacency, so the stage driving the external
output ends up in the highest-numbered group.

.. admonition:: Example

   A three-stage op-amp's ``gain_stage_2`` holds two ``common_source``
   candidates. The one whose ``out`` is an internal net stays ``gain_stage_2``;
   the one whose ``out`` reaches the external output port is promoted to
   ``gain_stage_3``.

The output,
:class:`~circuitgenome.recognizer.models.CategoryGroupResult`, is the
``circuit_block → category → [candidates]`` mapping, best guess first in each
category.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/recognizer/functional_block_recognizer
