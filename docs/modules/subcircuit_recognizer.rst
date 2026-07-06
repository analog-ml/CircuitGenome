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
entry's variant name. The library covers every reachable ``one_stage_opamp``
and ``two_stage_opamp_single_ended`` variant -- 36 patterns across seven
categories:

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Category
     - Patterns (count)
     - Notes
   * - ``input_pair``
     - 5
     - ``differential_pair_{nmos,pmos}``, degenerated variants (NMOS+NMOS /
       PMOS+PMOS transistors + 2 source-degeneration resistors),
       ``inverter_based_input`` (2 CMOS inverters: 2 PMOS + 2 NMOS).
   * - ``load``
     - 14
     - Resistor (VDD-side / GND-side), active current mirror (PMOS / NMOS),
       current-source (PMOS / NMOS), single-output folded cascode (NMOS-input /
       PMOS-input, 8 devices each), telescopic cascode (PMOS / NMOS, 6 devices
       each) in self-biased and wide-swing/Sooch flavours (the latter drive
       the mirror cascode gates from a ``bias2`` level rail, dropping the
       output floor from ``Vgs+Vdsat`` to ``2*Vdsat`` -- issue #129). Plus 2
       differential-output folded-cascode variants
       (``folded_cascode_load_{nmos,pmos}_input_differential_output``, 8 devices
       each) used exclusively by ``two_stage_opamp_fully_differential``.
   * - ``tail_current``
     - 6
     - Current mirror (PMOS / NMOS, 2 devices each), cascode current mirror
       (PMOS / NMOS, 4 devices each), resistor (VDD-side / GND-side, each using
       a hook to reject resistors whose supply terminal isn't the global rail).
   * - ``bias_generation``
     - 4
     - ``constructed_bias`` (the synthesizer's constructed multi-reference
       generator; its hook discovers NMOS-referenced legs, the ``pref``
       branch, and every PMOS-referenced leg), plus the historical
       ``diode_connected_mosfet_bias`` (NMOS reference + NMOS/PMOS leg
       pairs), ``magic_battery_bias`` (PMOS reference + PMOS/NMOS leg
       pairs), and ``resistor_bias`` (PMOS reference + PMOS/resistor leg
       pairs) for external/legacy netlists. All four use hooks (below) to
       discover however many output legs are present.
   * - ``cmfb``
     - 2
     - ``resistive_sense_cmfb`` (2 resistors + 5T OTA: resistive averager feeds
       a differential pair whose output mirrors onto ``out``),
       ``dda_cmfb`` (differential-difference amplifier: 4 NMOS + 2 PMOS + 2 NMOS
       tails, two input pairs sharing a diode-connected PMOS mirror). Both use
       ``{in1, in2, vref, bias, out}`` pins. Present only when ``load`` has
       ``output_cardinality: "differential"``; otherwise pruned to ``cmfb_absent``.
   * - ``compensation``
     - 3
     - ``miller_cap`` (1 capacitor across ``in``→``out``),
       ``miller_cap_with_nulling_resistor`` (series resistor + capacitor, sharing
       an internal ``cn`` node), ``indirect_compensation`` (capacitor to an
       internal ``ind`` node + series resistor to ``out``). Connectivity scoring
       naturally disambiguates overlapping 1-device subsets without hooks.
   * - ``second_stage``
     - 7
     - ``common_source`` (NMOS input + PMOS load, drains shorted to ``out``),
       ``common_source_pmos`` (the mirror image, PMOS input + NMOS sink;
       structurally identical to ``common_source`` with the ``in``/``bias``
       gate roles swapped, so both patterns match the same device pair and
       FBR's connectivity scoring picks the one whose ``in`` pin lands on
       the stage-input net), ``common_drain`` (PMOS source follower + PMOS
       current source; the follower's source/bulk tie and the source's
       bulk-on-vdd keep it disjoint from the CS and OTA shapes),
       ``common_drain_nmos`` (NMOS source follower + NMOS sink; the
       follower's source is the sink's drain, all bulks on gnd),
       ``differential_ota_second_stage`` (2 PMOS + 2 NMOS, cross-coupled via
       an internal ``d1`` node; parked as ``unsupported`` for synthesis,
       issue #114 -- the pattern still serves external netlists),
       ``noninverting_stage_nmos``/``noninverting_stage_pmos`` (2 PMOS + 2
       NMOS non-inverting current-mirror gain stages, issue #139; the
       diode-connected mirror master on the internal mirror node makes each
       non-isomorphic to the OTA shape).

:func:`~circuitgenome.recognizer.subcircuit_recognizer.recognize` matches
every pattern against the netlist's devices via a small backtracking search
(patterns are 1-4 devices, so no graph library is needed), filtering
candidates by device type and checking ``same_net``. A pattern's ``hook``, if
any, runs once per base-template match and may reject the match (return
``None``) or accept it with extra devices/pins merged in (a
:class:`~circuitgenome.recognizer.models.HookMatch`).

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

SR pattern coverage
--------------------

The pattern library spans every topology the synthesizer produces, broken down
below by where each pattern is introduced:

- **one_stage_opamp**: 24 patterns (5 ``input_pair`` × 10 ``load`` × 6 real
  ``tail_current`` × 3 ``bias_generation``). The round-trip test is
  parametrized over 11 representative combinations covering every variant.
- **two_stage_opamp_single_ended**: adds 8 new patterns (3 ``compensation`` +
  5 ``second_stage``). The round-trip test adds 11 further combinations
  covering all 5 ``second_stage`` variants against every stage-interface-
  compatible ``input_pair`` polarity, all 3 ``compensation`` variants, and
  all 5 ``input_pair`` variants.
- **two_stage_opamp_fully_differential**: adds 4 new patterns — 2
  differential-output ``load`` variants
  (``folded_cascode_load_{nmos,pmos}_input_differential_output``) and 2
  ``cmfb`` variants (``resistive_sense_cmfb``, ``dda_cmfb``). FBR's
  ``assign_slots`` was also fixed to exclude already-assigned candidates when
  processing same-category slot pairs (``comp_p``/``comp_n`` and
  ``second_stage_p``/``second_stage_n``). The round-trip test adds 11 further
  combinations covering both ``cmfb`` variants, all ``compensation`` and
  ``second_stage`` pairings, and both differential input-pair polarities.
- **three_stage_opamp_nmc_single_ended** and
  **three_stage_opamp_rnmc_single_ended**: the plain NMC scheme adds 2 new
  ``amplification_stage`` patterns — the non-inverting current-mirror stages
  ``noninverting_stage_{nmos,pmos}`` (issue #139) that fill the NMC gm2 slot
  — which the recognizer tells apart from the CS gm3 stage (and from
  ``differential_ota_second_stage``, whose mirror node differs) by graph
  structure. The ``third_stage`` slot otherwise reuses the
  ``amplification_stage`` category; ``comp1``/``comp2`` reuse the
  ``compensation`` category. FBR's ``assigned_ids`` mechanism correctly
  disambiguates 2 same-category gain slots and 2 same-category
  ``compensation`` slots via connectivity scoring on the distinct
  intermediate nets (``net_mid1``/``net_mid2``).
- **three_stage_opamp_nmc_fully_differential** and
  **three_stage_opamp_rnmc_fully_differential**: no new patterns needed.
  Each path (p/n) has independent ``second_stage``/``third_stage`` and
  ``comp1``/``comp2`` slots — 4 slots per category. FBR correctly assigns
  all 4 same-category ``compensation`` slots and all 4 same-category
  ``second_stage`` slots via connectivity scoring on per-path distinct nets
  (``net_loadout1``/``net_loadout2``, ``net_mid2_p``/``net_mid2_n``,
  ``outp``/``outn``). 11 round-trip combos each.

All 73 test combos assert ``unrecognized_devices == []`` and full
``variant_map`` recovery. Combos are chosen so every variant appears in at
least one and every selected combo is structurally unambiguous for the SR/FBR
pipeline. Known structural ambiguities -- ``resistor_bias`` paired with
``current_mirror_tail_{nmos,pmos}`` (the tail's diode-connected reference
transistor spuriously satisfies the ``magic_battery_bias`` NMOS leg template)
and any ``magic_battery_bias`` or ``resistor_bias`` combination where
bias-rail pruning reduces the ``bias_generation`` slot to 0 legs (making the
two variants structurally identical) -- are avoided by careful combo selection
rather than additional code. Primitive/multi-level pattern composition and topology identification from an
arbitrary netlist are deferred to later milestones.

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/recognizer/netlist_parser
   ../api/recognizer/subcircuit_recognizer
   ../api/recognizer/hooks
   ../api/recognizer/models
