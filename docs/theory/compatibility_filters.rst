Compatibility Filters
=====================

:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits` forms the
Cartesian product of every slot's candidate variants. Most of those
combinations are not circuits worth emitting: some assemble into an
electrically **non-functional** netlist (a node with no DC path, a stage that
cannot be biased), and others are exact **duplicates** that differ only in a
variant this combination never references. A set of small, pure-function
*compatibility filters* rejects those combinations before they are assembled,
one slot-level rule per filter.

.. admonition:: Example

   A self-biased ``inverter_based_input`` pair ignores its ``tail`` port, so
   all six ``tail_current`` variants would produce the identical netlist.

This page explains the electrical *why* behind each filter and links its API.
Each filter section links its module inline, and the `Per-module API
reference`_ section collects the ``automodule`` docs.

----

Two types of filter
-------------------

**Pure filters** expose a single ``is_*_compatible`` predicate that returns
``False`` for a combination that should be dropped. Assembly is skipped;
nothing else changes.

**Filter + prune pairs** handle a subtler case: a slot whose variant choice is
*irrelevant* for the rest of the combination (its output drives nothing, or
its port is never referenced). Enumerating all of that slot's variants would
produce N identical circuits. The ``is_*_compatible`` filter collapses the
choice to a single canonical variant, and the paired ``prune_*`` transform then
empties that variant's ports and devices so it contributes no dead devices and
stops "needing" its bias rail
(see :func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`).
The :ref:`CMFB <compat-cmfb>` and :ref:`tail-current <compat-tail-current>`
filters are the two filter + prune pairs.

.. admonition:: Example

   A ``load`` that does not consume ``bias_cmfb`` leaves the ``cmfb`` slot
   driving nothing, so the ``resistive_sense_cmfb`` and ``dda_cmfb`` choices
   would emit the same circuit — the filter keeps the canonical one and the
   prune empties it.

Two kinds of check
------------------

**Structural** checks inspect *actual device-terminal references* — what a
variant's transistors and resistors really connect to — and need no metadata.
They classify new variants automatically: the
:ref:`stage-interface <compat-stage-interface>`,
:ref:`compensation-parity <compat-compensation>`, and
:ref:`untapped-load-branch <compat-load-branch>` filters are structural.

**Tag-based** checks read a declared field from ``opamp_modules.yaml``
(``polarity``, ``output_cardinality``). Supporting a new variant is then a
one-line YAML tag, no code change: the :ref:`polarity <compat-polarity>`,
:ref:`output-cardinality <compat-output-cardinality>`, and the load side of
the :ref:`CMFB <compat-cmfb>` filter are tag-based.

The filters at a glance
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 26 12 12 50

   * - Filter
     - Shape
     - Check
     - Rejects / collapses
   * - :ref:`Polarity <compat-polarity>`
     - filter
     - tag
     - Combinations whose ``load``/``tail_current`` polarity contradicts the
       ``input_pair`` (a shared node left with no DC current path).
   * - :ref:`Stage-interface <compat-stage-interface>`
     - filter
     - structural
     - Stage interfaces where the next stage's required gate level falls
       outside the input pair's reachable output window (unbiasable).
   * - :ref:`Compensation parity <compat-compensation>`
     - filter
     - structural
     - Miller compensation wrapped around a non-inverting stage chain *with
       gain* (positive feedback, immeasurable AC response).
   * - :ref:`Output-cardinality <compat-output-cardinality>`
     - filter
     - tag
     - ``load`` ``output_cardinality`` that does not match the topology's
       ``output_type`` (a mandatory output port left floating).
   * - :ref:`Untapped-load-branch <compat-load-branch>`
     - filter
     - structural
     - Single-ended loads whose untapped branch node is left high-impedance
       between two series current sources (no DC definition).
   * - :ref:`CMFB <compat-cmfb>`
     - filter + prune
     - tag
     - The ``cmfb`` variant choice when the ``load`` does not consume it;
       prune empties the placeholder so rail 4 is not needed.
   * - :ref:`Tail-current <compat-tail-current>`
     - filter + prune
     - structural
     - The ``tail_current`` variant choice when the ``input_pair`` never
       references ``tail``; prune empties it so rail 7 is not needed.

Where they run
--------------

