Functional Block Recognizer (FBR)
=================================

The **Functional Block Recognizer** is the second half of the recognizer
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

Together, SR + FBR support round-trip recognition of all seven topology
templates the synthesizer produces.

Entry points
------------

- :func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots` —
  topology-guided slot assignment.
- :func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`
  — topology-free grouping.

Functional block recognition (Layer 2)
---------------------------------------

FBR operates in two modes depending on whether a topology template is available:

**Topology mode** (:func:`~circuitgenome.recognizer.functional_block_recognizer.assign_slots`):
takes SR's output plus a
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
report multiple overlapping candidates per category (as the Subcircuit Recognizer does) regardless of
how many slots need that category. The output,
:class:`~circuitgenome.recognizer.models.FunctionalBlockRecognitionResult`, is
shaped like ``variant_map`` (``{slot_name: SlotAssignment}``), plus any
unassigned candidate structures and ``unrecognized_devices`` passed through
from SR.

**Topology-free mode** (:func:`~circuitgenome.recognizer.functional_block_recognizer.group_by_category`):
works on any netlist with arbitrary net names without a topology template.
Each opamp pattern carries a ``circuit_block`` annotation (``gain_stage_1``,
``gain_stage_2``, ``bias``, ``compensation``, ``cmfb``) alongside its
``category`` (``input_pair``, ``load``, ...). The ``gain_stage_N`` prefix is
distinct from category names like ``second_stage``, so the two fields never
clash. The function groups SR structures by ``circuit_block`` then
``category``, ranking candidates within each category by external-port
adjacency (count of pins that connect directly to a subcircuit external port)
as a topology-free disambiguation signal. The output,
:class:`~circuitgenome.recognizer.models.CategoryGroupResult`, gives a
``circuit_block → category → [candidates]`` mapping where the first candidate
per category is the best topology-free guess.

The topology-free algorithm runs three passes:

**Pass 1 — Filter (single-category gain_stage_* blocks only)**

Removes three classes of spurious candidates in ``gain_stage_2``, ``gain_stage_3``,
etc. (blocks with exactly one category, i.e. ``second_stage`` slots):

- **Class A** — ``in`` pin on an external port: bias-reference nmos re-matched
  with gate on ``ibias``.
- **Class B** — ``bias`` pin on an external port: pmos leg of a bias mirror
  re-matched with gate on ``ibias``.
- **Class C** — any nmos device whose source is not ``gnd!``: cascode load
  devices (source tied to an intermediate folding node) that survive the
  pin-level checks.

**Pass 2 — Multi-category ranking (gain_stage_1)**

``gain_stage_1`` holds three categories simultaneously (``input_pair``,
``load``, ``tail_current``), and the simple external-port score heuristic is
inverted for all three: bias-generation devices score higher than the real
functional devices because they connect to ``ibias`` (external bias port) and
supply rails. Pass 2 corrects this in dependency order:

1. *input_pair* — re-sorted by the count of **distinct** external ports among
   ``{in1, in2}`` as the primary key. The real differential pair has both signal
   inputs on distinct external ports (score 2); bias mirror pairs have
   ``in1 = in2 = ibias`` (score 1); spurious second/third-stage device pairs have
   ``in1``, ``in2`` on internal nets (score 0).

2. *load* — candidates with ``in1``, ``in2``, or ``bias1`` on external ports are
   dropped (spurious bias-gen matches). Among survivors, those whose ``in1``/
   ``in2`` match the top ``input_pair`` candidate's ``out1``/``out2`` are
   promoted via **signal-chain following**. The real load always receives its
   differential inputs from the input pair's drain nodes.

3. *tail_current* — candidates whose ``out`` connects to an external port are
   dropped (spurious matches driving the circuit output instead of the internal
   tail node). Among survivors, those whose ``out`` matches the top
   ``input_pair`` candidate's ``tail`` pin are promoted via signal-chain
   following.

**Pass 3 — Split (single-category gain_stage_* blocks)**

``gain_stage_*`` blocks with exactly one remaining category and more than one
candidate are split into consecutive ``gain_stage_N`` groups ordered by ascending
external-port adjacency. This enables disambiguation of a three-stage opamp's
second and third gain stages: the intermediate stage (``out`` on an internal net)
stays in ``gain_stage_2``; the final stage (``out`` connecting to the external
output port) is promoted to ``gain_stage_3``.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/recognizer/functional_block_recognizer
