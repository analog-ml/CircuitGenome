gm/Id Sizing Flow
=================

*Deterministic procedural pipeline for PTM nodes and foundry PDKs.*

This page documents the **block-based gm/Id sizing pipeline**
(:func:`~circuitgenome.sizer.gmid.gmid_sizer.size_gmid`), the path
:func:`~circuitgenome.sizer.sizer.size_circuit` selects for technologies that
carry a gm/Id lookup table (the ``gmid_lut`` field of
:class:`~circuitgenome.sizer.shared.models.TechParams`, e.g. ``ptm45`` or the
GF180MCU foundry PDK).  Unlike the Level-1 analytical flow — documented in
:doc:`sizing_flow`, which *searches* integer W/L with CP-SAT — the gm/Id path
**computes** geometry in a single deterministic forward pass: with ``Id`` fixed
by KCL and ``gm/Id`` chosen per device, the LUT turns ``Id/W`` straight into
``W``.

It walks the pipeline end to end as **five phases**, each with a runnable
snippet and its observed output, then explains the **role vs functional
building block** distinction that drives the per-device gm/Id choice.

----

Bootstrap
---------

The metrics this pipeline computes (Phase 5) are a fast, deterministic
sizing-quality signal; for PTM / foundry techs the CLI reports **ngspice-measured**
performance instead (see :doc:`sizing_flow`, *Feasibility verdict and SPICE
metrics*).  For how a technology selects this path versus the Level-1 path — and
the ``UnsupportedTechError`` case — see :doc:`../modules/sizer`, *Path selection*.

All snippets below assume this bootstrap (topology ``two_stage_opamp_single_ended``,
tech ``ptm45``, a 1.0 V spec):

.. code-block:: python

   from circuitgenome.recognizer import assign_slots, parse, recognize
   from circuitgenome.synthesizer.loader import load_modules, load_topologies
   from circuitgenome.synthesizer.synthesizer import enumerate_circuits
   from circuitgenome.synthesizer.netlist import to_flat_spice
   from circuitgenome.sizer.shared.loader import load_tech
   from circuitgenome.sizer.shared.models import SizingSpec

   topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
   # a mirror-loaded variant (index 15) that biases cleanly at 1.0 V — see the note below
   circ = list(enumerate_circuits(topo, load_modules()))[15]
   parsed = parse(to_flat_spice(circ))
   fbr = assign_slots(recognize(parsed), topo)
   tech = load_tech("ptm45")
   spec = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                     second_stage_current_ratio=2.5, gain_min_db=55, gbw_min_hz=1e6,
                     phase_margin_min_deg=60, slew_rate_min_vps=0.65e6)

.. note::

   ``enumerate_circuits`` yields every structural variant of the topology; the
   walkthrough uses index 15 — a **current-mirror-loaded** two-stage that biases
   soundly at this 1.0 V spec — so every phase below is one coherent, feasible run.
   Other variants (resistor-loaded first stages, cascode tails) can fail the
   Phase 4b bias check; see the failure call-out there.

----

Flow at a glance
----------------

.. code-block:: text

   size_circuit ──(tech.gmid_lut?)──► size_gmid
      │
      ├─ Phase 1  Analyze          (analyze.py)   ─► CircuitView
      │             slots, block view, dedupe, cascodes, topology check
      ├─ Phase 2  Bias currents    (plan.py)      ─► CurrentPlan
      │             Ids by KCL, rail-referenced load resistors
      ├─ Phase 3  Plan             (plan.py)      ─► SizingPlan
      │             GmIdModel, gm requirements + Cc, per-device intent
      ├─ Phase 4  Size
      │             a. geometry (LUT→W/L, sym, mirror)   (geometry.py)  ◄── core
      │             b. DC bias check + tail repair       (bias.py)
      │             c. non-load resistors                (resistors.py) ─► MetricModifiers
      └─ Phase 5  Evaluate         (evaluate.py)  ─► metrics/margins
                    cascode-aware rout, analytical gain/GBW/PM
                    ─► SizingResult(status="GMID")

The key design property: because current is fixed in Phase 2 and gm/Id is a
*choice* (Phase 3), geometry is computed, not searched — Phases 4–5 are a
straight line with one explicit repair (the tail re-size in Phase 4b, which
returns a new sizing rather than mutating).

----

Design intent hierarchy
-----------------------

The gm/Id choices are organised as an explicit three-level hierarchy so the flow
reads top-down (:mod:`circuitgenome.sizer.gmid.intent`):