The filters run in a fixed order inside
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`, after the
variant product is formed and before the circuit is assembled: polarity →
stage-interface → compensation → output-cardinality → untapped-load-branch →
CMFB (filter, then prune) → tail-current (filter, then prune). The two prunes
must precede
:func:`~circuitgenome.synthesizer.bias_construction.construct_bias_generation`
so that emptied placeholders demand no bias rail. The synthesizer package
:doc:`README <../api/synthesizer>` and the :doc:`../overview` document the full
pipeline and the enumeration counts that these filters produce.

----

.. _compat-polarity:

Polarity compatibility filter
-----------------------------

This filter operates on the ``input_pair`` / ``load`` / ``tail_current``
combination: a circuit only has a real DC current path if the three agree on
polarity. For example, ``differential_pair_nmos``
draws current out of ``out1``/``out2`` into the tail, so it needs a ``load``
that *sources* current into ``out1``/``out2`` from vdd and a
``tail_current`` that *sinks* the tail node to gnd — pairing it with
``active_load_nmos`` (which also sinks to gnd) or ``current_mirror_tail_pmos``
(which also sources into the tail) leaves a node with no current path.

Each ``input_pair``, ``load``, and ``tail_current`` variant declares a
``polarity`` field in ``opamp_modules.yaml``: ``pmos_input``, ``nmos_input``,
or omitted for variants that work with either polarity
(``inverter_based_input``).
``enumerate_circuits`` skips any combination where ``load``'s or
``tail_current``'s ``polarity`` (if set) doesn't match ``input_pair``'s
(:mod:`~circuitgenome.synthesizer.compatibility.polarity`).

.. _compat-stage-interface:

Stage-interface compatibility filter
------------------------------------

An ``amplification_stage`` variant is structurally unbiasable against the
first stage when the gate level its *signal device* (the transistor whose gate
is the ``in`` port) requires falls outside the input pair's reachable output
window: an NMOS pair confines its output node to the upper part of the
supply range (its floor is the tail node, and vdd-referenced loads confine
it further), a PMOS pair mirrors that low — when the required level and
the window are disjoint, no sizing can establish the interface DC level
(mirror-type loads let the feedback loop drag the node to the boundary and
pin the pair in triode; range-limited loads rail outright).

The required level follows from the signal device's *source terminal*: a
common-source stage (source on a supply) puts the gate one ``V_GS`` from that
supply, so it suits the **opposite**-polarity pair — an NMOS pair's high output
suits a PMOS-gate CS stage, and vice versa.

``enumerate_circuits`` therefore skips any combination where an
``amplification_stage``-category slot whose ``in`` net is one of the load's
output nets (``load.out``/``out1``/``out2``) requires a pair type other than
the ``input_pair``'s — that is the second (gm2) stage, the one wired directly
to the load output. The check is structural (which device gates ``in`` and
where its source sits — no YAML tags), so new ``amplification_stage`` variants
are classified automatically. The 3-stage templates' ``third_stage`` slot
senses the *second* stage's output instead — a wide-swing common-source node
that can meet either gate level — and is deliberately left unconstrained, as
are combinations using the untagged ``inverter_based_input`` (its output level
sits near mid-rail, reachable by either gate type). Source followers now live
in the ``output_stage`` category, wired after the gain stages (their ``in`` is
``net_ampout``, never a load-output net), so this filter does not apply to them
(:mod:`~circuitgenome.synthesizer.compatibility.second_stage`).

.. _compat-compensation:

Compensation parity filter
--------------------------

Every ``compensation`` variant couples its ``in`` port to its ``out`` port
through a capacitor (Miller family). Wired across a stage chain, that
coupling is *negative* feedback — pole splitting — only when the chain is
inverting; around a non-inverting chain *with gain* the same capacitor is
positive feedback, and the AC response develops a right-half-plane
character whose gain/GBW/PM cannot be measured (issue #114:
``differential_ota_second_stage``, two cascaded common-source stages,
measured PM 270–281°).

A chain's parity is its number of common-source inversions — each
gate-to-drain hop inverts. ``enumerate_circuits`` skips only combinations
where a ``compensation`` slot wraps a chain whose total inversion count is a
*positive even* number. The check composes across slots: in the NMC
3-stage topologies ``comp1`` wraps the second+third stage cascade, so two
common-source stages (non-inverting composite with gain) are rejected —
standard nested-Miller sign structure requires a non-inverting second
stage and an inverting output stage. The check is structural (device
terminal walks, no YAML tags), so new ``amplification_stage`` and
``compensation`` variants are classified automatically; anything
unclassifiable imposes no constraint. Source followers (``output_stage``
slots in the buffered topologies) sit after the gain stages and outside the
compensation wrap, so they are not part of any chain this filter checks
(:mod:`~circuitgenome.synthesizer.compatibility.compensation`).

.. _compat-output-cardinality:

Output-cardinality compatibility filter
---------------------------------------

Some ``load`` variants declare a *mandatory* output-side port that only one
``output_type`` wires, so pairing them with the other kind of topology would
leave that port floating:

- ``folded_cascode_load_*_input_single_output`` and
  ``telescopic_cascode_load_{pmos,nmos}`` declare ``out`` as mandatory, which
  only ``single_ended`` topologies wire.
- ``folded_cascode_load_*_input_differential_output`` declare ``out1``/``out2``
  as mandatory, which only ``fully_differential`` topologies wire.

These loads carry an ``output_cardinality`` field in ``opamp_modules.yaml`` —
``"single"`` or ``"differential"`` — and ``enumerate_circuits`` skips any
combination where it doesn't match the topology's ``output_type``. Untagged
loads (``output_cardinality: None``, the resistor/active loads) work with
either output type. ``current_source_load_{pmos,nmos}`` also carry the
``"differential"`` tag, for an *electrical* reason rather than a port-wiring
one: their branch devices are plain current sources gated by ``bias_cmfb``, so
the load only has a defined operating point when the CMFB loop drives that
gate, which only ``fully_differential`` topologies provide (issue #112)
(:mod:`~circuitgenome.synthesizer.compatibility.output`).

.. _compat-load-branch:

Untapped-load-branch compatibility filter
-----------------------------------------

In a ``single_ended`` topology only one of the first stage's two branch nodes
is tapped (``load.out``/``out2`` → the stage-output net); the other
(``load.in1``/``out1``, ``net_diff1``) is untapped — nothing outside the first
stage senses or drives it, so its DC voltage must be defined by the load
itself. ``current_source_load_*`` put a plain current source on that branch
(both gates on a single shared node, no diode connection), leaving the node
high-impedance between two series current sources — the load device on one
side, the input-pair half plus tail on the other. No sizing can absorb the
inevitable current mismatch, and one device always leaves saturation
(issue #112), so ``enumerate_circuits`` skips these combinations.

The check is structural (no YAML tags): the ``in1`` node counts as DC-defined
when the load puts a diode-connected MOSFET on it (``active_load_*``), a
resistor touching it (``resistor_load_*``), or a MOSFET source terminal on it
(the cascode loads' folding/cascode devices). Only the bare rail-referenced
current source fails all three.

``fully_differential`` topologies tap both branches, so this filter does not
constrain them — defining the output common mode there is the CMFB loop's job.

With the current module library the filter prunes nothing on its own:
``current_source_load_*`` are already excluded from single-ended templates by
their ``output_cardinality`` tag, so it stands as the structural guard for any
future rail-gated load branch
(:mod:`~circuitgenome.synthesizer.compatibility.load_branch`).

.. _compat-cmfb:

CMFB compatibility filter
-------------------------

``fully_differential`` topologies have a ``cmfb`` slot, wired
``cmfb.out -> net_cmfb_out -> load.bias_cmfb``. Of the 14 ``load`` variants,
only the 4 tagged ``output_cardinality: "differential"`` declare
``bias_cmfb`` as a real ``role: input`` consumer:
``folded_cascode_load_*_input_differential_output`` (gating ``mn3``/``mn4``
or ``mp1``/``mp2``) and ``current_source_load_{pmos,nmos}`` (gating both
branch devices; issue #112). The other 8 declare it ``role: optional`` and
never reference it, so ``net_cmfb_out`` would drive nothing.

For a ``load`` whose ``output_cardinality`` isn't ``"differential"``, only the
canonical ``resistive_sense_cmfb`` variant is allowed through -- the
``dda_cmfb`` choice would otherwise be enumerated as a duplicate no-op
circuit. That canonical variant is then pruned to an empty placeholder (no
ports, no devices), so it contributes no devices to the assembled circuit and
``cmfb.bias`` is no longer counted as a needed bias rail. The
``vcm_ref`` external port (statically present on every ``fully_differential``
topology) is left unconnected for these circuits
(:mod:`~circuitgenome.synthesizer.compatibility.cmfb`).

.. _compat-tail-current:

Tail-current compatibility filter
---------------------------------

Every topology has a ``tail_current`` slot, wired ``input_pair.tail ->
net_tail <- tail_current.out``. Of the 5 ``input_pair`` variants, only the 4
``differential_pair_*`` variants reference their ``tail`` port from a device
terminal (``s``/``b: tail`` on the tail transistor, or ``t2: tail`` on the
degenerated variants' tail resistor). ``inverter_based_input`` -- two
back-to-back CMOS inverters -- is self-biased by design and never references
``tail``, so without this filter ``net_tail`` would be a floating,
single-terminal node and ``tail_current`` would drive nothing.

For an ``input_pair`` that doesn't reference ``tail``, only the canonical
``current_mirror_tail_pmos`` variant is allowed through -- the other 5
``tail_current`` choices would otherwise be enumerated as duplicate no-op
circuits. That canonical variant is then pruned to an empty placeholder (no
ports, no devices), so it contributes no devices to the assembled circuit,
``net_tail`` is no longer floating, and ``tail_current.bias`` is no longer
counted as a needed bias rail
(:mod:`~circuitgenome.synthesizer.compatibility.tail_current`).

.. admonition:: Example

   ``inverter_based_input`` yields a single circuit per ``load`` instead of
   six identical ones: whichever ``tail_current`` was chosen, the prune leaves
   the same tail-less netlist.

----

Per-module API reference
------------------------

Signatures and members for each filter module. The electrical rationale is in
the sections above; these pages are the API surface (import from the
``compatibility`` subpackage, not the individual modules).

.. toctree::
   :maxdepth: 1

   ../api/compatibility/polarity
   ../api/compatibility/stage_interface
   ../api/compatibility/compensation
   ../api/compatibility/output
   ../api/compatibility/load_branch
   ../api/compatibility/cmfb
   ../api/compatibility/tail_current
