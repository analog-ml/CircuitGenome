Subcircuit Recognizer
=====================

The **Subcircuit Recognizer (SR)** is the first structural half of the recognizer
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

The recognizer is deliberately split into two modules.  SR is **structural and
circuit-agnostic** — a differential pair or a current mirror is the same shape
whether it sits in an op-amp, a comparator, or a DAC — so it matches building
blocks and reports **all** candidates (including overlapping ones) without
picking a winner.  The
:doc:`Functional Block Recognizer <functional_block_recognizer>` (FBR) then adds
the **circuit-specific** interpretation, assigning each block its functional
role and resolving the overlaps; that semantic layer is op-amp-only in this
version.  Keeping the structural layer separate lets it be reused for other
circuit families as they are added.  Constraints that resist a declarative
pattern are handled by `hooks <Hooks_>`_.

Entry points
------------

- :func:`~circuitgenome.recognizer.netlist_parser.parse` — flat SPICE → parsed
  netlist.
- :func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize` — parsed
  netlist → recognized structures.

Netlist parsing (Layer 0)
--------------------------

:func:`~circuitgenome.recognizer.netlist_parser.parse` is the structural
inverse of :func:`~circuitgenome.synthesizer.netlist.to_flat_spice`: it reads
a ``.subckt <name> <port...>`` / ``.ends`` block with one MOSFET device line
per device,

.. code-block:: text

   {ref} {d} {g} {s} {b} {nmos|pmos}

and produces a :class:`~circuitgenome.recognizer.models.ParsedNetlist`. Net
and ref names are treated as arbitrary strings -- the parser makes no
assumptions about ``to_flat_spice``'s own naming conventions. Resistor lines
(``r<ref> <t1> <t2> <value>``) and capacitor lines (``c<ref> <p> <m>
<value>``) are also handled; the leading character of ``ref`` determines the
device type (``r`` → ``resistor``, ``c`` → ``capacitor``).

Subcircuit recognition (Layer 1)
---------------------------------

The SR pattern library
(``circuitgenome/recognizer/config/subcircuit_patterns.yaml``, loaded by
:func:`~circuitgenome.recognizer.subcircuit_recognizer.load_patterns`) is a
list of small template graphs. Each pattern declares:

- ``devices`` -- typed template slots (``nmos``/``pmos``), e.g. ``m1``, ``m2``.
- ``same_net`` -- terminal-equality constraints between slots, e.g.
  ``[m1.s, m2.s]`` ("``m1``'s source and ``m2``'s source must be the same
  net"); unlisted terminals are unconstrained.
- ``pins`` -- named nets exported by the pattern, e.g. ``in1: m1.g``.
- ``tech_type_from`` -- which template device's matched type (``"n"``/``"p"``)
  becomes the recognized structure's ``tech_type``.
- an optional ``hook`` -- a ``"module:function"`` extra-check for constraints
  too awkward to express declaratively.

Composite patterns correspond 1:1 to an ``opamp_modules.yaml`` module variant
and reuse its name, so a successful match's
:attr:`~circuitgenome.recognizer.models.RecognizedStructure.name` is directly
comparable to a
:attr:`~circuitgenome.synthesizer.models.SynthesizedCircuit.variant_map`
entry's variant name. The library covers every module variant across the
templates the synthesizer produces -- 43 patterns across eight categories:

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Category
     - Patterns (count)
     - Notes
   * - ``input_pair``
     - 5
     - - ``differential_pair_{nmos,pmos}``.
       - degenerated variants (NMOS+NMOS / PMOS+PMOS transistors + 2
         source-degeneration resistors).
       - ``inverter_based_input`` (2 CMOS inverters: 2 PMOS + 2 NMOS).
   * - ``load``
     - 14
     - - resistor (VDD-side / GND-side).
       - active current mirror (PMOS / NMOS).
       - current-source (PMOS / NMOS).
       - single-output folded cascode (NMOS-input / PMOS-input, 8 devices each).
       - telescopic cascode (PMOS / NMOS, 6 devices each), self-biased and
         wide-swing/Sooch flavours — the latter drive the mirror cascode gates
         from a ``bias2`` level rail, dropping the output floor from
         ``Vgs+Vdsat`` to ``2*Vdsat`` (issue #129).
       - differential-output folded cascode
         (``folded_cascode_load_{nmos,pmos}_input_differential_output``, 8
         devices each), used only by ``two_stage_opamp_fully_differential``.
   * - ``tail_current``
     - 8
     - - current mirror (PMOS / NMOS, 2 devices each).
       - cascode current mirror (PMOS / NMOS, 4 devices each).
       - stacked-diode cascode current mirror (PMOS / NMOS) — parked
         ``bias_infeasible`` (issue #111), kept for round-trips.
       - resistor (VDD-side / GND-side) — each uses a hook to reject resistors
         whose supply-side terminal isn't the global rail.
   * - ``bias_generation``
     - 4
     - - ``constructed_bias`` — the synthesizer's constructed multi-reference
         generator; its hook discovers NMOS-referenced legs, the ``pref``
         branch, and every PMOS-referenced leg.
       - ``diode_connected_mosfet_bias`` — historical: NMOS reference +
         NMOS/PMOS leg pairs.
       - ``magic_battery_bias`` — historical: PMOS reference + PMOS/NMOS leg
         pairs.
       - ``resistor_bias`` — historical: PMOS reference + PMOS/resistor leg
         pairs.

       All four use hooks (below) to discover however many output legs are
       present.
   * - ``cmfb``
     - 2
     - - ``resistive_sense_cmfb`` — 2 resistors + 5T OTA (resistive averager
         feeds a differential pair whose output mirrors onto ``out``).
       - ``dda_cmfb`` — differential-difference amplifier (4 NMOS + 2 PMOS + 2
         NMOS tails; two input pairs sharing a diode-connected PMOS mirror).

       Both use ``{in1, in2, vref, bias, out}`` pins. Present only when ``load``
       has ``output_cardinality: "differential"``; otherwise pruned to
       ``cmfb_absent``.
   * - ``compensation``
     - 3
     - - ``miller_cap`` — 1 capacitor across ``in``→``out``.
       - ``miller_cap_with_nulling_resistor`` — series resistor + capacitor
         sharing an internal ``cn`` node.
       - ``indirect_compensation`` — capacitor to an internal ``ind`` node +
         series resistor to ``out``.

       Connectivity scoring naturally disambiguates the overlapping 1-device
       subsets without hooks.
   * - ``amplification_stage``
     - 5
     - - ``common_source`` — NMOS input + PMOS load, drains shorted to ``out``.
       - ``common_source_pmos`` — the mirror image (PMOS input + NMOS sink);
         matches the same device pair as ``common_source`` with the
         ``in``/``bias`` gate roles swapped, so FBR's connectivity scoring picks
         the one whose ``in`` pin lands on the stage-input net.
       - ``differential_ota_second_stage`` — 2 PMOS + 2 NMOS, cross-coupled via
         an internal ``d1`` node; parked ``unsupported`` for synthesis
         (issue #114), the pattern still serves external netlists.
       - ``noninverting_stage_{nmos,pmos}`` — 2 PMOS + 2 NMOS non-inverting
         current-mirror gain stages (issue #139); the diode-connected mirror
         master makes each non-isomorphic to the OTA shape.
   * - ``output_stage``
     - 2
     - - ``common_drain`` — PMOS source follower + PMOS current source (the
         follower's source/bulk tie and the source's bulk-on-vdd keep it
         disjoint from the CS and OTA shapes).
       - ``common_drain_nmos`` — NMOS source follower + NMOS sink (the
         follower's source is the sink's drain, all bulks on gnd).

       Fills the ``output_stage`` slot of the ``*_buffered_*`` topologies.

:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize` matches
every pattern against the netlist's devices via a small backtracking search
(patterns are 1-4 devices, so no graph library is needed), filtering
candidates by device type and checking ``same_net``. A pattern's optional
``hook`` can further constrain or extend each match — see `Hooks`_ below.