.. code-block:: text

   Circuit intent (spec)         SizingSpec: gain, GBW, PM, swing, power …   (the *what*)
           │
           ▼
   Functional-block intent       BlockIntent: per building block — role, gm/Id
           │                      region, L multiple, and the *rationale*.
           ▼
   Transistor intent             TransistorIntent: the block intent resolved onto
                                  each device (role, gm/Id, L, block, why).

Level 1 is :class:`~circuitgenome.sizer.shared.models.SizingSpec`; Level 2 is the
:data:`~circuitgenome.sizer.gmid.intent.DEFAULT_BLOCK_INTENTS` registry of
:class:`~circuitgenome.sizer.gmid.intent.BlockIntent`; Level 3 is the per-device
:class:`~circuitgenome.sizer.gmid.intent.TransistorIntent`, surfaced on
:attr:`SizingResult.transistor_intents <circuitgenome.sizer.shared.models.SizingResult>`
for explainability.

Roles vs functional building blocks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These are two different levels of description, and the distinction is the crux of
how gm/Id is chosen.

A **role** is the *sizing archetype* — one of exactly three ways gm/Id gets
decided, because gm/Id is essentially a one-dimensional design variable (it
selects the inversion region):

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Role
     - gm/Id
     - How it is chosen
   * - ``SIGNAL``
     - **solved**
     - A transconductor (input pair, gain-stage driver). Its gm is *required* by
       the spec, and since ``gm = (gm/Id)·Id`` with ``Id`` already fixed, the
       gm/Id falls out as ``gm_req/Id`` — not a free knob (carries ``gm_id=None``).
   * - ``CURRENT_SOURCE``
     - free, low
     - A bias/load device (tail, mirror, bias gen). No gm target; biased to a low
       gm/Id (strong-ish inversion) for headroom, output resistance and matching.
   * - ``CASCODE``
     - free, lower
     - A stacked device that multiplies ``rout``; needs an even smaller ``Vdsat``
       to preserve the stacked headroom, so an even lower gm/Id.

A **functional building block** is the *semantic identity* of a device group
(input stage, gain stage, tail source, …).  It carries the human-readable
rationale and is the per-block override handle — but each block still resolves to
exactly one of the three roles for the actual sizing math.  The block adds
*which* stage/function; the role decides *how* gm/Id is picked.

Crucially, a block is keyed by **(slot, role)**, not by FBR slot alone, because
one slot can hold devices of different roles.  In the two-stage example the
``second_stage`` slot contains **both** the gain-stage signal driver *and* its
current-source load — they resolve to different blocks
(:func:`~circuitgenome.sizer.gmid.intent.functional_block`):

.. list-table:: Default block registry (``DEFAULT_BLOCK_INTENTS``)
   :header-rows: 1
   :widths: 20 18 12 50

   * - Building block
     - Role
     - gm/Id
     - Design rationale
   * - ``input_stage``
     - SIGNAL
     - solved
     - Convert differential voltage to current with high gm, low noise, matching.
   * - ``gain_stage``
     - SIGNAL
     - solved
     - Increase voltage gain while maintaining stability.
   * - ``output_stage``
     - SIGNAL
     - solved
     - Drive the load capacitance and provide slew current.
   * - ``active_load``
     - CURRENT_SOURCE
     - 10
     - First-stage current-mirror load: accurate current, high output resistance.
   * - ``stage_load``
     - CURRENT_SOURCE
     - 10
     - Current-source load of a gain/output stage: high ``rout`` for gain.
   * - ``tail_current``
     - CURRENT_SOURCE
     - 10
     - Set the input-pair bias current with headroom and high ``rout``.
   * - ``bias_generator``
     - CURRENT_SOURCE
     - 10
     - Stable, low-sensitivity reference currents for the mirrors it drives.
   * - ``cmfb``
     - CURRENT_SOURCE
     - 10
     - Regulate the output common-mode voltage.
   * - ``cascode``
     - CASCODE
     - 8
     - Increase output resistance with a small ``Vdsat``.

.. note::

   :class:`~circuitgenome.sizer.gmid.intent.GmIdIntent` carries the
   ``block_intents`` registry, so a caller or optimizer can retune a single block
   (e.g. push the ``tail_current`` gm/Id lower for more headroom) while the rest
   fall back to the defaults — the extension seam for gm/Id tuning and topology
   mutation.

