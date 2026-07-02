gm/Id Sizing Workflow — Procedural Pipeline (PTM / Foundry PDK)
===============================================================

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

It walks the pipeline end to end as thirteen steps, each with a runnable snippet
and its observed output, then explains the **role vs functional building block**
distinction that drives the per-device gm/Id choice.

.. contents:: On this page
   :depth: 2
   :local:
   :backlinks: none

----

Scope and routing
-----------------

:func:`~circuitgenome.sizer.sizer.size_circuit` dispatches on the technology:

* ``tech.gmid_lut`` present (``ptm45``, GF180MCU) → **this** gm/Id pipeline.
* card-less ``generic`` (no LUT, no SPICE card) → the Level-1 analytical sizer
  (:doc:`sizing_flow`).
* a PTM/SPICE-model node **without** a LUT (``ptm32``/``ptm22``/``ptm16``) →
  ``UnsupportedTechError`` — the Level-1 square law is not valid there, and the
  gm/Id path needs a table the tech does not provide.

The analytical metrics this pipeline computes are a fast, deterministic
sizing-quality signal; for PTM / foundry techs the CLI reports **ngspice-measured**
performance instead (see :doc:`sizing_flow`, *Feasibility verdict and SPICE
metrics*).

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
   circ = next(enumerate_circuits(topo, load_modules()))
   parsed = parse(to_flat_spice(circ))
   fbr = assign_slots(recognize(parsed), topo)
   tech = load_tech("ptm45")
   spec = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=2e-12,
                     second_stage_current_ratio=2.5, gain_min_db=55, gbw_min_hz=1e6,
                     phase_margin_min_deg=60, slew_rate_min_vps=0.65e6)

----

Flow at a glance
----------------

.. code-block:: text

   size_circuit ──(tech.gmid_lut?)──► size_gmid
      │
      ├─ 1. extract slots ──► build_blocks                  (blocks.py)
      ├─ 2. topology check + dedupe                         (shared/preprocess)
      ├─ 3. assign Ids by KCL                               (shared/preprocess)
      ├─ 4. size load resistors                             (shared/preprocess)
      ├─ 5. GmIdIntent → GmIdPolicy → GmIdModel             (gmid_sizer._model_for)
      ├─ 6. compute gm requirements + Cc                    (shared/preprocess)
      ├─ 7. resolve block intent → per-device role/gm-Id/L  (intent.resolve_transistor_intents)
      ├─ 8. assign geometry (LUT→W/L, sym, mirror)          (geometry.py)   ◄── core
      ├─ 9. DC bias feasibility (headroom + cascode)        (dc_op.py)      ◄── mutates sizing
      ├─ 10. size non-load resistors                        (resistors.py)
      ├─ 11. cascode rout override                          (blocks.node_rout)
      ├─ 12. evaluate metrics                               (shared/metrics)
      └─ 13. return SizingResult(status="GMID")

