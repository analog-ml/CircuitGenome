Topology Synthesizer
====================

The **Topology Synthesizer (SYN)** constructs op-amp circuits from modular functional
building blocks and emits SPICE netlists.  It models an op-amp as a composition
of **module slots** (input pair, load, tail current, bias, compensation,
amplification/output stage); each slot is filled by a concrete **module
variant**, and :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`
iterates over every valid combination, wiring them together according to a
**topology template**.  Cross-slot *compatibility filters* prune combinations
that are non-functional or electrically invalid, so the enumeration yields only
structurally sound circuits — thousands of them, for dataset generation, design
exploration, or topology studies.

The :doc:`../overview` gives the higher-level tour — the module summary, the
category figures, and the supported-template list with per-template circuit
counts.  This page holds the full detail: the complete variant catalogue, the
enumeration and compatibility analysis, the demand-driven bias construction,
the three-stage compensation schemes, the modular interface contract, and the
SPICE output formats.

Entry points
------------

- :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits` — generate
  every valid circuit for a topology template.
- :func:`~circuitgenome.synthesizer.synthesizer.synthesize` — build a single
  circuit from an explicit variant map.
- :func:`~circuitgenome.synthesizer.netlist.to_flat_spice` /
  :func:`~circuitgenome.synthesizer.netlist.to_hierarchical_spice` — export a
  :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit` to SPICE.

Module categories
-----------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Category
     - Variants
   * - Input pair
     - PMOS differential pair, NMOS differential pair, PMOS with source
       degeneration, NMOS with source degeneration, inverter-based (parked
       as ``unsupported``, issue #113: no fixed-Vgs sizing path yet)
   * - Load
     - Resistor (VDD-side / GND-side), PMOS active (current mirror), NMOS
       active (current mirror), PMOS/NMOS current source, folded cascode
       (PMOS/NMOS-input, single-output & differential-output), telescopic
       cascode (PMOS/NMOS, self-biased & wide-swing/Sooch — issue #129)
   * - Tail current
     - Current mirror (PMOS/NMOS), cascode current mirror (PMOS/NMOS),
       resistor (VDD-side / GND-side)
   * - Bias generation
     - No enumerated variants: constructed per combination from consumer
       demands (see "Demand-driven bias construction" below) -- an NMOS
       master reference on ``ibias`` plus one typed leg per consumed rail
       (rails 1-4 for ``load``, rail 5 for ``second_stage``, rail 6 for
       ``third_stage``, rail 7 for ``tail_current``)
   * - CMFB
     - Resistive-sense 5T OTA, differential-difference amplifier (DDA) --
       senses the load's first-stage differential outputs
       (``net_diff1``/``net_diff2``) against an external ``vcm_ref`` and
       drives the ``bias_cmfb`` input of the differential-output cascode
       loads and the ``current_source_load_*`` variants.
       Present only when ``load``'s ``output_cardinality`` is
       ``"differential"``; otherwise pruned to an empty placeholder (see
       the :ref:`CMFB compatibility filter <compat-cmfb>`) and ``vcm_ref``
       is left unconnected.
   * - Compensation
     - Miller capacitor, Miller cap with nulling resistor, indirect
       compensation
   * - Amplification stage
     - Common-source (NMOS/PMOS); non-inverting current-mirror stage
       (NMOS/PMOS input, issue #139: a CS input device driving a
       current-mirror active load — non-inverting *with* a single dominant
       pole, so it fills the NMC gm2 slot that needs a non-inverting stage);
       differential OTA (parked as ``unsupported``, issue #114: actually two
       cascaded common-source stages — non-inverting but with an in-band
       second pole). Fills the ``second_stage``/``third_stage`` (and
       ``_p``/``_n``) topology slots.
   * - Output stage
     - Common-drain source follower (PMOS/NMOS). Fills the ``output_stage``
       (and ``_p``/``_n``) slots of the ``*_buffered_*`` topologies only; a
       follower is A2 ≈ 1 (a buffer, not a gain stage) and is excluded from
       the gain product, so a buffered circuit's ``gain_db`` equals its
       unbuffered sibling's.

Enumeration and compatibility
-----------------------------

Each ``*_buffered_*`` template is the plain template with a source-follower
``output_stage`` slot inserted after the amplification stage: the amplification
stage now drives ``net_ampout`` (``_p``/``_n``) and compensation re-points
there, while the follower drives the final output.

The 5th ``input_pair`` variant, ``inverter_based_input``, is parked with an
``unsupported:`` reason tag (issue #113): it is self-biased — its quiescent
current is set by W/L at the gate voltage the wiring pins to Vcm, not by
``spec.ibias`` — and the gm/Id sizer has no fixed-Vgs sizing path for it, so
``enumerate_circuits`` drops it from the candidate pool (pass
``config={"include_unsupported": True}`` to enumerate it anyway, e.g. for
recognizer round-trips). The 5th ``second_stage`` variant,
``differential_ota_second_stage``, is parked the same way (issue #114):
despite its name it is two cascaded common-source stages, so its ``in`` →
``out`` composite is *non-inverting* — Miller-family compensation around it
is positive feedback (a right-half-plane AC response whose gain/GBW/PM
cannot be measured; see the :ref:`compensation parity filter
<compat-compensation>`) — and its
internal ``d1`` node is a second gain stage/pole that the sizer's
single-gm2 stage model cannot see. The non-inverting role that NMC needs for
gm2 is instead filled by the *enumerable* ``noninverting_stage_{nmos,pmos}``
variants (issue #139): also non-inverting with gain, but their second
inversion is a current mirror whose pole sits at a low-Z diode node (out of
band), so the single-gm2 model holds and the DC bias closes. The two source
followers ``common_drain``
and ``common_drain_nmos`` (issue #125) are **not** parked: they moved out of
the amplification pool into the new ``output_stage`` category and enumerate
in the ``*_buffered_*`` templates, where a follower fills the ``output_stage``
(``_p``/``_n``) slot after the amplification stage. A follower is A2 ≈ 1 (a
buffer, not a gain stage), so it is excluded from the gain product — a
buffered circuit's ``gain_db`` equals its unbuffered sibling's — and it can no
longer occupy a ``second_stage``/``third_stage`` gain slot.

A softer tag, ``bias_infeasible:``, marks a variant whose wiring is
*functionally correct* but whose DC bias does not close under the normal
supply/Vcm headroom of the default (low-voltage) spec class — currently the
two ``stacked_cascode_current_mirror_tail_*`` variants (issue #111). A
stacked-diode cascode mirror pins its output cascode's source a full
``|Vgs|`` from the rail, so the tail node needs ``|Vgs|+Vdsat`` (~1.3 V at
gf180) of compliance versus the wide-swing ``cascode_current_mirror_tail_*``'s
``2·Vdsat`` (~0.35 V). Unlike an ``unsupported`` variant it builds into a
complete, valid netlist (it self-biases its cascode gate, so it consumes only
rail 7 and needs no rail 8) and would size normally; it is simply predicted to
be rejected at the DC bias gate. ``enumerate_circuits`` drops it by default and
keeps it only with ``config={"include_infeasible": True}`` (CLI:
``--include-infeasible``) — intended for design-space exploration, which wants
the full set of functionally-correct wirings, including correct-but-infeasible
circuits, as mutation seeds rather than acceptance candidates.

Of the remaining 4 × 14 × 6 = 336 possible ``input_pair`` / ``load`` /
``tail_current`` combinations, only 84 have compatible PMOS/NMOS polarities
(see the :ref:`polarity compatibility filter <compat-polarity>`) — the rest
are filtered out by ``enumerate_circuits``. Of those 84, the
:ref:`output-cardinality compatibility filter <compat-output-cardinality>`
further splits them by which
output type the ``load`` supports: **60** are valid for single-ended
templates (excluding the 12 combinations using a differential-output cascode
load and the 12 using a ``current_source_load_*``, whose CMFB-driven gates
need a fully-differential template; issue #112) and **48** are valid for
fully-differential templates (excluding the 36 combinations using a
single-output cascode or telescopic-cascode load). The
:ref:`untapped-load-branch compatibility filter <compat-load-branch>`
independently guards the same
``current_source_load_*`` exclusion structurally: a load may not leave the
single-ended templates' untapped branch node high-impedance (no defined
operating point; issue #112).

The ``bias_generation`` slot contributes no enumeration factor at all: its
variant is *constructed* per combination from what the other slots consume
on each bias rail (see "Demand-driven bias construction" below), so every
core combination carries exactly one, structurally matched bias generator.

The 1-stage template therefore produces **60 distinct circuits**. In the
multi-stage templates, 4 ``amplification_stage`` variants are enumerable —
the two common-source stages (``common_source``, ``common_source_pmos``) and
the two non-inverting current-mirror stages (``noninverting_stage_nmos``,
``noninverting_stage_pmos``, added in issue #139); the two followers moved to
the ``output_stage`` category (issue #125) and
``differential_ota_second_stage`` is parked per issue #114, see above. The
``second_stage`` slot that senses the first stage's output keeps only the
level-reachable ones (the :ref:`stage-interface compatibility filter
<compat-stage-interface>`): one CS + one non-inverting stage per pair
polarity (``common_source``/``noninverting_stage_nmos`` for the 24 PMOS-pair
combinations, ``common_source_pmos``/``noninverting_stage_pmos`` for the 24
NMOS-pair combinations). In the 2-stage template the single ``compensation``
slot wraps that second stage directly, so the :ref:`compensation parity
filter <compat-compensation>` rejects the non-inverting stage (a
positive-even inversion count is positive feedback around a Miller cap),
leaving one CS stage per polarity. The 2-stage
single-ended template thus produces **180 circuits**
((30 × 1 + 30 × 1) × 3 ``compensation``; the count grew from 144 when the
two wide-swing telescopic loads were added, issue #129). The 2-stage
fully-differential template, which has two ``compensation`` slots, two
``second_stage`` slots (one per output path, both sensing the first
stage), and one ``cmfb`` slot, produces **648 circuits**: of the 48
fully-differential-compatible ``input_pair``/``load``/``tail_current``
combinations, the 24 using a ``"differential"``-cardinality load (the two
differential-output cascode loads and the two ``current_source_load_*``)
keep both ``cmfb`` variants (24 × 2 = 48); the other 24 collapse ``cmfb``
to a single canonical variant (24 × 1 = 24) -- 48 + 24 = 72 effective
load/``cmfb`` combinations (see the :ref:`CMFB compatibility filter
<compat-cmfb>`) -- 72
× 1² × 9 ``compensation`` pairs = 648. Each 3-stage
single-ended template adds two more ``second_stage`` slots (gm2, gm3 --
only gm2 senses the first stage; gm3 keeps both CS variants) and
two ``compensation`` slots (Cm1, Cm2) on top of the 1-stage base. In the
NMC scheme Cm1 wraps the gm2+gm3 cascade while Cm2 wraps gm3 alone: Cm2
forces gm3 inverting (a CS stage), and Cm1 then requires gm2 to be
non-inverting (the level-reachable ``noninverting_stage_*`` stage, so its
+2 inversions make the wrapped cascade odd) — the :ref:`compensation
parity filter <compat-compensation>` admits exactly that nesting, so the
NMC single-ended template enumerates **1080 circuits** (60 × 1 gm2 × 2 gm3
× 9). Before the non-inverting stage existed (issue #139) no gm2 could
satisfy Cm1 and the template enumerated 0. In the RNMC scheme each
capacitor wraps a single stage, so the non-inverting stage is rejected in
either slot and only the CS×CS pairings survive: **1080 circuits**
(60 × 2 × 9). Each 3-stage fully-differential template duplicates those
four slots per output path (and keeps the single ``cmfb`` slot), producing
non-empty sets for both schemes: NMC and **23 328 circuits** (RNMC,
72 × (2 × 9)²).

Demand-driven bias construction
--------------------------------

The bias generator is not an enumerated module: ``enumerate_circuits``
*constructs* it per combination from what the other slots actually consume
on each of the eight bias rails (``out1``..``out4`` feed
``load.bias1``/``bias2``/``bias3``/``bias_cmfb``, ``out5`` feeds
``second_stage*.bias``, ``out6`` feeds ``third_stage*.bias``, ``out7`` feeds
``tail_current.bias``, ``out8`` feeds ``tail_current.bias_casc``; each
role's rail is independent, so the roles never share a bias voltage and can
be sized independently).

Each consumed rail is classified structurally (no YAML tags) into a *kind*:

- ``gate_vdd`` / ``gate_gnd`` -- a consumer MOSFET gate whose source sits on
  a supply needs a voltage one ``V_GS`` from that supply. The leg is an
  ``ibias``-derived mirror ending in a diode-connected device on the rail,
  which doubles as the mirror *master* of its consumers -- the sizer sets
  consumer currents by W/L ratio instead of matching voltages.
- ``current_source`` / ``current_sink`` -- the consumer brings its own
  reference diode (the current-mirror tails' mirror diode): the rail is a
  *current* interface and the leg is a bare
  mirror with no diode of its own. A bias-side diode here would either sit
  in parallel with the tail's reference (splitting the current) or fight it
  (issue #99's measured rail-7 contention) -- both are now unconstructable.
- ``cascode_gnd`` / ``cascode_vdd`` -- a cascode gate (consumer source on an
  internal node) needs its ``V_GS`` plus the saturation floor of the stack
  toward its back supply. The leg is a mirror into a diode-connected device
  riding a small floor resistor (``out = V_GS + I × R`` from that supply):
  the diode covers the large, Vth-dependent ``V_GS`` part -- tracking the
  consumer over process and temperature -- and the resistor covers only the
  small Vdsat floor; both are sized per rail by the sizer from the consumer
  stack (issue #99's parked cascode class).
- ``tunable`` -- no structurally implied level (conflicting demands on a
  shared rail): a mirror into a resistor, ``out = I_leg × R``, per-rail
  tunable by the sizer.

The constructed variant (name ``constructed_bias``) always carries an NMOS
master reference on the ``ibias`` pin; a ``pref`` branch deriving the
PMOS-side mirror reference is emitted only when some leg needs it. The pref
branch is *cascoded*: a wide-swing ``ncasc`` level (PMOS mirror into a
narrow diode) pins the branch mirror's Vds near the master's instead of at
``vdd - |V_GSP|`` -- closing most of the extra-mirror-hop λ error that
issue #103's A/B measured against the retired ``magic_battery_bias``. Only
consumed rails get a port and a leg -- unconsumed rails simply don't exist.
The leg templates live in ``config/bias_legs.yaml``; the demand analysis and
assembly in :mod:`circuitgenome.synthesizer.bias_construction`.

Because every rail gets exactly the leg its consumer requires, the
structurally unbiasable flavor mismatches that issue #99 measured (and that
previously had to be filtered out) can no longer be expressed, and
mixed-flavor consumer sets -- e.g. every real-cmfb fully-differential
circuit, whose rail 4 is gnd-referenced while rails 1/5 are vdd-referenced
-- get correct per-rail legs instead of being routed to an all-resistor
generator.

In ``fully_differential`` topologies, the ``cmfb`` slot's ``bias`` port is
wired to ``out4`` (``net_bias4``), but (per the :ref:`CMFB compatibility
filter <compat-cmfb>`) ``cmfb`` is pruned to an empty placeholder unless
``load``'s
``output_cardinality`` is ``"differential"`` -- construction runs after that
prune, so placeholder slots demand nothing and rail 4 gets a leg exactly
when a real cmfb consumes it.

Three-stage compensation schemes
---------------------------------

The 3-stage templates reuse the existing ``amplification_stage`` modules for
the second (gm2) and third (gm3) gain stages, and the existing
``compensation`` modules for the two Miller capacitors Cm1/Cm2. The NMC
scheme additionally needs a **non-inverting gm2**, because Cm1 wraps the
gm2+gm3 cascade and pole-splitting Miller feedback is negative only around
an inverting chain — with an inverting gm3, gm2 must be non-inverting. That
role is filled by the ``noninverting_stage_{nmos,pmos}`` variants (issue
#139); before they existed, the NMC templates enumerated zero circuits (see
the :ref:`compensation parity filter <compat-compensation>`).

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Scheme
     - Cm1 / Cm2 connections
   * - Nested Miller (NMC)
     - Cm1 spans gm2+gm3 (gm1's output → final output, the outer loop);
       Cm2 spans gm3 only (gm2's output → final output, the inner loop).
       Both capacitors return to the final output node.
   * - Reversed Nested Miller (RNMC)
     - Cm1 spans gm3 only (gm2's output → final output); Cm2 spans gm2 only
       (gm1's output → gm2's output) instead of returning to the final
       output. This reduces loading on the output node, which is useful
       when gm3 is a low-gain buffer stage.

Modular interface contract
--------------------------

Each module category defines a **canonical port signature** shared by all its
variants.  The topology template wires ports to global nets by name; the
internal device structure is invisible to the template.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Category
     - Canonical ports
   * - ``input_pair``
     - ``in1``, ``in2``, ``out1``, ``out2``, ``tail``, ``vdd``, ``gnd``
   * - ``load``
     - ``in1``, ``in2`` (folding nodes, driven by ``input_pair.out1`` /
       ``out2``), ``out1``, ``out2`` (differential output nodes — wired to
       dedicated ``net_loadout1``/``net_loadout2`` nets in
       ``fully_differential`` topologies for distinct cascode-output devices,
       or merged back onto ``in1``/``in2`` via ``alias_of`` for simple
       resistor/active/current-source loads), ``out`` *(mandatory only for
       single-output cascode loads, wired to the stage's single output node
       in ``single_ended`` topologies; optional/unused otherwise)*,
       ``bias1``, ``bias2``, ``bias3``, ``bias_cmfb`` *(optional bias inputs;
       each variant declares only as many as it needs)*, ``vdd``, ``gnd``.
       Whichever of ``out``/``out1``/``out2`` is mandatory is declared via
       ``output_cardinality: "single" | "differential" | None``, checked
       against the topology's ``output_type`` by the output-cardinality
       compatibility filter
   * - ``tail_current``
     - ``out``, ``bias`` *(current-mirror / cascode-current-mirror variants
       wire this to the dedicated ``net_bias7`` rail; resistor-tail variants
       declare it ``optional`` and leave it unconnected)*, ``bias_casc``
       *(cascode-current-mirror variants only: the wide-swing cascode-gate
       level, wired to ``net_bias8``)*, ``vdd``, ``gnd``
   * - ``bias_generation``
     - ``ibias``, ``out1``..``out8`` -- consumed rails only (``out1``-``out4``
       feed ``load``'s ``bias1``/``bias2``/``bias3``/``bias_cmfb``, ``out5``
       feeds ``second_stage.bias``, ``out6`` feeds ``third_stage.bias``,
       ``out7`` feeds ``tail_current.bias``, ``out8`` feeds
       ``tail_current.bias_casc``), ``vdd``, ``gnd``. The variant
       is constructed per combination by
       :func:`~circuitgenome.synthesizer.bias_construction.construct_bias_generation`,
       with one typed leg per consumed rail
   * - ``cmfb``
     - ``in1``, ``in2`` (differential sense inputs, wired to
       ``net_loadout1``/``net_loadout2`` -- the ``load``'s cascode-output
       nodes), ``vref`` (common-mode reference, wired to the external
       ``vcm_ref`` port), ``bias`` (tail-current bias, reuses ``net_bias4``
       from ``bias_generation.out4``), ``out`` (drives ``load.bias_cmfb`` via
       ``net_cmfb_out``), ``vdd``, ``gnd``. Two variants:
       ``resistive_sense_cmfb`` (resistive averager + 5T OTA) and
       ``dda_cmfb`` (differential-difference amplifier). Present only when
       ``load``'s ``output_cardinality`` is ``"differential"`` (see "CMFB
       compatibility filter" above); otherwise pruned to an empty placeholder
       and ``vcm_ref`` is left unconnected
   * - ``compensation``
     - ``in``, ``out``
   * - ``second_stage``
     - ``in``, ``out``, ``bias``, ``vdd``, ``gnd``

Supply ports (``vdd``, ``gnd``) are automatically connected to the global
rails ``vdd!`` / ``gnd!`` unless explicitly overridden in the topology
template.

SPICE output formats
--------------------

**Flat** — every device inlined in one ``.subckt`` block.  Maximally
portable.

.. code-block:: spice

   .subckt circuit_0001 ibias in1 in2 out vdd! gnd!
   m1_input_pair net_diff1 in1 net_tail net_tail pmos
   m2_input_pair net_mid in2 net_tail net_tail pmos
   r1_load vdd! net_diff1 1k
   r2_load vdd! net_mid 1k
   ...
   .ends

**Hierarchical** — one ``.subckt`` per module variant, top-level uses ``X``
instances.  Shared variants are defined only once.

.. code-block:: spice

   .subckt differential_pair_pmos in1 in2 out1 out2 tail vdd gnd
   m1 out1 in1 tail tail pmos
   m2 out2 in2 tail tail pmos
   .ends

   .subckt circuit_0001 ibias in1 in2 out vdd! gnd!
   Xinput_pair in1 in2 net_diff1 net_mid net_tail vdd! gnd! differential_pair_pmos
   ...
   .ends

Analysis
--------

.. toctree::
   :maxdepth: 1

   ../theory/compatibility_filters

API reference
-------------

.. toctree::
   :maxdepth: 1

   ../api/synthesizer
   ../api/models
   ../api/loader
   ../api/netlist
   ../api/compatibility/index
   ../api/bias_construction
   ../api/net_aliasing