----

Phase-by-phase walkthrough
--------------------------

Each phase below assumes the bootstrap above and the results of the prior
phases.  Each phase is one function with a typed result, so the orchestrator
(:func:`~circuitgenome.sizer.gmid.gmid_sizer.size_gmid`) reads as this
walkthrough does.

Phase 1 — Analyze: the structural view
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.analyze.analyze_circuit` derives everything the
later phases need to know about the circuit's *structure*, once, into a
:class:`~circuitgenome.sizer.gmid.analyze.CircuitView`: the per-slot MOSFET and
resistor lists, the typed block view (:func:`~circuitgenome.sizer.gmid.blocks.build_blocks`
classifies each load's *kind* — MIRROR / CASCODE / RESISTOR / CURRENT_SOURCE),
the deduplicated ``ref → (Device, slot)`` map (a device appearing in several
slots is attributed to the highest-priority one), the cascode device refs, and a
topology-mismatch warning when a gain-stage slot holds no signal transistor.

.. code-block:: python

   from circuitgenome.sizer.gmid.analyze import analyze_circuit
   view = analyze_circuit(fbr, topo)
   print({k: [d.ref for d in v] for k, v in view.slot_transistors.items()})
   print(view.blocks.load.load_kind, view.blocks.n_stages, view.blocks.is_fully_differential)
   print(view.warnings)
   print({r: (d.type, slot) for r, (d, slot) in view.all_transistors.items()})

.. code-block:: text

   {'input_pair': ['m1_input_pair','m2_input_pair'], 'load': ['m1_load','m2_load'],
    'bias_gen': ['mnref_bias_gen','mn5_bias_gen','mp5_bias_gen'],
    'second_stage': ['mn1_second_stage','mp1_second_stage']}
   LoadKind.MIRROR  2  False
   []
   {'m1_input_pair': ('pmos','input_pair'), 'm2_input_pair': ('pmos','input_pair'),
    'm1_load': ('nmos','load'), 'm2_load': ('nmos','load'),
    'mn1_second_stage': ('nmos','second_stage'), 'mp1_second_stage': ('pmos','second_stage'),
    'mnref_bias_gen': ('nmos','bias_gen'), 'mn5_bias_gen': ('nmos','bias_gen'),
    'mp5_bias_gen': ('pmos','bias_gen')}   # 9 unique devices

.. note::

   The slot names and bias-net conventions the whole sizer assumes about a
   template live in one place:
   :mod:`circuitgenome.sizer.shared.taxonomy`.  A new topology whose slots
   follow those conventions needs no sizer changes; one that introduces new
   slot names is supported by extending the groups there.

Phase 2 — Bias currents: Ids by KCL + load resistors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.plan.assign_currents` fixes what *cannot* be
chosen.  It walks the bias current (``spec.ibias``) and the per-stage current
ratios to give every device its ``Id``
(:func:`~circuitgenome.sizer.shared.preprocess.assign_ids`), and sets
rail-referenced load resistors so the first-stage output biases correctly
(:func:`~circuitgenome.sizer.shared.preprocess.size_load_resistors`).  This is
what makes the rest deterministic: with ``Id`` fixed, the LUT turns a chosen
gm/Id straight into geometry.

.. code-block:: python

   from circuitgenome.sizer.gmid.plan import assign_currents
   currents = assign_currents(view, spec, tech)
   print({r: round(i*1e6, 2) for r, i in currents.ids_map.items()})   # Ids in uA
   print(currents.load_resistors, currents.gd_load_r)

.. code-block:: text

   {'m1_input_pair': 7.5, 'm2_input_pair': 7.5, 'm1_load': 7.5, 'm2_load': 7.5,
    'mn1_second_stage': 37.5, 'mp1_second_stage': 37.5, 'mnref_bias_gen': 15.0,
    'mn5_bias_gen': 15.0, 'mp5_bias_gen': 15.0}
   {}  0.0

(ibias 15 µA splits 7.5 per input-pair half; the second stage carries 15 × 2.5 =
37.5; the bias devices carry 15.  This mirror-loaded variant has no rail-referenced
load resistors, so ``load_resistors`` is empty — its tail is a resistor, sized in
Phase 4c.)