The key design property: because current is fixed at step 3 and gm/Id is a
*choice* (steps 5–7), geometry is computed, not searched — steps 8–13 are a
straight line with one in-place repair (step 9's tail re-size).

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

Step-by-step walkthrough
------------------------

Each step below assumes the bootstrap above and the results of the prior steps.

Step 1 — extract slots + block view
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.shared.preprocess._extract_slot_transistors` /
``_extract_slot_resistors`` pull the MOSFETs and resistors out of the
recognizer's functional-block slots;
:func:`~circuitgenome.sizer.gmid.blocks.build_blocks` groups them into typed
blocks (input pair, load, stages, tail…) and classifies each load's *kind*
(MIRROR / CASCODE / RESISTOR / CURRENT_SOURCE).

.. code-block:: python

   from circuitgenome.sizer.shared.preprocess import (
       _extract_slot_transistors, _extract_slot_resistors)
   from circuitgenome.sizer.gmid.blocks import build_blocks
   slot_transistors = _extract_slot_transistors(fbr)
   slot_resistors   = _extract_slot_resistors(fbr)
   blocks = build_blocks(slot_transistors, slot_resistors)
   print({k: [d.ref for d in v] for k, v in slot_transistors.items()})
   print(blocks.load.load_kind, blocks.n_stages, blocks.is_fully_differential)

.. code-block:: text

   {'input_pair': ['m1_input_pair','m2_input_pair'], 'tail_current': ['m1_tail_current','m2_tail_current'],
    'bias_gen': ['mn1_bias_gen','mn6_bias_gen','mp5_bias_gen','mn8_bias_gen','m1_tail_current'],
    'second_stage': ['mn1_second_stage','mp1_second_stage']}
   LoadKind.RESISTOR  2  False

Step 2 — topology check + dedupe
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.shared.preprocess._check_topology_match` warns if the
recognized slots don't match the claimed topology; ``_deduplicate`` collapses
devices appearing in multiple slots into one ``ref → (Device, slot)`` map.

.. code-block:: python

   from circuitgenome.sizer.shared.preprocess import _check_topology_match, _deduplicate
   topo_warn = _check_topology_match(slot_transistors, topo.name)
   all_transistors = _deduplicate(slot_transistors)
   print(topo_warn)
   print({r: (d.type, slot) for r, (d, slot) in all_transistors.items()})

.. code-block:: text

   []
   {'m1_input_pair': ('pmos','input_pair'), 'm2_input_pair': ('pmos','input_pair'),
    'm1_tail_current': ('pmos','tail_current'), 'm2_tail_current': ('pmos','tail_current'),
    'mn1_second_stage': ('nmos','second_stage'), 'mp1_second_stage': ('pmos','second_stage'),
    'mn1_bias_gen': ('nmos','bias_gen'), ... }   # 10 unique devices

Step 3 — assign currents by KCL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.shared.preprocess._assign_ids` walks the bias current
(``spec.ibias``) and the per-stage current ratios to give every device its
``Id``.  This is what makes the rest deterministic: with ``Id`` fixed, the LUT
turns a chosen gm/Id straight into geometry.

.. code-block:: python

   from circuitgenome.sizer.shared.preprocess import _assign_ids
   ids_map = _assign_ids(slot_transistors, all_transistors, spec)
   print({r: round(i*1e6, 2) for r, i in ids_map.items()})   # Ids in uA

.. code-block:: text

   {'m1_input_pair': 7.5, 'm2_input_pair': 7.5, 'm1_tail_current': 15.0, 'm2_tail_current': 15.0,
    'mn1_second_stage': 37.5, 'mp1_second_stage': 37.5, 'mn1_bias_gen': 15.0, ...}

(ibias 15 µA → tail 15, each input-pair half 7.5, second stage 15×2.5 = 37.5.)

Step 4 — size load resistors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.shared.preprocess._size_load_resistors` sets
rail-referenced load resistors so the first-stage output biases correctly;
``gd_load_r`` (their conductance) is derived for later gain/PM math.

.. code-block:: python

   from circuitgenome.sizer.shared.preprocess import _size_load_resistors
   resistors = _size_load_resistors(slot_resistors, spec, tech)
   gd_load_r = (1.0 / min(resistors.values())) if resistors else 0.0
   print(resistors, gd_load_r)

.. code-block:: text

   {'r1_load': 69866.7, 'r2_load': 69866.7}  1.431e-05

Step 5 — build the GmIdModel from intent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.gmid_sizer._model_for` translates the
role-level fallbacks in :class:`~circuitgenome.sizer.gmid.intent.GmIdIntent` into
a ``GmIdPolicy`` and constructs the
:class:`~circuitgenome.sizer.shared.device_model.GmIdModel` wrapping the
:class:`~circuitgenome.sizer.shared.gmid_lut.GmIdLut` (.npz table).  This is the
only place the model is instantiated.

.. code-block:: python

   from circuitgenome.sizer.gmid.gmid_sizer import _model_for
   from circuitgenome.sizer.gmid.intent import DEFAULT_INTENT
   intent = DEFAULT_INTENT
   model = _model_for(tech, intent)
   print(type(model).__name__, model.is_gmid, model.lut.gm_id_axis[:3], model.lut.gm_id_axis[-1])

.. code-block:: text

   GmIdModel  True  [6.0 6.5 7.0]  24.0

Step 6 — derive gm requirements + compensation caps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.shared.preprocess._compute_requirements` computes,
from the spec (GBW, gain, phase margin, load cap), each signal device's required
gm, the max output swing, and the compensation caps ``cc_pf`` / ``cc2_pf``.  It
emits ceiling warnings if a required gm/Id exceeds the weak-inversion limit.

.. code-block:: python

   from circuitgenome.sizer.shared.preprocess import _compute_requirements
   gm_req_map, vod_max_map, cc_pf, cc2_pf, ceil_warn = _compute_requirements(
       slot_transistors, all_transistors, ids_map, tech, spec, model, gd_load_r)
   print({r: round(g*1e6, 1) for r, g in gm_req_map.items()}, cc_pf, cc2_pf)
   print(ceil_warn)

.. code-block:: text

   {'m1_input_pair': 125.7, 'm2_input_pair': 125.7, 'mn1_second_stage': 900.0, 'mp1_second_stage': 0.0}  10.0  None
   ['second-stage gm requirement exceeds the weak-inversion ceiling — increase second_stage_current_ratio/ibias or relax gain.']

Step 7 — resolve functional-block intent onto each device
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.intent.resolve_transistor_intents` maps every
device to its functional building block via
:func:`~circuitgenome.sizer.gmid.intent.functional_block` (signal precedence,
then cascode, then current-source), and reads the block's role, gm/Id region and
L multiple from the registry.  Note the ``second_stage`` slot splitting by role:
the signal driver → ``gain_stage``, its load → ``stage_load`` (see
`Roles vs functional building blocks`_).

.. code-block:: python

   from circuitgenome.sizer.gmid.blocks import cascode_device_refs
   from circuitgenome.sizer.gmid.intent import resolve_transistor_intents
   cascodes = cascode_device_refs(slot_transistors)
   tintents = resolve_transistor_intents(all_transistors, cascodes, intent.block_intents)
   for r in ["m1_input_pair","mn1_second_stage","mp1_second_stage","m1_tail_current","mn1_bias_gen"]:
       ti = tintents[r]
       print(f"{r:18s} block={ti.block:14s} role={ti.role:14s} gm_id={ti.gm_id} l_mult={ti.l_mult}")

.. code-block:: text

   m1_input_pair      block=input_stage    role=signal         gm_id=None l_mult=2.0
   mn1_second_stage   block=gain_stage     role=signal         gm_id=None l_mult=2.0
   mp1_second_stage   block=stage_load     role=current_source gm_id=10.0 l_mult=4.0
   m1_tail_current    block=tail_current   role=current_source gm_id=10.0 l_mult=4.0
   mn1_bias_gen       block=bias_generator role=current_source gm_id=10.0 l_mult=4.0

Step 8 — assign geometry (LUT → W/L, symmetry, mirror ratios)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.geometry.assign_geometry_gmid` is the core
forward pass, driven by each device's ``TransistorIntent``: (a) the LUT gives
per-device (W, L) from ``Id`` + the block's gm/Id region and L (signal devices
solve gm/Id from ``gm_req``); (b) snap W to grid; (c) *symmetry* — matched pairs
share the anchor's geometry; (d) *mirror ratios* — each output W = exact current
ratio × the diode reference's W.  Returns
:class:`~circuitgenome.sizer.shared.models.TransistorSizing` (W, L, Vgs, Vdsat).