Hooks
~~~~~

A **hook** is a Python callback attached to a pattern for constraints too
awkward to express in the declarative YAML. It runs once per base-template
match and either **rejects** the match (returns ``None``) or **accepts** it,
optionally merging in extra devices and pins (a
:class:`~circuitgenome.recognizer.models.HookMatch`). This lets a small fixed
template match a variable-size structure — e.g. a bias generator with any
number of output legs — or apply a guard a purely declarative pattern cannot
express.

Six hooks are implemented in :mod:`circuitgenome.recognizer.hooks`:

- :func:`~circuitgenome.recognizer.hooks.constructed_bias_legs` discovers
  the constructed generator's legs (NMOS-referenced pairs, the ``pref``
  branch, gnd-referenced / current / resistor legs off the PMOS-side
  reference), rejecting purely NMOS-referenced shapes so they resolve to
  the historical pattern below.
- :func:`~circuitgenome.recognizer.hooks.diode_connected_mosfet_bias_legs`,
  :func:`~circuitgenome.recognizer.hooks.magic_battery_bias_legs`, and
  :func:`~circuitgenome.recognizer.hooks.resistor_bias_legs` each handle a
  historical single-flavor generator: the shared reference device is always
  present, but the number of output "legs" (0-7) varies per netlist. The
  base template matches only the reference device; the hook walks the
  netlist to find however many legs are actually present and appends their
  devices and ``legN_out`` pins to
  :class:`~circuitgenome.recognizer.models.HookMatch`.