Phase 3 — Plan: requirements + per-device intent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.plan.plan_devices` derives what must be
*achieved* and what is *chosen*, into a
:class:`~circuitgenome.sizer.gmid.plan.SizingPlan`:

* It builds the :class:`~circuitgenome.sizer.shared.device_model.GmIdModel`
  (wrapping the :class:`~circuitgenome.sizer.shared.gmid_lut.GmIdLut` ``.npz``
  table) with a ``GmIdPolicy`` translated from the role-level fallbacks in
  :class:`~circuitgenome.sizer.gmid.intent.GmIdIntent`.  This is the only place
  the model is instantiated.
* :func:`~circuitgenome.sizer.shared.preprocess.compute_requirements` computes,
  from the spec (GBW, gain, phase margin, load cap), each signal device's
  required gm and the compensation caps ``cc_pf`` / ``cc2_pf``, emitting ceiling
  warnings if a required gm/Id exceeds the weak-inversion limit.
* :func:`~circuitgenome.sizer.gmid.intent.resolve_transistor_intents` maps every
  device to its functional building block via
  :func:`~circuitgenome.sizer.gmid.intent.functional_block` (signal precedence,
  then cascode, then current-source), and reads the block's role, gm/Id region
  and L multiple from the registry.  Note the ``second_stage`` slot splitting by
  role: the signal driver → ``gain_stage``, its load → ``stage_load`` (see
  `Roles vs functional building blocks`_).

.. code-block:: python

   from circuitgenome.sizer.gmid.plan import plan_devices
   from circuitgenome.sizer.gmid.intent import DEFAULT_INTENT
   intent = DEFAULT_INTENT
   plan = plan_devices(view, currents, spec, tech, intent)
   print({r: round(g*1e6, 1) for r, g in plan.gm_req_map.items()}, plan.cc_pf, plan.cc2_pf)
   print(plan.warnings)
   for r in ["m1_input_pair","mn1_second_stage","mp1_second_stage","mnref_bias_gen"]:
       ti = plan.tintents[r]
       print(f"{r:18s} block={ti.block:14s} role={ti.role:14s} gm_id={ti.gm_id} l_mult={ti.l_mult}")

.. code-block:: text

   {'m1_input_pair': 3.1, 'm2_input_pair': 3.1, 'mn1_second_stage': 900.0, 'mp1_second_stage': 0.0}  0.5  None
   ['second-stage gm requirement exceeds the weak-inversion ceiling — increase second_stage_current_ratio/ibias or relax gain.']
   m1_input_pair      block=input_stage    role=signal         gm_id=None l_mult=2.0
   mn1_second_stage   block=gain_stage     role=signal         gm_id=None l_mult=2.0
   mp1_second_stage   block=stage_load     role=current_source gm_id=10.0 l_mult=4.0
   mnref_bias_gen     block=bias_generator role=current_source gm_id=10.0 l_mult=4.0

Phase 4a — Size: assign geometry (LUT → W/L, symmetry, mirror ratios)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.geometry.assign_geometry_gmid` is the core
forward pass, driven by each device's ``TransistorIntent``: (a) the LUT gives
per-device (W, L) from ``Id`` + the block's gm/Id region and L (signal devices
solve gm/Id from ``gm_req``); (b) snap W to grid; (c) *symmetry* — matched pairs
share the anchor's geometry; (d) *mirror ratios* — each output W = exact current
ratio × the diode reference's W.  Returns
:class:`~circuitgenome.sizer.shared.models.TransistorSizing` (W, L, Vgs, Vdsat).

.. code-block:: python

   from circuitgenome.sizer.gmid.geometry import assign_geometry_gmid
   sizing, geom_warn, geom_feasible = assign_geometry_gmid(
       plan.model, view.all_transistors, view.slot_transistors,
       currents.ids_map, plan.tintents, plan.gm_req_map, tech, vod_max_map=plan.vod_max_map)
   for r, s in sizing.items():
       print("%-18s W=%.2f L=%.3f Vgs=%.3f Vdsat=%.3f" % (r, s.w_um, s.l_um, s.vgs_v, s.vds_sat_v))

.. code-block:: text

   m1_input_pair      W=0.10 L=0.090 Vgs=-0.733 Vdsat=0.243
   m2_input_pair      W=0.10 L=0.090 Vgs=-0.733 Vdsat=0.243
   m1_load            W=0.10 L=0.090 Vgs=0.581 Vdsat=0.131
   m2_load            W=0.10 L=0.090 Vgs=0.581 Vdsat=0.131
   mn1_second_stage   W=97.30 L=0.090 Vgs=0.298 Vdsat=0.044
   mp1_second_stage   W=2.40 L=0.180 Vgs=-0.654 Vdsat=0.179
   mnref_bias_gen     W=0.35 L=0.180 Vgs=0.607 Vdsat=0.152
   mn5_bias_gen       W=0.35 L=0.180 Vgs=0.607 Vdsat=0.152
   mp5_bias_gen       W=0.95 L=0.180 Vgs=-0.655 Vdsat=0.180