.. code-block:: python

   from circuitgenome.sizer.gmid.geometry import assign_geometry_gmid
   transistor_sizing, geom_warn = assign_geometry_gmid(
       model, all_transistors, slot_transistors, ids_map, tintents, gm_req_map, tech)
   for r, s in transistor_sizing.items():
       print("%-18s W=%.2f L=%.3f Vgs=%.3f Vdsat=%.3f" % (r, s.w_um, s.l_um, s.vgs_v, s.vds_sat_v))

.. code-block:: text

   m1_input_pair      W=0.80 L=0.090 Vgs=-0.541 Vdsat=0.102
   m1_tail_current    W=0.95 L=0.180 Vgs=-0.655 Vdsat=0.180
   mn1_second_stage   W=97.30 L=0.090 Vgs=0.298 Vdsat=0.044
   mp1_second_stage   W=2.40 L=0.180 Vgs=-0.654 Vdsat=0.179
   mn1_bias_gen       W=0.35 L=0.180 Vgs=0.607 Vdsat=0.152   ...

Step 9 — DC bias feasibility (headroom + cascode budget)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.dc_op.check_dc_operating_point` does two things:
:func:`~circuitgenome.sizer.gmid.headroom.apply_headroom` checks the tail's
saturation headroom and, when short, re-sizes the tail mirror group to a higher
gm/Id (lower ``Vdsat``) — mutating ``transistor_sizing`` in place — keeping mirror
ratios consistent; then a **cascode-aware budget** sums a stacked tail's series
``Vdsat`` and flags infeasibility a single-device check would miss.  Returns
warnings plus a ``bias_feasible`` verdict.

.. code-block:: python

   from circuitgenome.sizer.gmid.dc_op import check_dc_operating_point
   dc_warn, bias_feasible = check_dc_operating_point(
       model, blocks, slot_transistors, all_transistors, ids_map,
       transistor_sizing, spec, tech)
   print(bias_feasible, dc_warn)

.. code-block:: text

   False  ['tail current source has insufficient saturation headroom (-41 mV available vs 180 mV
           Vdsat at Vcm=0.50 V) — the input-pair bias current will fall short; ...']

Expected here: a PMOS tail at 1.0 V mid-rail is headroom-starved (the issue
#74/#76 advisory).  Raising ``vdd`` (e.g. to 1.8 V) clears it.

.. note::

   These two passes currently live in ``dc_op.py`` and ``headroom.py``; a pending
   refactor consolidates them into a single ``bias.py`` exposing the same
   ``check_dc_operating_point`` function.

Step 10 — size non-load resistors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.resistors.size_resistors` sizes
source-degeneration, resistor-tail, resistor-bias and CMFB-sense resistors,
returning their small-signal effects: ``gm1_factor`` (degeneration on input gm),
``gd_tail_override`` (resistor tail on CMRR), ``gd_out_extra`` (CMFB loading).