- :func:`~circuitgenome.recognizer.hooks.resistor_tail_vdd_check` and
  :func:`~circuitgenome.recognizer.hooks.resistor_tail_gnd_check` each accept a
  single-resistor ``tail_current`` match only if the resistor's supply-side
  terminal is the global ``vdd!``/``gnd!`` rail, preventing the unconstrained
  1-device template from spuriously matching every resistor in the netlist.

Recognition result
~~~~~~~~~~~~~~~~~~~

The result, a
:class:`~circuitgenome.recognizer.models.SubcircuitRecognitionResult`, may
contain **multiple overlapping candidates** for the same device(s) -- SR does
not pick a winner. For example, ``current_mirror_tail_nmos`` and
``diode_connected_mosfet_bias`` share the same 2-terminal diode-connected
shape, so a single diode-connected NMOS may match the base template of both
patterns; disambiguation is FBR's job.
``unrecognized_devices`` lists any device matched by no pattern -- for a
netlist produced from a known ``SynthesizedCircuit`` with full pattern
coverage, this should be empty.

That diode-connected-NMOS overlap surfaces as two structures that both claim
the same device (fields abbreviated for illustration):

.. code-block:: python

   SubcircuitRecognitionResult(
       structures=[
           ...,
           RecognizedStructure(name="current_mirror_tail_nmos",
                               category="tail_current", tech_type="n",
                               pins={"out": "net_tail", "bias": "net_bias7"},
                               devices=[m5, m6]),
           RecognizedStructure(name="diode_connected_mosfet_bias",
                               category="bias_generation", tech_type="n",
                               pins={"ref": "net_bias7"},
                               devices=[m5]),          # m5 also claimed above
       ],
       unrecognized_devices=[],                        # full coverage
   )

SR pattern coverage
--------------------

The pattern library spans every topology the synthesizer produces. The table
below has two columns worth spelling out:

- **New patterns** — the patterns a template is the *first* to require. Counts
  are incremental (each row adds to the rows above it), so they sum to the
  full library.
- **Round-trip combos** — parametrized test cases that synthesize a circuit,
  flatten it to SPICE, run it back through SR + FBR, and assert the original
  ``variant_map`` is recovered exactly.

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Template
     - New patterns
     - Round-trip combos
   * - ``one_stage_opamp``
     - 27 — 5 ``input_pair``, 10 single-ended ``load``, 8 ``tail_current``
       (6 default + 2 parked stacked-cascode), 4 ``bias_generation``
     - 11
   * - ``two_stage_opamp_single_ended``
     - +6 — 3 ``compensation``, 3 ``amplification_stage``
     - 11
   * - ``two_stage_opamp_fully_differential``
     - +6 — 4 differential ``load`` (2 diff-output folded cascode + 2
       ``current_source_load_*``), 2 ``cmfb``
     - 13
   * - ``three_stage_opamp_{nmc,rnmc}_single_ended``
     - +2 — ``noninverting_stage_{nmos,pmos}`` (the NMC gm2 stage, issue #139)
     - 10
   * - ``three_stage_opamp_{nmc,rnmc}_fully_differential``
     - none — reuses existing patterns per output path
     - 8
   * - ``*_buffered_*``
     - +2 — ``output_stage`` followers ``common_drain``/``common_drain_nmos``
       (issue #125)
     - within the 2-/3-stage rows

That is 43 patterns and 53 template combos; with 2 opt-in stacked-diode
cascode-tail round-trips (``include_infeasible``) it totals **55**. All 55
assert ``unrecognized_devices == []`` and full ``variant_map`` recovery.

**Combos are chosen to be unambiguous.** Every variant appears in at least one,
and each selected combo is structurally unambiguous for the SR/FBR pipeline, so
the two known structural ambiguities are sidestepped by combo selection rather
than extra code:

- ``resistor_bias`` paired with ``current_mirror_tail_{nmos,pmos}`` — the
  tail's diode-connected reference transistor spuriously satisfies the
  ``magic_battery_bias`` NMOS leg template.
- any ``magic_battery_bias`` or ``resistor_bias`` combination where bias-rail
  pruning reduces the ``bias_generation`` slot to 0 legs, making the two
  variants structurally identical.

Primitive/multi-level pattern composition and topology identification from an
arbitrary netlist are deferred to later milestones.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/recognizer/netlist_parser
   ../api/recognizer/subcircuit_recognizer
   ../api/recognizer/hooks
   ../api/recognizer/models