Phase 4b — Size: DC bias feasibility (headroom + cascode budget)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.bias.check_dc_operating_point` does two things:
a **headroom repair** checks the tail's saturation headroom and, when short,
re-sizes the tail mirror group to a higher gm/Id (lower ``Vdsat``) — keeping
mirror ratios consistent; then a **cascode-aware budget** sums a stacked tail's
series ``Vdsat`` and flags infeasibility a single-device check would miss.  It
returns the (possibly repaired) sizing, the warnings, and a ``bias_feasible``
verdict — the input sizing is never mutated.

.. code-block:: python

   from circuitgenome.sizer.gmid.bias import check_dc_operating_point
   sizing, dc_warn, bias_feasible = check_dc_operating_point(
       plan.model, view.blocks, view.slot_transistors, view.all_transistors,
       currents.ids_map, sizing, spec, tech)
   print(bias_feasible, dc_warn)

.. code-block:: text

   True  []

For this mirror-loaded variant the tail biases with headroom to spare, so Phase 4b
returns ``True`` with no warning and the sizing passes through unchanged (the "tail
already fit" row below).

.. admonition:: What a failure looks like
   :class: caution

   Not every variant is so lucky.  A **cascode tail** at this 1.0 V spec is
   headroom-starved — running the same phase on such a variant returns
   ``bias_feasible = False``:

   .. code-block:: text

      False  ['cascode tail current source cannot bias: needs 237 mV of stacked Vdsat
              but only 65 mV is available at Vcm=0.50 V — the input-pair current will
              collapse (use a non-cascode tail, lower the input common-mode, or raise
              the supply).']

   The repair could not fit the tail, so the design is rejected and the metrics must
   not be trusted (see the interpretation below).  Raising ``vdd`` (e.g. to 1.8 V),
   lowering the input common-mode, or flipping the input polarity clears it.

**Interpreting the verdict.**  Phase 4b *repairs first and warns only on failure*,
so the presence or absence of a warning maps to three outcomes:

.. list-table::
   :header-rows: 1
   :widths: 34 16 50

   * - Outcome
     - ``bias_feasible``
     - Meaning
   * - No warning, tail already fit
     - ``True``
     - DC bias is sound; the sizing is unchanged.
   * - No warning, tail repaired
     - ``True``
     - DC bias is sound, but the returned sizing has the tail mirror group
       re-sized to fit — a clean result does **not** mean nothing was adjusted.
   * - Warning emitted
     - ``False``
     - The repair could not fit the tail; it cannot stay saturated, so the assumed
       ``Id`` will not flow.

Every Phase-4b warning sets ``bias_feasible = False``.  When it does, the W/L values are
still internally consistent (they hit their gm/Id targets), but the **design is
infeasible**: the tail drops into triode, ``gm1 = (gm/Id)·Id`` collapses, and the
gain/GBW reported in `Phase 5 — Evaluate: analytical metrics`_ are *optimistic and
should not be trusted*.  It is a physical-feasibility failure, not a computation
error — treat a Phase-4b warning as "reject, and do not trust the metrics".

.. note::

   Not every result warning is a bias warning.  The gm-ceiling advisory from
   `Phase 3 — Plan: requirements + per-device intent`_
   (*"…exceeds the weak-inversion ceiling…"*) also means the design falls short, but for a
   different reason — the required gm is unreachable at the bias current — and it does
   **not** set ``bias_feasible``.  Audit by wording: *"insufficient saturation headroom"*
   or *"cascode tail … cannot bias"* ⇒ the bias is infeasible.

.. note::

   This is a fast **analytical pre-check**, and it is tail-focused: a SPICE DC
   bias-soundness check
   (:func:`~circuitgenome.sizer.shared.spice_sim.check_bias_soundness`) grounds the final
   verdict for PTM / foundry techs.  So ``bias_feasible = True`` is *necessary but not
   sufficient* — it does not yet check, e.g., second-stage headroom.  Remedies for a
   failure: raise the supply, lower the input common-mode, flip the input polarity, or use
   a non-cascode tail.

Phase 4c — Size: non-load resistors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.resistors.size_resistors` sizes
source-degeneration, resistor-tail, resistor-bias and CMFB-sense resistors,
returning their small-signal effects as a
:class:`~circuitgenome.sizer.gmid.resistors.MetricModifiers`: ``gm1_factor``
(degeneration on input gm), ``gd_tail_override`` (resistor tail on CMRR),
``gd_out_extra`` (CMFB loading).