.. code-block:: python

   from circuitgenome.sizer.gmid.resistors import size_resistors
   extra_r, gm1_factor, gd_tail_override, gd_out_extra = size_resistors(
       blocks, slot_resistors, ids_map, transistor_sizing, model, spec, tech, intent)
   resistors = {**resistors, **extra_r}
   print(extra_r, gm1_factor, gd_tail_override, gd_out_extra)

.. code-block:: text

   {}  1.0  None  0.0        # this variant has no degeneration / resistor-tail / CMFB

Step 11 — cascode-aware first-stage rout override
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If there's a cascode load, :func:`~circuitgenome.sizer.gmid.blocks.node_rout`
computes the first-stage output resistance cascode-aware (the ``gm·ro·ro`` boost
a single-``gds`` estimate misses), treating the input-pair tail node as AC ground.

.. code-block:: python

   from circuitgenome.sizer.gmid.blocks import node_rout
   rout1_override = None
   if blocks.has_cascode_load():
       out_net = blocks.first_stage_out_net()
       all_mos = [d for d, _s in all_transistors.values()]
       stop = frozenset({blocks.tail_net()}) if blocks.tail_net() else frozenset()
       rout1_override = node_rout(out_net, all_mos, model, transistor_sizing, stop)
   print(blocks.has_cascode_load(), rout1_override)

.. code-block:: text

   False  None       # no cascode load in this topology → analytical rout used as-is

Step 12 — evaluate analytical metrics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.shared.metrics._evaluate_metrics` produces the
analytical, ngspice-free estimate of gain / GBW / PM / etc., fed all the override
hooks from steps 10–11.

.. code-block:: python

   from circuitgenome.sizer.shared.metrics import _evaluate_metrics
   metrics, margins = _evaluate_metrics(
       transistor_sizing, slot_transistors, cc_pf, tech, spec, model,
       cc2_pf=cc2_pf, gd_load_r=gd_load_r, rout1_override=rout1_override,
       gm1_factor=gm1_factor, gd_tail_override=gd_tail_override, gd_out_extra=gd_out_extra)
   print({k: round(v, 2) for k, v in metrics.items()})

.. code-block:: text

   {'gain_db': 47.03, 'gbw_hz': 993689.98, 'phase_margin_deg': 89.21, 'slew_rate_vps': 1500000.0,
    'power_w': 0.0, 'cmrr_db': 34.26, 'psrr_db': 49.47}

Step 13 — package the SizingResult
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~circuitgenome.sizer.gmid.gmid_sizer.size_gmid` returns a
:class:`~circuitgenome.sizer.shared.models.SizingResult` with
``solver_status="GMID"``, the transistor sizings, resistors, compensation caps,
metrics, the ``bias_feasible`` verdict, the resolved ``transistor_intents``, and
the accumulated warnings (topology + ceiling + geometry + DC).

.. code-block:: python

   from circuitgenome.sizer.shared.models import SizingResult
   result = SizingResult(
       transistors=transistor_sizing, cc_pf=cc_pf, metrics=metrics, margins=margins,
       solver_status="GMID", cc2_pf=cc2_pf,
       warnings=topo_warn + ceil_warn + geom_warn + dc_warn,
       resistors=resistors, bias_feasible=bias_feasible,
       transistor_intents=tintents)
   print(result.solver_status, result.bias_feasible, len(result.transistors), len(result.warnings))

.. code-block:: text

   GMID  False  10  2

In practice you never assemble this by hand — the whole pipeline is one call:

.. code-block:: python

   from circuitgenome.sizer.gmid import size_gmid
   result = size_gmid(parsed, recognize(parsed), fbr, topo, tech, spec)

----

See also
--------

* :doc:`sizing_flow` — the Level-1 (Shichman-Hodges) analytical flow with CP-SAT,
  used for the card-less ``generic`` technology.
* :mod:`circuitgenome.sizer.gmid.intent` — the design-intent hierarchy
  (``GmIdIntent``, ``BlockIntent``, ``TransistorIntent``).
* :func:`circuitgenome.sizer.sizer.size_circuit` — the technology-routing entry point.