.. code-block:: python

   from circuitgenome.sizer.gmid.resistors import size_resistors
   extra_r, modifiers = size_resistors(
       view.blocks, view.slot_resistors, currents.ids_map, sizing,
       plan.model, spec, tech, intent, cc_pf=plan.cc_pf, cc2_pf=plan.cc2_pf)
   resistors = {**currents.load_resistors, **extra_r}
   print(extra_r, modifiers)

.. code-block:: text

   {'r1_tail_current': 15513.891903032769} MetricModifiers(gm1_factor=1.0, gd_tail_override=6.44583581122228e-05, gd_out_extra=0.0)
   # this variant has a resistor tail (r1_tail_current); its conductance sets
   # gd_tail_override — the tail gds used for CMRR — but no degeneration or CMFB

Phase 5 — Evaluate: analytical metrics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.evaluate.evaluate_circuit` produces the
analytical, ngspice-free estimate of gain / GBW / PM / etc.  When the first-stage
load is a cascode it first computes the output resistance cascode-aware
(:func:`~circuitgenome.sizer.gmid.blocks.node_rout` — the ``gm·ro·ro`` boost a
single-``gds`` estimate misses, treating the input-pair tail node as AC ground),
and it applies the Phase-4c :class:`~circuitgenome.sizer.gmid.resistors.MetricModifiers`.

.. code-block:: python

   from circuitgenome.sizer.gmid.evaluate import evaluate_circuit
   metrics, margins, notes = evaluate_circuit(view, currents, plan, sizing, modifiers, spec, tech)
   print({k: round(v, 2) for k, v in metrics.items()})

.. code-block:: text

   {'gain_db': 60.55, 'gbw_hz': 15957948.23, 'phase_margin_deg': 77.44, 'slew_rate_vps': 30000000.0,
    'power_w': 0.0, 'cmrr_db': -8.2, 'psrr_db': 49.47}

Gain (60.6 dB) and phase margin (77.4°) clear the spec's 55 dB / 60° targets.  The
poor ``cmrr_db`` traces straight back to Phase 4c: the resistor tail's finite
conductance (``gd_tail_override``) is a leaky current source, which the CMRR term
penalises heavily — a concrete example of a Phase-4c modifier shaping a Phase-5
metric.

Putting it together
~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.gmid_sizer.size_gmid` runs exactly the five
phases above and packages a
:class:`~circuitgenome.sizer.shared.models.SizingResult` with
``solver_status="GMID"``, the transistor sizings, resistors, compensation caps,
metrics, the ``bias_feasible`` verdict, the resolved ``transistor_intents``, and
the accumulated warnings (topology + ceiling + geometry + DC).  In practice you
never assemble it by hand — the whole pipeline is one call:

.. code-block:: python

   from circuitgenome.sizer.gmid import size_gmid
   result = size_gmid(parsed, recognize(parsed), fbr, topo, tech, spec)
   print(result.solver_status, result.bias_feasible, len(result.transistors), len(result.warnings))

.. code-block:: text

   GMID  True  9  1

``bias_feasible`` is ``True`` and the design clears its gain and phase-margin
targets.  The single remaining warning is the Phase-3 weak-inversion *ceiling
advisory* (the second stage's required gm sits above the efficient inversion
region) — not a bias failure, and the gain spec is met regardless.

----

See also
--------

* :doc:`sizing_flow` — the Level-1 (Shichman-Hodges) analytical flow with CP-SAT,
  used for the card-less ``generic`` technology.
* :mod:`circuitgenome.sizer.gmid.intent` — the design-intent hierarchy
  (``GmIdIntent``, ``BlockIntent``, ``TransistorIntent``).
* :mod:`circuitgenome.sizer.shared.taxonomy` — the slot/net naming conventions a
  circuit template must follow (the single point of extension for new templates).
* :func:`circuitgenome.sizer.sizer.size_circuit` — the technology-routing entry point.
