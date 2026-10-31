# Stop 4 — gmid/: the gm/Id sizing pipeline

**Files** (all under `circuitgenome/sizer/gmid/` unless noted, 3,375 lines):

| Reading order | File | Lines |
|---|---|---|
| 4.1 | [`gmid_sizer.py`](../../../circuitgenome/sizer/gmid/gmid_sizer.py) | 201 |
| 4.2 | [`analyze.py`](../../../circuitgenome/sizer/gmid/analyze.py) | 187 |
| 4.3 | [`intent.py`](../../../circuitgenome/sizer/gmid/intent.py) | 327 |
| 4.4 | [`plan.py`](../../../circuitgenome/sizer/gmid/plan.py) | 137 |
| 4.5 | [`geometry.py`](../../../circuitgenome/sizer/gmid/geometry.py) | 332 |
| 4.6 | [`bias.py`](../../../circuitgenome/sizer/gmid/bias.py) | 267 |
| 4.7 | [`stage_interface.py`](../../../circuitgenome/sizer/gmid/stage_interface.py) | 390 |
| 4.8 | [`rnmc_refine.py`](../../../circuitgenome/sizer/gmid/rnmc_refine.py) | 156 |
| 4.9 | [`two_stage_refine.py`](../../../circuitgenome/sizer/gmid/two_stage_refine.py) | 83 |
| 4.10 | [`resistors.py`](../../../circuitgenome/sizer/gmid/resistors.py) | 308 |
| 4.11 | [`bias_levels.py`](../../../circuitgenome/sizer/gmid/bias_levels.py) | 236 |
| 4.12 | [`evaluate.py`](../../../circuitgenome/sizer/gmid/evaluate.py) | 122 |
| companion | [`docs/theory/gmid_sizing_flow.rst`](../../theory/gmid_sizing_flow.rst) | 629 |

**Time**: 6–8 h.

**After this stop you can**:
- Trace `circuit_0010` from FBR slots to a `SizingResult` and name the phase
  and file that produced every W, L and current.
- Explain why each device lands at the gm/Id and L it does, and say which
  rule (spec, design intent, swing, mirror ratio, repair) set it.
- Say which checks feed `bias_feasible`, and which repairs or re-plans can
  change a sizing after the first geometry pass.

**Advice**: keep `gmid_sizer.py` open in a second window the whole time. It
is only 200 lines and it is the map. Every other file in this stop is one box
in it.

---

## Background for this stop

Concepts from [00_background.md](00_background.md) this stop uses all the time:

- **The method itself**: [gm/Id and the LUT](00_background.md#gmid) (pick
  gm/Id and L, the table gives current per µm, so W = Id ÷ (Id/W)) and
  [inversion levels](00_background.md#inversion) (gm/Id 6 is strong, 24 is
  weak).
- **Currents are not chosen**: [KCL](00_background.md#kcl). Every device
  current follows from `ibias` and the wiring.
- **The building blocks being sized**: [diff pair and tail](00_background.md#diff-pair),
  [current mirror](00_background.md#current-mirror), [loads](00_background.md#loads),
  [cascode](00_background.md#cascode), [bias generator](00_background.md#bias-generator),
  [common-source stage](00_background.md#common-source).
- **What can go wrong at DC**: [operating regions](00_background.md#regions),
  [Vdsat and headroom](00_background.md#vdsat), [headroom and stacking](00_background.md#headroom),
  [stage interface](00_background.md#stage-interface), [output swing](00_background.md#swing).
- **Where the gm targets come from**: [GBW](00_background.md#gbw),
  [phase margin](00_background.md#phase-margin), [Miller compensation](00_background.md#miller),
  [nested Miller / RNMC](00_background.md#nested-miller), [CMRR](00_background.md#cmrr).
- **The overall plan**: [search vs forward pass](00_background.md#two-strategies)
  and [analytical estimate vs SPICE](00_background.md#analytical-vs-spice).

Four ideas that are specific to this stop:

**1. The LUT has a floor and a ceiling.** The gf180mcu table only covers
gm/Id from 6 to 24 /V. A device cannot be sized outside that range. If the
spec asks for *less* gm than 6·Id, the device is still built at gm/Id 6, so it
delivers *more* gm than asked
([`GmIdModel.realized_gm`](../../../circuitgenome/sizer/physics/device_model.py#L357)).
If the spec asks for *more* than 24·Id, the request is capped and a warning
says the design will fall short (the "weak-inversion ceiling").
*Analogy*: a bike with only gears 6 to 24. Ask for gear 2 and you get gear 6;
ask for gear 30 and you get gear 24 plus a note that you will be slow.

**2. Three sizing roles.** Every transistor is sized in one of three ways
(`SIGNAL`, `CURRENT_SOURCE`, `CASCODE`, defined in
[`device_model.py`](../../../circuitgenome/sizer/physics/device_model.py)):

| Role | Who | How its gm/Id is chosen | Its L |
|---|---|---|---|
| `SIGNAL` | input pair, gain-stage driver | **solved**: `gm_required / Id` | 2 × L_min |
| `CURRENT_SOURCE` | tail, mirror, bias generator, stage load | **fixed**: 10 /V (strong-ish) | 4 × L_min |
| `CASCODE` | stacked current-source device | **fixed**: 8 /V (strong) | 3 × L_min |

L is always a multiple of the technology's minimum length (`L_min` = 0.28 µm
on gf180mcu), so 2× = 0.56 µm and 4× = 1.12 µm. A long L makes a better
current source ([output resistance](00_background.md#rout)); a shorter L keeps
a signal device fast ([ft](00_background.md#ft)).

**3. Repair, don't search.** The pipeline is a straight line. Each step
receives a sizing, and either passes it through or returns a *new* repaired
copy (it never edits its input). A failed repair does not stop the pipeline.
It adds a warning and sets `bias_feasible = False`. *Analogy*: an assembly
line with inspectors. An inspector can swap a part, or stamp the product
"reject", but the line never runs backwards.

**4. The width grid and fingers.** Widths snap to the 0.005 µm grid.
[`GridSpec.snap`](../../../circuitgenome/sizer/models.py#L74) also caps a width
at the widest single device (100 µm on gf180).
[`GridSpec.snap_total`](../../../circuitgenome/sizer/models.py#L79) does not
cap. A wider device is drawn as several parallel *fingers* at the end
([`_with_fingers`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L195)).
Mirror outputs use `snap_total`, because a mirror's current *is* its width
ratio, so a capped mirror output would carry less current than planned.

---

## The map: phases, files, outputs

The pipeline has five phases. Phase 4 ("Size") is the big one and spans most
of the files.

| Phase | What it decides | File(s) | Output type |
|---|---|---|---|
| 1 Analyze | which devices form which block; load kind; cascodes | `analyze.py` (on top of `physics/circuit_view.py`) | `GmIdCircuitView` |
| 2 Bias currents | every device's quiescent current; resistor-load values | `plan.py::assign_currents` (calls `physics/preprocess.assign_ids`, `size_load_resistors`) | `CurrentPlan` |
| 3 Plan | gm each stage needs, Cc, per-device gm/Id and L | `plan.py::plan_devices`, `intent.py` (calls `preprocess.compute_requirements`) | `SizingPlan` |
| 4a Geometry | W and L of every device | `geometry.py` | `dict[str, TransistorSizing]`, warnings, `bool` |
| 4b DC bias | does the tail fit under the pair? repair it | `bias.py` | same triple |
| 4c Stage interface | does stage 1's output level suit stage 2? repair it | `stage_interface.py` | same triple |
| 4 re-plan loops | redo Phase 3 on the sized circuit, re-run 4a–4c | `rnmc_refine.py`, `two_stage_refine.py` | `(SizingPlan, changed)` |
| 4d Resistors | degeneration, tail, bias, CMFB-sense, nulling resistors | `resistors.py` | `({ref: Ω}, MetricModifiers)` |
| 4e Bias levels | constructed bias generator's level devices and resistors | `bias_levels.py` | `(sizing, {ref: Ω})` |
| 4f Swing checks | follower level shift, DDA CMFB sense range | `physics/stage_chain.py` | `(warnings, bool)` |
| 5 Evaluate | the eight metrics and their margins | `evaluate.py` | `(metrics, margins, notes)` |
| assemble | fingers, resistors, caps, warnings | `gmid_sizer.py` | `SizingResult` |

### Diagram of `size_gmid` and `_size_planned`

```
size_gmid(parsed, sr, fbr, topology, tech, spec, intent)            gmid_sizer.py:73
│
├─ Phase 1  view     = analyze_circuit(fbr, topology)                 analyze.py
├─ Phase 2  currents = assign_currents(view, spec, tech, intent)      plan.py
│
├─ spec.cmrr_min_db set  AND  reports_nodal_rejection(view)?          gmid_sizer.py:94
│     no ──► _size_planned(view, currents, ...)                       (circuit_0010: no CMRR target)
│     yes ─► lift_nodal_cmrr(size = λ gm1: _size_planned(..., cmrr_gm1=gm1))
│              best = size(0.0)
│              up to 2×: raise gm1 by the CMRR shortfall, re-size,
│                        keep only if CMRR rose AND bias kept AND no other margin worse
│
_size_planned(view, currents, spec, tech, intent, cmrr_gm1)          gmid_sizer.py:101
│
├─ Phase 3  plan = plan_devices(view, currents, spec, tech, intent, cmrr_gm1)
│
├─ Phase 4  ┌─ _size(plan) ── inner function ───────────────────────────────┐
│           │  sizing, w1, geom_ok = assign_geometry_gmid(...)   geometry.py │
│           │  sizing, w2, bias_ok = check_dc_operating_point(...) bias.py   │
│           │  sizing, w3, si_ok   = check_stage_interface(...)  stage_interface.py
│           │  return sizing, w1+w2+w3, (bias_ok AND si_ok AND geom_ok)      │
│           └────────────────────────────────────────────────────────────────┘
│   sizing, warnings, feasible = _size(plan)                     first pass
│
│   RNMC loop, up to 3 rounds  (no-op unless compensation_scheme == RNMC)
│   │  refined, changed = refine_rnmc_plan(..., hold_gm2)
│   │  not changed ─► stop
│   │  resized = _size(refined)
│   │  re-size broke a sound bias AND not hold_gm2 ─► hold_gm2 = True, retry (discard)
│   │  otherwise ─► adopt refined plan + resized sizing
│
│   Two-stage loop, up to 2 rounds  (no-op unless a two-stage, non-NMC/RNMC circuit misses GBW/PM)
│   │  refined, changed = refine_two_stage_plan(...)
│   │  not changed ─► stop
│   │  resized = _size(refined)
│   │  re-size broke a sound bias ─► stop (discard)
│   │  otherwise ─► adopt
│
│   extra_r, modifiers = size_resistors(...)                      resistors.py
│   sizing, level_r    = tune_bias_levels(...)                    bias_levels.py
│   fol_w, fol_ok      = check_follower_swing(...)                physics/stage_chain.py
│   cm_w,  cm_ok       = check_cmfb_sense_swing(...)              physics/stage_chain.py
│   feasible = feasible AND fol_ok AND cm_ok
│
├─ Phase 5  metrics, margins, notes = evaluate_circuit(...)       evaluate.py
│
└─ SizingResult(transistors = _with_fingers(sizing), resistors = load + extra + level,
                capacitors = size_compensation_caps(...), bias_feasible = feasible,
                solver_status = "GMID", warnings = fol + cm + view + plan + size + eval + adoption)
```

How the `bias_feasible` verdict is built: it is a plain AND of five
yes/no answers. Any one "no" makes the whole verdict "no".

```
bias_feasible = geom_ok   (geometry.py:   output device can meet its swing Vdsat budget)
            AND bias_ok   (bias.py:       tail fits under the pair; cascode tail stack fits)
            AND si_ok     (stage_interface.py: stage 1 output level inside stage 2's window)
            AND fol_ok    (stage_chain.check_follower_swing: follower level shift vs swing spec)
            AND cm_ok     (stage_chain.check_cmfb_sense_swing: DDA CMFB sense range vs swing spec)
```

`resistors.py`, `bias_levels.py` and `evaluate.py` never change it. Later,
for LUT techs, the SPICE `.op` check in `verify/op.py` has the final say
(Stop 6).

### Running example for this stop

The snippet below runs Phases 1 to 4b on `circuit_0010` exactly the way
`_size_planned` calls them. Run it from the repo root. Every number quoted in
sections 4.2 to 4.6 comes from it (gf180mcu,
`examples/two_stage_se_specs/spec_gf180.yaml`, `main` @ `8571bf9`).

```python
# PYTHONPATH=$PWD python this_file.py
from pathlib import Path
from circuitgenome.recognizer import parse, recognize, assign_slots
from circuitgenome.synthesizer.loader import load_topologies
from circuitgenome.sizer import load_spec, load_tech
from circuitgenome.sizer.gmid.analyze import analyze_circuit
from circuitgenome.sizer.gmid.plan import assign_currents, plan_devices
from circuitgenome.sizer.gmid.geometry import assign_geometry_gmid
from circuitgenome.sizer.gmid.bias import check_dc_operating_point
from circuitgenome.sizer.gmid.intent import DEFAULT_INTENT as intent

netlist = Path("circuits/two_stage_se/circuit_0010_flat.ckt").read_text()
topo = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
fbr = assign_slots(recognize(parse(netlist)), topo)
tech = load_tech("gf180mcu"); spec = load_spec("examples/two_stage_se_specs/spec_gf180.yaml")

view = analyze_circuit(fbr, topo)                                   # Phase 1
cur = assign_currents(view, spec, tech, intent)                     # Phase 2
plan = plan_devices(view, cur, spec, tech, intent)                  # Phase 3
sizing, gw, geom_ok = assign_geometry_gmid(                         # Phase 4a
    plan.model, view.all_transistors, view.slot_transistors, cur.ids_map,
    plan.tintents, plan.gm_req_map, tech,
    vod_max_map=plan.vod_max_map, wide_refs=plan.wide_refs)
sizing, dw, bias_ok = check_dc_operating_point(                     # Phase 4b
    plan.model, view.blocks, view.slot_transistors, view.all_transistors,
    cur.ids_map, sizing, spec, tech)
print(cur.ids_map, plan.gm_req_map, plan.cc_pf, plan.tintents, sizing, bias_ok)
```

For `circuit_0010` the later steps change nothing. The stage-interface check
passes untouched, `refine_rnmc_plan` returns `changed=False` (not an RNMC
circuit), and `refine_two_stage_plan` returns `changed=False` (the full model
already meets GBW and phase margin). So the geometry printed after Phase 4b is
the final sizing in the README table, apart from fingers.

---

## 4.1 `gmid_sizer.py` (201 lines)

**Job in one sentence.** Run the five phases in order, wire each phase's
output into the next, and pack everything into one `SizingResult`.

**Design knowledge it encodes.**
- The analog designer's order of work: [DC first, small signal second](00_background.md#small-signal).
  Currents (Phase 2) and geometry plus DC checks (Phase 4) come before any
  metric (Phase 5).
- [Forward pass, not search](00_background.md#two-strategies): no solver, no
  objective function. Just a fixed sequence with a few bounded re-plan loops.
- Some rules of thumb are not accurate enough, so the circuit is sized once,
  measured with a better model, and re-planned:
  [RNMC damping](00_background.md#nested-miller), the two-stage
  [GBW](00_background.md#gbw)/[PM](00_background.md#phase-margin) full model,
  and the nodal [CMRR](00_background.md#cmrr).

**Walkthrough.**

1. [`gmid_sizer.py:73`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L73)
   `size_gmid` takes the same arguments as every sizer, plus `intent`. The
   dispatcher passes `intent_for_tech(tech.name)`
   ([`sizer.py:56`](../../../circuitgenome/sizer/sizer.py#L56)). `parsed` and
   `sr_result` are never read (README simplification #3).
2. [`gmid_sizer.py:89`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L89)
   Phase 1, `analyze_circuit`: the structural view (4.2).
3. [`gmid_sizer.py:92`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L92)
   Phase 2, `assign_currents`: the currents, fixed by [KCL](00_background.md#kcl) (4.4).
   They are computed **once**, outside every loop. No later re-plan changes a
   current.
4. [`gmid_sizer.py:94-98`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L94)
   The CMRR fork. If the spec has a CMRR target *and* the circuit's CMRR is
   computed by the nodal solve (`reports_nodal_rejection`,
   [`stage_chain.py:724`](../../../circuitgenome/sizer/physics/stage_chain.py#L724)),
   the whole of Phases 3–5 is wrapped by
   [`lift_nodal_cmrr`](../../../circuitgenome/sizer/physics/preprocess.py#L649)
   (issue #296). It sizes once with no CMRR floor, then up to
   [`_CMRR_PASSES`](../../../circuitgenome/sizer/physics/preprocess.py#L48) = 2
   more times with a raised input-pair gm. It keeps a re-size only if the CMRR
   went up and nothing else got worse. This is the
   [nodal CMRR](00_background.md#cmrr) story: on these circuits CMRR ≈
   gm1 / |gds1 − gds2|, so more gm1 helps. `_model_for` is a private helper
   imported from `plan.py` for this one call (README simplification #2).
5. [`gmid_sizer.py:115`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L115)
   Phase 3, `plan_devices`: gm targets, Cc, per-device intent (4.4).
6. [`gmid_sizer.py:118-129`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L118)
   The inner function `_size(plan)` is Phase 4's core: geometry → DC bias
   repair → stage-interface repair. Each returns `(sizing, warnings, ok)`. The
   three `ok` flags are AND-ed. It is a function because the re-plan loops call
   it again with a new plan.
7. [`gmid_sizer.py:136-147`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L136)
   The RNMC loop. It runs up to `_RNMC_PASSES + 1` = 3 rounds
   ([line 67](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L67)). If raising
   gm2 breaks a bias that was sound, that round is thrown away and the next
   round runs with `hold_gm2=True`, so Cc2 and gm3 do the damping instead. This
   is the [RNMC](00_background.md#nested-miller) rule "raise gm2, then Cc2,
   cap gm3 last", applied to the *sized* circuit. Details in 4.8.
8. [`gmid_sizer.py:151-158`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L151)
   The two-stage loop, up to `_TWO_STAGE_PASSES` = 2 rounds. It raises gm1/gm2
   to what the full loop model needs for GBW and PM (issue #274). If a re-size
   breaks a sound bias, the loop stops and keeps the previous sizing. Details
   in 4.9.
9. [`gmid_sizer.py:159-169`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L159)
   The rest of Phase 4: resistors (4.10), bias levels (4.11), then two swing
   checks the sizing cannot fix. Their flags are AND-ed into `bias_feasible`
   at [line 169](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L169).
10. [`gmid_sizer.py:172`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L172)
    Phase 5, `evaluate_circuit` (4.12).
11. [`gmid_sizer.py:176-192`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L176)
    Assemble the result. Transistors go through `_with_fingers`. Resistors
    merge the Phase 2 load resistors with the Phase 4 ones. Caps come from
    `plan.cc_pf`/`cc2_pf`. `open_loop_gain_measurable` flags a gain too large
    for SPICE's rig to measure. Warnings are ordered so the swing verdicts
    come first, because the designer reports a rejection by the first warning.
12. [`gmid_sizer.py:195`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L195)
    `_with_fingers`: any device wider than `tech.width.max` gets
    `fingers = ceil(W / w_max)`. The total W is unchanged.

**Running example.** `spec_gf180.yaml` has no `cmrr_min_db`, so
`circuit_0010` takes the direct `_size_planned` path. It reaches `_size` once,
both loops exit on their first check, and `_with_fingers` splits
`mp1_second_stage` (105.21 µm > 100 µm) into 2 fingers. That is the only
`fingers=2` in the README's sizing.

**Test to read.**
- [`test_gf180.py:130`](../../../tests/test_gf180.py#L130)
  `test_gf180_sizes_via_gmid_path`: a gf180 circuit comes back with
  `solver_status == "GMID"` and every L on the gf180 grid.
- [`test_gf180.py:168`](../../../tests/test_gf180.py#L168)
  `test_gf180_sizing_marks_devices_wider_than_one_pdk_device`: every device's
  `w_um / fingers` fits `width.max`, using the fewest fingers that fit.
- [`test_sizer.py:1754`](../../../tests/test_sizer.py#L1754)
  `test_a_short_nodal_cmrr_is_lifted_by_the_input_pair_gm`: the
  `lift_nodal_cmrr` wrapper meets a CMRR target 4 dB above the free design by
  widening the input pair, without failing more specs. The parametrized
  [`test_sizer.py:1811`](../../../tests/test_sizer.py#L1811) checks the
  "keep only if nothing else got worse" rule.

**Check yourself.**
1. Which phase's output is computed once and never revised by any loop?
2. A re-size in the two-stage loop makes the tail fall out of saturation.
   What does `_size_planned` do with it?
3. Name the five flags AND-ed into `bias_feasible`.

<details><summary>Answers</summary>

1. Phase 2, the `CurrentPlan` (`currents`). It is computed in `size_gmid`
   before both `_size_planned` and the CMRR wrapper, and passed in unchanged.
2. It discards it and stops the loop (`break` at
   [line 157](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L157)). The
   previous plan and sizing stay.
3. `geom_ok` (swing budget in geometry), `bias_ok` (tail headroom / cascode
   stack), `si_ok` (stage interface), `fol_ok` (follower swing), `cm_ok`
   (DDA CMFB sense range).
</details>

---

## 4.2 `analyze.py` (187 lines)

**Job in one sentence.** Take the FBR's slot assignments and turn them into a
read-only, typed view of the op-amp: which devices form each block, what kind
of load the first stage has, and which devices are cascodes.

**Design knowledge it encodes.**
- [Loads](00_background.md#loads): the first-stage load's *kind* (mirror,
  cascode, resistor, plain current source) decides gain and headroom.
- [Current mirror](00_background.md#current-mirror): a mirror is recognised by a
  diode-connected device (gate wired to its own drain).
- [Cascode](00_background.md#cascode): a cascode is recognised by stacking (one
  device's source on another's drain).
- [Stage interface](00_background.md#stage-interface): the first stage's output
  net is the second stage's signal gate.

**Walkthrough.**

1. [`analyze.py:172`](../../../circuitgenome/sizer/gmid/analyze.py#L172)
   `analyze_circuit` first calls the shared
   [`physics/circuit_view.analyze_circuit`](../../../circuitgenome/sizer/physics/circuit_view.py#L198)
   (Stop 3): slot lists, deduplicated `all_transistors`, orphan adoption,
   topology-mismatch warnings, `compensation_scheme`. It then copies those
   fields into a `GmIdCircuitView` and adds two more: `blocks` and
   `cascode_refs`.
2. [`analyze.py:161`](../../../circuitgenome/sizer/gmid/analyze.py#L161)
   `GmIdCircuitView` is a `CircuitView` subclass with `blocks: OpAmpBlocks`
   and `cascode_refs: set[str]`. It is the only thing Phase 1 hands on.
3. [`analyze.py:141`](../../../circuitgenome/sizer/gmid/analyze.py#L141)
   `build_blocks` makes one `Block` per slot. Only the `load` slot is
   classified (`classify_load`). Every other block gets `LoadKind.NONE`. A
   slot with only resistors (a resistor load) still gets a block. The
   fully-differential flag is just "is there a `second_stage_p/_n` or
   `third_stage_p/_n` slot".
4. [`analyze.py:63`](../../../circuitgenome/sizer/gmid/analyze.py#L63)
   `classify_load` checks in priority order: stacked → `CASCODE`; a
   diode-connected device → `MIRROR`; resistors → `RESISTOR`; any MOSFET →
   `CURRENT_SOURCE`. Cascode wins over mirror because a cascoded mirror is
   both, and the cascode is what costs headroom. The two detectors are
   [`_has_diode`](../../../circuitgenome/sizer/gmid/analyze.py#L37) (g == d)
   and [`_has_cascode`](../../../circuitgenome/sizer/gmid/analyze.py#L42) (some
   device's source is another device's drain).
5. [`analyze.py:76`](../../../circuitgenome/sizer/gmid/analyze.py#L76)
   `Block` holds a slot's MOSFETs and resistors. `signal_device` is the first
   device whose gate is a signal net
   ([`is_signal_device`](../../../circuitgenome/sizer/physics/taxonomy.py#L38):
   the gate is not a rail, `ibias`, `net_bias*`, `*_pref` or `*_ncasc`).
   `is_cascode` also runs `_has_cascode`, because only the load is classified
   and the tail needs the same question answered (issue #145).
6. [`analyze.py:100`](../../../circuitgenome/sizer/gmid/analyze.py#L100)
   `OpAmpBlocks` adds shortcuts: `input_pair`, `load`, `tail`,
   `has_cascode_load()`, and
   [`first_stage_out_net()`](../../../circuitgenome/sizer/gmid/analyze.py#L127),
   which reads the gate of the next stage's signal device. That is the
   [stage-interface](00_background.md#stage-interface) node `stage_interface.py`
   works on.
7. [`analyze.py:49`](../../../circuitgenome/sizer/gmid/analyze.py#L49)
   `cascode_device_refs`: a device is a cascode if it is not a signal device,
   its source is not a rail, and its source is the drain of another device in
   the same slot. These refs become the `CASCODE` role in `intent.py`.

**Running example.** For `circuit_0010` the view is:

| Block | Devices | `load_kind` | `signal_device` |
|---|---|---|---|
| `input_pair` | m1_input_pair, m2_input_pair | none | m1_input_pair |
| `load` | m1_load, m2_load | **mirror** (m1_load has g = d = `net_diff1`) | m1_load |
| `tail_current` | m1_tail_current, m2_tail_current | none | — |
| `bias_gen` | mnref, mn5, mp5, mn7 *and* m1_tail_current | none | — |
| `second_stage` | mn1_second_stage, mp1_second_stage | none | mn1_second_stage |

`is_fully_differential = False`, `first_stage_out_net() = net_mid`,
`cascode_refs = set()` (nothing is stacked), `compensation_scheme = None`
(a plain two-stage Miller circuit), and there are no warnings and no adopted
orphans.

Two things to notice:
- `m1_tail_current` is in *two* slots. The FBR also counts the tail mirror's
  diode as part of the bias generator. The shared dedup gives each ref one
  slot by priority (`input_pair > load > tail_current > ... > bias_gen`,
  [`circuit_view.py:148`](../../../circuitgenome/sizer/physics/circuit_view.py#L148)),
  so in `all_transistors` it is a `tail_current` device. The `bias_gen`
  `Block` still lists it.
- `m1_load`'s `signal_device` is m1_load itself. Its gate `net_diff1` is not a
  bias-net name, so `is_signal_device` says True. This matters in 4.3.

**Older pages (read for intuition, not for line numbers).**
[`analyze_explained.html`](../../_extra/walkthrough/gmid/analyze_explained.html)
explains orphan adoption (`_adopt_orphan_mosfets`) and the `CircuitView`
assembly. That code now lives in `physics/circuit_view.py`, not here.
[`blocks_explained.html`](../../_extra/walkthrough/gmid/blocks_explained.html)
Parts 2, 3, 6, 7 and 8 match this file (detectors, `classify_load`,
`cascode_device_refs`, `Block`, `OpAmpBlocks`, `build_blocks`). Its Parts 4–5
(`_looking_in_drain`, `node_rout`) now live in `physics/stage_chain.py`
(Stop 3).

**Test to read.** [`test_gmid_blocks.py`](../../../tests/test_gmid_blocks.py)
- [`:17`](../../../tests/test_gmid_blocks.py#L17) `test_classify_load_kinds`:
  hand-made device lists give each of the five `LoadKind`s.
- [`:37`](../../../tests/test_gmid_blocks.py#L37) `test_build_blocks_load_kind`:
  a mirror load is `MIRROR`; a resistor-only load slot still gets a block.
- [`:56`](../../../tests/test_gmid_blocks.py#L56) `test_tail_is_cascode`: a
  stacked tail is detected structurally even though the tail is never
  classified (issue #145).
- [`:74`](../../../tests/test_gmid_blocks.py#L74)
  `test_default_construction_is_the_empty_decomposition`: an empty
  `GmIdCircuitView()` works and every accessor returns "absent".

**Check yourself.**
1. A load slot has a diode-connected NMOS with a cascode stacked on it. Which
   `LoadKind` does it get, and why that one?
2. Why does `Block.is_cascode` not trust `load_kind` alone?
3. Which net does `first_stage_out_net()` return for `circuit_0010`, and how
   is it found?

<details><summary>Answers</summary>

1. `CASCODE`. `_has_cascode` is checked before `_has_diode`, because the
   stacking is what eats headroom, and `bias.py`/`stage_interface.py` need to
   know that.
2. Only the `load` slot goes through `classify_load`. For every other slot
   (the tail especially) `load_kind` is `NONE`, so the structural
   `_has_cascode` fallback is the only way to see a stacked tail.
3. `net_mid`: the gate of `second_stage`'s signal device, `mn1_second_stage`.
</details>

---

## 4.3 `intent.py` (327 lines)

Read this before `plan.py`: `plan.py` only calls it.

**Job in one sentence.** Say, for every transistor, which kind of building
block it belongs to and therefore which gm/Id region and channel length it
should be sized at, with a written reason.

**Design knowledge it encodes.**
- [Inversion levels](00_background.md#inversion): gm/Id *is* the choice of
  inversion region. Signal devices get theirs from the spec; everything else
  gets a fixed region.
- [Output resistance](00_background.md#rout): current sources use a long L
  (4 × L_min) for high rout.
- [Cascode](00_background.md#cascode): cascodes use strong inversion (gm/Id 8)
  for a small Vdsat, because they stack.
- [Source follower](00_background.md#source-follower): gain ≈ 1, so no gm
  target. It gets a fixed gm/Id 15 and short L for speed.

**The three levels.** The module docstring draws them:

```
Circuit intent (spec)      SizingSpec: gain, GBW, PM, swing, ...     the "what"
        ▼
Functional-block intent    BlockIntent: role, gm/Id, L multiple, rationale   (tunable)
        ▼
Transistor intent          TransistorIntent: the block intent copied onto one device
```

*Analogy*: a building code (spec), a set of standard part types such as "load
bearing beam" or "partition wall" each with a default thickness (block
intent), and the label on each individual beam saying which part type it is
(transistor intent).

**The design-intent table, mapped to the background.** These are the defaults
in [`DEFAULT_BLOCK_INTENTS`](../../../circuitgenome/sizer/gmid/intent.py#L124),
built from the constants at
[`intent.py:76-85`](../../../circuitgenome/sizer/gmid/intent.py#L76):

| Block | Role | gm/Id (1/V) | [Inversion](00_background.md#inversion) | L | Why |
|---|---|---|---|---|---|
| `input_stage` | SIGNAL | solved | whatever the spec needs (6–24) | 2× | high gm1 for [GBW](00_background.md#gbw), matching |
| `gain_stage` | SIGNAL | solved | whatever the spec needs | 2× | gm2 for gain and [PM](00_background.md#phase-margin) |
| `output_stage` | SIGNAL | 15 | moderate | 1× | [follower](00_background.md#source-follower): low 1/gm, small Vov for swing |
| `active_load` | CURRENT_SOURCE | 10 | strong-ish | 4× | accurate current, high rout |
| `stage_load` | CURRENT_SOURCE | 10 | strong-ish | 4× | high rout for the stage's gain |
| `tail_current` | CURRENT_SOURCE | 10 | strong-ish | 4× | [headroom](00_background.md#headroom) and rout ([CMRR](00_background.md#cmrr)) |
| `bias_generator` | CURRENT_SOURCE | 10 | strong-ish | 4× | stable references, matched L to the mirrors it feeds |
| `cmfb` | CURRENT_SOURCE | 10 | strong-ish | 4× | regulate the output common mode ([CMFB](00_background.md#cmfb)) |
| `cascode` | CASCODE | 8 | strong | 3× | small Vdsat to save stacked headroom |
| `current_source` | CURRENT_SOURCE | 10 | strong-ish | 4× | fallback for anything else |

"Solved" means `gm_id = None` here; `geometry_for` replaces it with
`gm_required / Id` (4.5).

**Walkthrough.**

1. [`intent.py:89`](../../../circuitgenome/sizer/gmid/intent.py#L89)
   `BlockIntent(role, gm_id, l_mult, rationale)`: one row of the table.
   Frozen, so it cannot be changed by accident.
2. [`intent.py:108`](../../../circuitgenome/sizer/gmid/intent.py#L108)
   `TransistorIntent` is the same data plus `ref` and `block` name.
3. [`intent.py:173-195`](../../../circuitgenome/sizer/gmid/intent.py#L173)
   Two lookup tables turn `(slot, role)` into a block name. The same slot can
   hold two roles: `second_stage` holds both the signal driver (`gain_stage`)
   and its current-source load (`stage_load`).
4. [`intent.py:198`](../../../circuitgenome/sizer/gmid/intent.py#L198)
   `functional_block(slot, is_signal, is_cascode)`. Signal is checked first,
   then cascode, then current source. Unknown slots fall back to
   `gain_stage` (signal) or `current_source` (non-signal).
5. [`intent.py:211`](../../../circuitgenome/sizer/gmid/intent.py#L211)
   `resolve_transistor_intents` loops over `all_transistors`, asks
   `is_signal_device` and `ref in cascode_refs`, and copies the matching
   `BlockIntent` onto a `TransistorIntent`. This is the whole of Level 2 →
   Level 3.
6. [`intent.py:228`](../../../circuitgenome/sizer/gmid/intent.py#L228)
   `GmIdIntent` carries the block registry plus "flat" per-role knobs
   (`signal_gm_id = 14`, `current_source_gm_id = 10`, L multiples) and two
   resistor knobs (`degeneration_factor = 0.5`, `cmfb_sense_r = 1 MΩ`). The
   flat knobs build the `GmIdPolicy` in `plan._model_for` (4.4). That policy
   drives the pre-geometry gds estimate and the gm/Id a signal device without
   a gm target falls back to (see the running example). `signal_gm_id` and
   `signal_l_mult` also seed the resistor-load bias directly
   (`plan._load_seed_v`).
7. [`intent.py:265`](../../../circuitgenome/sizer/gmid/intent.py#L265)
   `make_intent` builds a `GmIdIntent` whose block rows and flat knobs share
   one set of numbers, so a per-tech override moves both.
8. [`intent.py:321-327`](../../../circuitgenome/sizer/gmid/intent.py#L321)
   `INTENT_BY_TECH` is empty on purpose (issue #77): gf180mcu already meets
   the spec with the defaults, and sky130's failures are not fixable by
   choosing a different L. `intent_for_tech` therefore always returns
   `DEFAULT_INTENT` today.

**Running example.** The resolved intents for `circuit_0010`
(L = `l_mult` × 0.28 µm, snapped):

| Device | Block | Role | gm/Id | L (µm) |
|---|---|---|---|---|
| m1/m2_input_pair | `input_stage` | SIGNAL | solved | 0.56 |
| m1/m2_load | **`gain_stage`** | SIGNAL | solved (none given) | 0.56 |
| m1/m2_tail_current | `tail_current` | CURRENT_SOURCE | 10 | 1.12 |
| mn1_second_stage | `gain_stage` | SIGNAL | solved | 0.56 |
| mp1_second_stage | `stage_load` | CURRENT_SOURCE | 10 | 1.12 |
| mnref/mn5/mp5/mn7_bias_gen | `bias_generator` | CURRENT_SOURCE | 10 | 1.12 |

This answers "why is the tail L 1.12 but the pair L 0.56": the tail is a
current source (4 × L_min, for a high rout that keeps
[CMRR](00_background.md#cmrr) and the current stable), while the pair is a
signal device (2 × L_min, a balance of gain and speed).

The surprise is the mirror load. Its gates sit on `net_diff1`, which is not a
bias-net name, so both load devices count as *signal* devices. `"load"` is not
in the signal table, so they fall back to `gain_stage` and get L = 0.56 µm,
not the `active_load` row's 1.12 µm. They have no gm target either (only the
pair and the second-stage driver get one), so `geometry_for` uses the policy's
current-source gm/Id of 10. So in practice the `active_load` row applies to
*plain current-source* loads (gates on `net_bias*`), not to mirror loads. See
the simplification notes.

**Older page.** [`intent_explained.html`](../../_extra/walkthrough/gmid/intent_explained.html).

**Test to read.** [`test_block_intent.py`](../../../tests/test_block_intent.py)
- [`:45`](../../../tests/test_block_intent.py#L45) `test_functional_block_mapping`:
  the `(slot, role)` → block table, including "same slot, non-signal →
  `stage_load`" and "load, non-signal → `active_load`".
- [`:62`](../../../tests/test_block_intent.py#L62)
  `test_registry_is_complete_and_documented`: every row has a role and a
  rationale; signal rows have `gm_id=None` except `output_stage`.
- [`:90`](../../../tests/test_block_intent.py#L90)
  `test_make_intent_keeps_block_and_flat_in_lockstep`: one knob moves both
  the block rows and the flat fields.
- [`:116`](../../../tests/test_block_intent.py#L116)
  `test_per_block_override_is_local`: setting only `tail_current` to gm/Id 6
  changes the tail's W and Vdsat but not the input pair's W.

**Check yourself.**
1. Why does a signal device carry `gm_id=None` instead of a number?
2. A device in the `second_stage` slot has its gate on `net_bias5`. Which
   block and L does it get?
3. Why is the follower `output_stage` the one signal block with a fixed gm/Id?

<details><summary>Answers</summary>

1. Its current is fixed by KCL and its gm is required by the spec, so
   gm/Id = gm_req / Id is not a free choice. `None` means "solve it".
2. Not a signal device (gate on `net_bias*`), not a cascode, so `stage_load`:
   CURRENT_SOURCE, gm/Id 10, L = 4 × 0.28 = 1.12 µm. That is
   `mp1_second_stage`.
3. A follower has gain ≈ 1 and never gets a gm requirement, so there is
   nothing to solve from. It is fixed at 15 /V for a low output resistance
   (1/gm) and a small Vov that keeps swing.
</details>

---

## 4.4 `plan.py` (137 lines)

**Job in one sentence.** Phase 2: fix every device's current. Phase 3: turn
the spec into gm targets, compensation caps and swing budgets, and attach the
per-device intent, without creating any geometry yet.

**Design knowledge it encodes.**
- [KCL](00_background.md#kcl): currents come from `ibias` and the slot groups,
  not from optimisation.
- [GBW](00_background.md#gbw) → gm1 = 2π·GBW·Cc; [phase margin](00_background.md#phase-margin)
  → gm2 from gm1·CL / (Cc·tan(90° − PM)); [gain](00_background.md#stages) →
  gm2 from A0 / (gm1·R1·R2).
- [Miller compensation](00_background.md#miller): Cc ≈ 0.25·CL.
- [Output swing](00_background.md#swing) → a maximum Vdsat per output device.

**Walkthrough.**

1. [`plan.py:29`](../../../circuitgenome/sizer/gmid/plan.py#L29) `CurrentPlan`
   (Phase 2 output): `ids_map` (ref → A), `load_resistors` (ref → Ω),
   `gd_load_r` (1/R of the smallest load resistor, 0 for an active load).
2. [`plan.py:83`](../../../circuitgenome/sizer/gmid/plan.py#L83)
   `assign_currents` calls
   [`preprocess.assign_ids`](../../../circuitgenome/sizer/physics/preprocess.py#L220)
   (Stop 3). Its rules: `input_pair`/`load` devices share `ibias` equally
   (half-bias slots); `tail_current`/`bias_gen` carry `ibias`; second stage
   carries `ibias × second_stage_current_ratio`; cascode loads and CMFB have
   their own plans. Then it sizes rail-referenced load resistors with
   `size_load_resistors`, seeded by
   [`_load_seed_v`](../../../circuitgenome/sizer/gmid/plan.py#L69): the
   LUT's |Vgs| of the driven signal device at gm/Id 14 and 2 × L_min. That
   is the [stage-interface](00_background.md#stage-interface) idea for
   resistor loads (issue #158).
3. [`plan.py:95`](../../../circuitgenome/sizer/gmid/plan.py#L95) `_model_for`
   builds the `GmIdModel` from the tech's LUT and a `GmIdPolicy` filled from
   the intent's flat knobs. It is private, but `gmid_sizer.py` imports it
   for the CMRR wrapper (README simplification #2).
4. [`plan.py:108`](../../../circuitgenome/sizer/gmid/plan.py#L108)
   `plan_devices` (Phase 3):
   - [line 127](../../../circuitgenome/sizer/gmid/plan.py#L127): an RNMC
     template is planned with the *default* two-cap rules (`scheme=None`) and
     re-planned later on the sized circuit (`rnmc_refine.py`). An NMC
     template passes `"nested_miller"` through.
   - [line 128](../../../circuitgenome/sizer/gmid/plan.py#L128):
     [`compute_requirements`](../../../circuitgenome/sizer/physics/preprocess.py#L305)
     (Stop 3) does the spec math and returns `gm_req_map`, `vod_max_map`,
     `cc_pf`, `cc2_pf` and gm-ceiling warnings. It reads output conductances
     through the gm/Id model, so they come from the LUT.
   - [line 133](../../../circuitgenome/sizer/gmid/plan.py#L133):
     `resolve_transistor_intents` (4.3).
5. [`plan.py:43`](../../../circuitgenome/sizer/gmid/plan.py#L43) `SizingPlan`
   bundles `model`, `gm_req_map`, `vod_max_map`, `cc_pf`, `cc2_pf`,
   `tintents`, `warnings`, and `wide_refs`. `plan_devices` always leaves
   `wide_refs` empty. Only the re-plan loops fill it, to let a raised
   second-stage device grow past 100 µm as fingers (issue #266).

**Running example.**

*Phase 2, `CurrentPlan`* (`ibias` = 20 µA, `second_stage_current_ratio` = 2.5):

| Devices | Slot group | Current | Why |
|---|---|---|---|
| m1/m2_input_pair | half-bias | 10 µA each | 2 PMOS devices share `ibias`: 20 / 2. The tail's 20 µA splits evenly at rest |
| m1/m2_load | half-bias | 10 µA each | each load device carries its pair device's current |
| m1/m2_tail_current | full-bias | 20 µA | the tail supplies the whole pair |
| 4 × bias_gen | full-bias | 20 µA | each bias leg copies `ibias` |
| mn1/mp1_second_stage | second stage | 50 µA | 2.5 × 20 µA |

`load_resistors = {}` and `gd_load_r = 0.0`: the load is a mirror, not a
resistor.

*Phase 3, `SizingPlan`*, step by step through `compute_requirements`:

1. **Cc = 1.25 pF.** The pole-split floor is 0.25 × CL = 0.25 × 5 pF. The
   slew spec only sets an *upper* limit, `ibias / SR` = 20 µA / 0.3 V/µs =
   66.7 pF, which does not bind
   ([preprocess.py:422](../../../circuitgenome/sizer/physics/preprocess.py#L422)).
2. **gm1 target = 15.7 µS** = 2π × 2 MHz × 1.25 pF / k_fs, with k_fs = 1
   for a mirror load
   ([preprocess.py:434](../../../circuitgenome/sizer/physics/preprocess.py#L434)).
   That is a gm/Id of only 1.57 /V at 10 µA, far below the LUT floor of 6.
   So the pair will be built at gm/Id 6 and deliver 6 × 10 µA = **60 µS**,
   3.8 × the ask (`realized_gm`). That is why GBW comes out at about 7 MHz,
   not 2 MHz: closed form 60 µS / (2π × 1.25 pF) = 7.6 MHz; the full model
   reports 7.06 MHz.
3. **gm2 target = 415.7 µS.** Two rules compete. Gain: A0 / (gm1·R1·R2) =
   1000 / (15.7 µS × 998 kΩ × 255 kΩ) ≈ 250 µS
   ([preprocess.py:494](../../../circuitgenome/sizer/physics/preprocess.py#L494)).
   Phase margin: gm1_eff·CL / (Cc·tan 30°) = 60 µS × 5 pF / (1.25 pF × 0.577)
   = 415.7 µS
   ([preprocess.py:515](../../../circuitgenome/sizer/physics/preprocess.py#L515)).
   PM wins. It uses the gm1 the pair *will really have* (60 µS), not the
   15.7 µS asked for. At 50 µA that is gm/Id 8.31.
4. **No ceiling warnings.** Both gm/Id values are under the table's 24.
5. **`vod_max_map`**: 0.3 V for `m1_load`, `m2_load`, `mp1_second_stage`
   (from `Vdd − swing_max` = 3.3 − 3.0) and 0.3 V for `mn1_second_stage`
   (from `swing_min − Vss` = 0.3 − 0). Geometry uses only the second-stage
   entries (4.5).
6. `gm_req_map` = {pair: 15.7 µS each, mn1: 415.7 µS, mp1: 0}. The loads,
   tail and bias devices have no entry.

**Older page.** [`plan_explained.html`](../../_extra/walkthrough/gmid/plan_explained.html).

**Test to read.** No test targets `plan.py` alone. Phase 2 and 3 are pinned
through `compute_requirements` in Stop 3's tests and through
[`test_sizer.py:1223`](../../../tests/test_sizer.py#L1223)
`test_ptm45_uses_gmid_path_and_matches_pairs` (end to end on the gm/Id path).

**Check yourself.**
1. Why does `assign_currents` run once in `size_gmid` while `plan_devices`
   can run several times?
2. In `circuit_0010`, which spec actually sets gm2: gain or phase margin? And
   why does the PM rule use 60 µS rather than 15.7 µS for gm1?
3. What would change in `CurrentPlan` for `circuit_0001` (resistor load)?

<details><summary>Answers</summary>

1. Currents follow from KCL and `ibias`, which no loop changes. The plan
   depends on the sized circuit's real gm and parasitics, which the re-plan
   loops and the CMRR wrapper revise.
2. Phase margin (415.7 µS vs about 250 µS from gain). The pair cannot be
   built below gm/Id 6, so it will really deliver 60 µS; sizing gm2 for
   15.7 µS would leave the second pole too close to the real GBW.
3. `load_resistors` gets `r1_load`/`r2_load` (76 kΩ each in the README run),
   and `gd_load_r` becomes 1/76 kΩ. The resistor conductance then lowers the
   first-stage output resistance in `compute_requirements`, and k_fs drops to
   0.5.
</details>

---

## 4.5 `geometry.py` (332 lines)

**Job in one sentence.** Turn every device's current, gm target and intent
into a W and L by reading the LUT, then make matched pairs identical and
mirror outputs exact copies.

**Design knowledge it encodes.**
- [The gm/Id recipe](00_background.md#gmid): gm/Id and L → Id/W from the LUT
  → W = Id ÷ (Id/W).
- [Matched pairs](00_background.md#diff-pair) must be identical, or the pair
  is unbalanced.
- [Current mirror](00_background.md#current-mirror): output W = (I_out / I_ref)
  × W_ref at the same L.
- [Output swing](00_background.md#swing): a smaller Vdsat on the output devices
  lets the output go closer to the rails; weaker inversion gives smaller
  Vdsat.

**Walkthrough.** `assign_geometry_gmid` at
[`geometry.py:222`](../../../circuitgenome/sizer/gmid/geometry.py#L222) is a
fixed sequence of steps:

1. **Setup** ([lines 261-266](../../../circuitgenome/sizer/gmid/geometry.py#L261)).
   `_output_stage_slots` picks the slots on the output node (the second/third
   stage in a multi-stage circuit, so the first-stage load's swing entry is
   ignored there). `_mirror_tied_refs` finds mirror outputs. `cascode_load`
   notes whether the first-stage load has a cascode.
2. **Swing floor** ([lines 275-285](../../../circuitgenome/sizer/gmid/geometry.py#L275)).
   For an output-path device with a swing budget (not a mirror output, and
   not a signal device under a cascode load), call
   [`swing_gm_id_floor`](../../../circuitgenome/sizer/gmid/geometry.py#L63).
   It walks the LUT's gm/Id axis upward and returns the first gm/Id whose
   Vdsat ≤ **half** the budget
   ([`_SWING_VDSAT_FRACTION = 0.5`](../../../circuitgenome/sizer/gmid/geometry.py#L60)).
   Half, because the swing bench stops counting before the exact Vdsat point
   (issue #126). If even gm/Id 24 cannot fit the *full* budget, `feasible`
   becomes False. This is the [swing](00_background.md#swing) rule: weaker
   inversion → smaller Vdsat → more swing.
3. **LUT lookup** ([line 286](../../../circuitgenome/sizer/gmid/geometry.py#L286)).
   [`GmIdModel.geometry_for`](../../../circuitgenome/sizer/physics/device_model.py#L368)
   (Stop 3) picks gm/Id: for a SIGNAL device with a target, `gm_req / Id`,
   clamped to the table's [6, 24]; otherwise the intent's gm/Id, or the
   policy's default for that role. Then it raises gm/Id to the swing floor if
   there is one, reads Id/W, and returns W = Id / (Id/W). This is the
   [gm/Id recipe](00_background.md#gmid) steps 3–5.
4. **Snap** ([line 291](../../../circuitgenome/sizer/gmid/geometry.py#L291)).
   `snap` (capped at 100 µm), or `snap_total` (uncapped) for `wide_refs`.
   A capped ask adds a "weak-inversion ceiling" warning.
5. **Symmetry** ([`_apply_symmetry`](../../../circuitgenome/sizer/gmid/geometry.py#L121)).
   In `input_pair`, `load` and `tail_current`, devices of the same type *and
   the same planned current* copy the first one's W and L. FD halves
   (`second_stage_p` ↔ `_n`, `third_stage_p` ↔ `_n`) are matched by type.
6. **Mirror ratios** ([`_apply_mirror_ratios`](../../../circuitgenome/sizer/gmid/geometry.py#L156)).
   Group devices by (gate net, type). In a group with a diode-connected
   member, every other member gets the diode's L and
   W = `snap_total((I_out / I_ref) × W_ref)`. This is the
   [current-mirror](00_background.md#current-mirror) rule, made exact. It runs
   after symmetry, so a device that is both gets the mirror answer.
7. **Load margin** ([`_apply_load_current_margin`](../../../circuitgenome/sizer/gmid/geometry.py#L199)).
   Only for a single-ended `CURRENT_SOURCE` load fighting a MOSFET tail: the
   load gets 5 % extra width
   ([`_LOAD_CS_MARGIN`](../../../circuitgenome/sizer/gmid/geometry.py#L52)).
   Nothing in that circuit holds the node between them steady, so a deliberately
   slightly-strong load makes it settle on a safe side (issue #103).
   *Analogy*: two people pulling a rope with exactly equal force; nobody knows
   where the knot ends up. Make one slightly stronger and the knot moves to
   their side.
8. **Build and final check** ([lines 310-331](../../../circuitgenome/sizer/gmid/geometry.py#L310)).
   Make each `TransistorSizing` with Vgs and Vdsat read back from the LUT at
   the final W. Then check mirror-tied output devices against their *raw*
   swing budget, since step 2 skipped them.

**Running example.** The geometry for `circuit_0010`:

| Device | Id | gm/Id | L | W (µm) | Vgs | Vdsat | What set it |
|---|---|---|---|---|---|---|---|
| m1/m2_input_pair | 10 µA | 6.0 | 0.56 | 3.07 | −1.083 | 0.274 | ask 1.57 → LUT floor 6; Id/W = 3.26 µA/µm → 10 / 3.26 = 3.07 |
| m1/m2_load | 10 µA | 10.0 | 0.56 | 2.495 | 0.841 | 0.168 | signal, no target → policy 10; m2 mirrors m1 at ratio 1 |
| m1/m2_tail_current | 20 µA | 10.0 | 1.12 | 42.085 | −0.942 | 0.154 | intent 10; Id/W = 0.475 µA/µm → 42.08; m2 mirrors m1 (diode) |
| mn1_second_stage | 50 µA | **11.5** | 0.56 | 17.855 | 0.807 | 0.145 | solved 8.31, raised to swing floor 11.5 |
| mp1_second_stage | 50 µA | 10.0 | 1.12 | 105.21 | −0.942 | 0.154 | mirror of mp5: 2.5 × 42.085 |
| mnref/mn5/mn7_bias_gen | 20 µA | 10.0 | 1.12 | 9.76 | 0.832 | 0.171 | intent 10; mn5, mn7 mirror the `ibias` diode mnref |
| mp5_bias_gen | 20 µA | 10.0 | 1.12 | 42.085 | −0.942 | 0.154 | intent 10; diode for `net_bias5` |

The three interesting rows:

- **The input pair is in strong inversion (gm/Id 6) by accident of the spec.**
  The spec asks for so little gm1 that the table's strongest point already
  exceeds it. Its |Vgs| of 1.08 V matters next, in `bias.py`.
- **`mn1_second_stage` is set by swing, not by phase margin.** At the solved
  gm/Id 8.31 its Vdsat would be 0.20 V. The swing budget is 0.3 V
  (`output_swing_min_v`), half of it is 0.15 V, and the first LUT point under
  0.15 V is gm/Id 11.5 (Vdsat 0.145 V). So gm2 becomes 11.5 × 50 µA =
  **575 µS**, more than the 415.7 µS asked. That extra gm2 is part of why PM
  lands at 63° rather than exactly 60°.
- **`mp1_second_stage` is wider than one device.** Step 3 computes about
  105.2 µm and `snap` caps it at 100 µm. Then step 6 overwrites it:
  `mp1_second_stage` shares gate `net_bias5` with the diode `mp5_bias_gen`, so
  W = (50 / 20) × 42.085 = 105.21 µm via the uncapped `snap_total`. Capping it
  at 100 µm would have given only about 47.5 µA instead of 50 µA. It later
  becomes 2 fingers. It is mirror-tied, so the swing floor skipped it; its
  Vdsat of 0.154 V still fits the raw 0.3 V budget in step 8.

No warnings, `feasible = True`.

**Older page.** [`geometry_explained.html`](../../_extra/walkthrough/gmid/geometry_explained.html).

**Test to read.** [`test_gmid_geometry.py`](../../../tests/test_gmid_geometry.py) (on ptm45)
- [`:46`](../../../tests/test_gmid_geometry.py#L46)
  `test_realized_gm_matches_geometry_floor`: a gm ask under the table floor
  is built at the floor, and `realized_gm` predicts exactly that.
- [`:58`](../../../tests/test_gmid_geometry.py#L58) `test_mirror_ratio_is_exact`:
  a 2.5:1 current ratio gives a 2.5 × width at matched L.
- [`:79`](../../../tests/test_gmid_geometry.py#L79) `test_matched_pair_symmetry`.
- [`:145`](../../../tests/test_gmid_geometry.py#L145)
  `test_current_source_load_gets_margin` and
  [`:161`](../../../tests/test_gmid_geometry.py#L161)
  `test_no_margin_without_a_tail`: the 5 % margin applies only with a tail.
- [`:242`](../../../tests/test_gmid_geometry.py#L242)
  `test_swing_budget_floors_output_gm_id`: a 0.3 V budget pushes Vdsat to
  about 0.15 V, and gm never drops below the ask.
- [`:269`](../../../tests/test_gmid_geometry.py#L269)
  `test_swing_budget_skips_mirror_outputs` and
  [`:288`](../../../tests/test_gmid_geometry.py#L288)
  `test_swing_budget_spares_driver_under_cascode_load`: the two exclusions
  from the floor.

**Check yourself.**
1. Why do mirror outputs use `snap_total` and not `snap`?
2. Raising `mn1_second_stage`'s gm/Id from 8.31 to 11.5 makes it *wider*. Why
   is that safe for the spec?
3. Why is the swing floor skipped for a mirror output device?

<details><summary>Answers</summary>

1. A mirror output's current is set by its width ratio to the diode. Capping
   its width at 100 µm would silently cut its current. `snap_total` keeps the
   full width, and `_with_fingers` draws it as parallel devices.
2. gm = (gm/Id) × Id with Id fixed, so a higher gm/Id only *raises* gm, and a
   gm requirement is a minimum. The cost is area and slower speed (lower ft).
3. Step 6 overwrites its W from the diode reference anyway, so its inversion
   level follows the diode's. A floor applied in step 2 would not survive.
   Instead it is checked against the raw budget after step 6.
</details>

---

## 4.6 `bias.py` (267 lines)

**Job in one sentence.** Check that the tail current source has enough
voltage room under (or over) the input pair to stay saturated, repair it if
it does not, and say whether the DC bias is feasible.

**Design knowledge it encodes.**
- [Headroom and stacking](00_background.md#headroom): with a PMOS pair, the
  tail's drain sits at Vcm + |Vgs_pair|, and the tail needs at least its
  [Vdsat](00_background.md#vdsat) between that node and Vdd.
- [Operating regions](00_background.md#regions): a tail starved of headroom
  drops into triode and supplies less current, so gm1 and GBW collapse
  (issue #76).
- [Inversion](00_background.md#inversion): moving a device toward weak
  inversion shrinks both Vdsat and |Vgs|. That is the only repair knob.
- [Cascode](00_background.md#cascode): a stacked tail needs the *sum* of its
  devices' Vdsat.

*Analogy*: a stack of boxes under a ceiling. The pair's |Vgs| is the box on
top of the input level; the tail is the box between it and the ceiling. If it
does not fit, use thinner boxes: first a thinner tail, then a thinner pair.

**Walkthrough.**

1. [`bias.py:218`](../../../circuitgenome/sizer/gmid/bias.py#L218)
   `check_dc_operating_point` runs pass 1 for the input tail
   (`_apply_headroom`), then the same repair for a CMFB amplifier's tail
   (`_apply_cmfb_headroom`, issue #208). Neither changes its input mapping.
2. [`bias.py:139-148`](../../../circuitgenome/sizer/gmid/bias.py#L139)
   `_repair_tail_headroom` (shared by both) computes the headroom:
   `Vdd − (Vcm + |Vgs_pair|)` for a PMOS pair, `(Vcm − |Vgs_pair|) − Vss` for
   an NMOS pair. Vcm is mid-supply
   ([`SizingSpec.vcm`](../../../circuitgenome/sizer/models.py#L200)). If the
   tail's Vdsat fits, it returns at once with no change.
3. [`bias.py:178-180`](../../../circuitgenome/sizer/gmid/bias.py#L178)
   The repair acts on the tail's whole **mirror group** (every same-type
   device on the tail's gate net). Re-sizing them all at one gm/Id keeps
   their width ratios, so the mirror still copies the right current.
4. [`bias.py:186-197`](../../../circuitgenome/sizer/gmid/bias.py#L186)
   It builds a list of candidate pair operating points: the pair as sized,
   then every LUT gm/Id that gives the pair a smaller |Vgs| (more headroom).
   Raising the pair's gm/Id is safe for the spec: its gm only grows.
5. [`bias.py:200-215`](../../../circuitgenome/sizer/gmid/bias.py#L200)
   For each candidate, in order, find the lowest tail gm/Id whose Vdsat fits
   90 % of the headroom
   ([`_tail_gm_id_for_headroom`](../../../circuitgenome/sizer/gmid/bias.py#L41)).
   It picks the *lowest* fitting gm/Id because that keeps the tail as strong
   (high rout) as the room allows. The first candidate that really fits after
   grid snapping wins. If none fits, it keeps the closest attempt and adds an
   "insufficient saturation headroom" warning.
6. [`bias.py:238`](../../../circuitgenome/sizer/gmid/bias.py#L238)
   `bias_feasible = not any("headroom" in w for w in warnings)`: the verdict
   is read back from the warning text.
7. [`bias.py:243-266`](../../../circuitgenome/sizer/gmid/bias.py#L243)
   Pass 2, only for a stacked tail (`tail.is_cascode`).
   [`_tail_stack_to_rail`](../../../circuitgenome/sizer/gmid/bias.py#L55) walks
   from the tail node toward the rail, drain to source, and sums the Vdsat of
   every device on the way. If the sum exceeds the headroom, a "cascode tail
   current source cannot bias" warning sets `bias_feasible = False`. There is
   no repair here, only a verdict. `_tail_current_net` (a private helper from
   `physics/stage_chain.py`) finds the real tail node when degeneration
   resistors sit between the pair and the tail.

What it does *not* check: second-stage headroom, the stage-interface level
(that is `stage_interface.py`, 4.7), or anything SPICE would find. It is a
fast pre-check; `verify/op.py` gives the final DC verdict (Stop 6).

**Running example.** For `circuit_0010` (PMOS pair, PMOS tail, Vdd 3.3 V):

```
Vcm               = (3.3 + 0) / 2                 = 1.65 V
|Vgs_pair|        (gm/Id 6, L 0.56)               = 1.083 V
net_tail sits at  1.65 + 1.083                    = 2.733 V
headroom          3.3 − 2.733                     = 0.567 V
tail Vdsat        (gm/Id 10, L 1.12)              = 0.154 V   → fits, 0.41 V to spare
```

No repair, no warning, nothing changed, `bias_ok = True`. The tail is not
stacked, so pass 2 is skipped. At 3.3 V this check is easy. At a low supply
such as ptm45's 1.0 V, the same sum leaves little or no room, and the repair
has to move the pair toward weak inversion (the tests below).

**Older page.** [`dc-bias-feasibility_explained.html`](../../_extra/walkthrough/gmid/dc-bias-feasibility_explained.html)
explains the physics and walks a full repair by hand. It describes the repair
as the body of `_apply_headroom`. That body now lives in
`_repair_tail_headroom`, shared with the CMFB tail. Also see "Phase 4b" in
[`gmid_sizing_flow.rst`](../../theory/gmid_sizing_flow.rst) for the
three-outcome table (fits / repaired / warned).

**Test to read.** [`test_headroom.py`](../../../tests/test_headroom.py) (ptm45,
PMOS pair over a PMOS tail at 10 µA / 20 µA)
- [`:53`](../../../tests/test_headroom.py#L53)
  `test_tail_gm_id_for_headroom_monotone`: 300 mV of room has a fitting gm/Id;
  1 mV has none.
- [`:59`](../../../tests/test_headroom.py#L59) `test_headroom_violation_warns`:
  at Vdd = 0.8 V nothing fits; a "headroom" warning, pair untouched.
- [`:68`](../../../tests/test_headroom.py#L68)
  `test_headroom_repairs_pair_toward_weak_inversion`: at Vdd = 1.0 V the
  repair succeeds by giving the pair a smaller |Vgs| and the tail a smaller
  Vdsat; no warning.
- [`:79`](../../../tests/test_headroom.py#L79) `test_headroom_ok_at_high_supply`:
  at 3.0 V the tail is left exactly as it was.

**Check yourself.**
1. Why does the repair re-size the tail's whole gate group instead of just
   the tail device?
2. A clean result (no warning) from `check_dc_operating_point`: does it mean
   the sizing was not changed?
3. How much more |Vgs| could `circuit_0010`'s pair have before its tail
   would no longer fit?

<details><summary>Answers</summary>

1. The tail is a mirror output. Re-sizing only it would break the width
   ratio to its diode and change its current. Moving the whole group to one
   gm/Id keeps W ∝ I.
2. No. "No warning" covers both "already fit" and "repaired". Compare the
   returned sizing with the input to know.
3. About 0.41 V. The tail needs 0.154 V and has 0.567 V, so net_tail could
   rise to about 3.146 V, i.e. |Vgs_pair| up to about 1.50 V.
</details>

---

---

## 4.7 `gmid/stage_interface.py` (390 lines)

**Job in one sentence.** Check that the DC level the second stage pins on
the first-stage output fits what the first-stage load needs. If it does not,
try to fix it by moving devices to another gm/Id, and reject the circuit
honestly if nothing works.

**Design knowledge it encodes.**

- [Stage interface](00_background.md#stage-interface): the downstream pipe
  sets the pressure at the junction, and the upstream regulator works only in
  a band.
- [Vdsat and headroom](00_background.md#vdsat): every device in a
  [cascode](00_background.md#cascode) stack needs its Vdsat. The stack therefore
  demands that the node sit above (NMOS) or below (PMOS) a level.
- [Inversion level](00_background.md#inversion): moving a device toward weak
  inversion (higher gm/Id) lowers its `Vgs` and Vdsat. That is the repair knob.
- [CMFB](00_background.md#cmfb) for the FD branch: a CMFB that senses a node
  pins its level at mid-rail.

**Two shapes of the same check.**

| | Single-ended (#124) | Fully differential (#161) |
|---|---|---|
| Constraint | a **window**: `lo ≤ pin ≤ hi` with 50 mV slack | an **equality**: `pin ≈ CMFB target` within 50 mV |
| Applies to | cascode loads only ([L308](../../../circuitgenome/sizer/gmid/stage_interface.py#L308)) | a CMFB that senses the interface nets ([L191](../../../circuitgenome/sizer/gmid/stage_interface.py#L191)) |
| Repair knobs | load mirror groups and the second-stage device | the second-stage *pair*, both sides at one gm/Id |

**Walkthrough (single-ended, the main path).**

1. [L306–L315](../../../circuitgenome/sizer/gmid/stage_interface.py#L306):
   FD goes to `_check_fd_interface`. A non-cascode SE load returns at once
   ("enough headroom for the window never to bind"). It also needs the
   first-stage output net, the load, and the pinning device.
2. **The pin.** `_pin_level`
   ([L141](../../../circuitgenome/sizer/gmid/stage_interface.py#L141)): a
   common-source second stage holds its gate one `|Vgs|` from its source rail.
   A follower holds it at mid-supply ± `|Vgs|`.
3. **The window.** `_stack_bound`
   ([L84](../../../circuitgenome/sizer/gmid/stage_interface.py#L84)) walks from
   the node down (NMOS) or up (PMOS) through the load stack and adds each
   device's Vdsat until it reaches a known level. That anchor is a rail, a
   cascode whose gate is set by diode drops (`_diode_level`,
   [L66](../../../circuitgenome/sizer/gmid/stage_interface.py#L66)), or the
   input pair (source at Vcm ∓ `|Vgs|`). The NMOS walk gives `lo`, the PMOS
   walk gives `hi`.
4. [L329–L332](../../../circuitgenome/sizer/gmid/stage_interface.py#L329): if
   the slack is at least `_MARGIN_V` (50 mV, [L63](../../../circuitgenome/sizer/gmid/stage_interface.py#L63)),
   it is done.
5. **Repair scan**
   ([L335–L370](../../../circuitgenome/sizer/gmid/stage_interface.py#L335)):
   a double loop over gm/Id candidates.
   - The outer loop tries the violated side's **mirror group** (the load devices
     sharing a gate with a diode) at each table gm/Id. Mirror devices have no
     `gm` requirement, so this knob is always spec-safe. Re-sizing the whole
     group at one gm/Id keeps the [mirror ratios](00_background.md#current-mirror).
   - The inner loop tries the **second-stage device** at each gm/Id that still
     meets its `gm` requirement.
   - The first candidate with ≥ 50 mV slack wins
     ([L368](../../../circuitgenome/sizer/gmid/stage_interface.py#L368)).
6. **Verdict.** If the best candidate clears the raw bounds (slack ≥ 0) but not
   the margin, it is kept and SPICE decides
   ([L372](../../../circuitgenome/sizer/gmid/stage_interface.py#L372)).
   Otherwise the original sizing comes back with a `"stage interface cannot
   bias"` warning and `feasible=False`
   ([L377–L390](../../../circuitgenome/sizer/gmid/stage_interface.py#L377)).

**FD branch.** `_check_fd_interface`
([L218](../../../circuitgenome/sizer/gmid/stage_interface.py#L218)) asks
whether the CMFB touches the interface nets. If yes, the target is mid-rail.
It scans the second-stage pair's gm/Id, keeping both sides equal. Within
50 mV it is fine, within 100 mV it keeps the closest, and beyond that it
rejects. Note that since #165 the shipped FD templates' CMFBs sense the
*outputs*, so this branch returns early for them (see
[test_sizer.py:1481](../../../tests/test_sizer.py#L1481)). The machinery stays
for a future interface-sensing CMFB.

**Running example.** `circuit_0010` has a current-mirror load, not a cascode:
`blocks.has_cascode_load()` is False, so the check returns `(sizing, [], True)`
at L309 on every call. The telescopic contrast below is the case the file
exists for. It is traced in the debugger pass at the end of this stop.

| Call | Pin (`mn1_second_stage` Vgs) | NMOS floor `lo` | Repair | Slack after |
|---|---|---|---|---|
| 1st `_size` | 0.887 V | 1.008 V (pin 121 mV *below* it) | `mn1–mn4_load` to gm/Id 15.5 → floor 0.836 V | 51 mV |
| 2nd `_size` (after the two-stage refine raised `gm2`) | 0.833 V | 1.008 V | same group to gm/Id 18.0 | 58 mV |

**Test to read.**

- [test_sizer.py:1354](../../../tests/test_sizer.py#L1354)
  `test_stage_interface_repairs_telescopic_cs` (still present). It asserts the
  result is feasible, has no stage-interface warning, and
  `Vgs(mn3 diode) + Vdsat(mn2 cascode) + 49 mV ≤ pin`.
- [test_sizer.py:1393](../../../tests/test_sizer.py#L1393)
  `test_stage_interface_rejects_unclosable_gap`: at 1.5 V no assignment
  closes the gap, giving `bias_feasible=False` and the warning.
- [test_sizer.py:1406](../../../tests/test_sizer.py#L1406)
  `…_leaves_fitting_candidates_alone`; FD exemptions at
  [L1481](../../../tests/test_sizer.py#L1481),
  [L1499](../../../tests/test_sizer.py#L1499),
  [L1512](../../../tests/test_sizer.py#L1512).

**Check yourself.**

1. Why is the mirror group a "spec-safe" knob, but the second-stage device not?
2. Why are non-cascode single-ended loads skipped?
3. In FD, why is the constraint an equality and not a window?

<details><summary>Answers</summary>

1. Mirror devices have no `gm` requirement, so any gm/Id is allowed as long as
   the whole group moves together. The second-stage device must keep
   `gm/Id · Id ≥ gm_req`, which limits how far it can move.
2. One device per side leaves plenty of headroom, so the window never binds
   (module docstring, "Scope, single-ended").
3. A CMFB sensing the node holds it at mid-rail. The second stage cannot pull
   it anywhere, so its `|Vgs|` must *match* that level or its output rails.

</details>

## 4.8 `gmid/rnmc_refine.py` (156 lines)

**Job in one sentence.** After the first geometry pass, re-run the
three-stage compensation design on the *sized* circuit's real numbers and, if
it now wants a different `gm2`, `Cc2` or `gm3`, hand back a new plan to
re-size against.

**Design knowledge it encodes.**

- [Reversed nested Miller (RNMC)](00_background.md#nested-miller): three stages,
  two caps, a complex pole pair that can ring. The damping knobs, in order, are
  raise `gm2`, raise `Cc2`, cap `gm3` (this costs gain).
- [Parasitic caps](00_background.md#parasitic-caps): the gate capacitance of
  the next device loads each `Cc` node. Before geometry that capacitance is a
  guess. After geometry it is known.
- [gm/Id](00_background.md#gmid): the table has a floor and a ceiling. The
  sized device can deliver a lot more `gm` than Phase 3 asked for (see the
  running example in 4.9: `circuit_0010`'s input pair delivers 60 µS against a
  15.7 µS request). The loop's real crossing then moves.

**Why estimates drift (plain words).** Phase 3 plans on a sketch, the
blueprint before any wall is built: "the input pair will have the `gm` the GBW
spec asks for; the third stage's gate will weigh about this much." Phase 4 then
builds the real walls. The tail repair can push the input pair into weak
inversion, so it ends up with *far* more `gm` than requested. The module
docstring cites a ptm45 FD fixture at about 27x the requested `gm1`, crossing
0 dB at 54 MHz instead of 2 MHz. The table's gm/Id floor also puts a minimum
under every `gm`. An inner pole pair damped for the sketch can then sit in the
right half plane, which means it oscillates. So the code measures the real
circuit and designs the compensation again. Think of a tailor: measure, cut,
try it on, adjust, try it on again.

**Walkthrough.**

1. [rnmc_refine.py:81](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L81)
   is the gate. Anything that is not RNMC, or has no `Cc`/`Cc2`, comes back
   `(plan, False)` at once. `circuit_0010` leaves here.
2. [rnmc_refine.py:84](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L84)
   builds the [stage chain](03_physics.md) from the sizing, so every `gm`,
   `rout` and node cap is now a *sized* value. A chain without three live
   stages also leaves ([L87](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L87)).
3. [L92–L99](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L92) finds the
   two devices whose gates load the `Cc` nodes: the second stage's signal
   device (`ss`) and the third stage's *input* device (`ts`, the one whose gate
   is on a second-stage output).
4. [L102–L110](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L102) sets the
   search box:
   - `gm2_max` is the table's weak-inversion ceiling. When `hold_gm2` is set it
     is the `gm2` the circuit has *now*, so `gm2` may not rise.
   - `gm3_min` is the weakest-inversion `gm`. If an earlier pass already asked
     for less than the device delivers, it is the delivered `gm3` (some other
     floor, such as the swing floor, is holding it there).
5. [L119](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L119) calls
   `rnmc.design_rnmc` (Stop 3) with the real values. Two inputs are functions,
   not numbers: `c1_f` and `c2_f` are built by `_node_cap_at`
   ([L36](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L36)). If the
   designer tries a new `gm2`, the device gets a new width, and with it a new
   gate cap. `_node_cap_at` swaps the old gate cap for the new one. Behind an
   output buffer the second-stage width is *not* clamped to one PDK device
   ([L117](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L117), #266).
6. [L130–L132](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L130) gives the
   verdict. The plan changed if `gm2` rose, `gm3` fell, or `Cc2` grew.
7. [L135–L139](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L135) refreshes
   the `"RNMC compensation:"` advisories. A gm3-cap advisory from an earlier
   pass survives until a later pass caps `gm3` again.
8. [L145–L154](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L145) writes
   the new `gm` requirements into a *copy* of `gm_req_map`. A raised
   second-stage device joins `wide_refs` behind a buffer, so geometry may build
   it from parallel fingers. Only the third stage's input device gets the
   `gm3` cap, so the mirror next to it keeps its low Vdsat (swing).
   [L155](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L155) returns the
   new plan with the new `cc2_pf`.

**The loop that calls it: `hold_gm2` in `gmid_sizer.py`.**

```python
hold_gm2 = False
for _ in range(_RNMC_PASSES + 1):                      # 3 tries
    refined, changed = refine_rnmc_plan(..., hold_gm2=hold_gm2)
    if not changed:
        plan = refined; break                          # keep the fresh advisories
    resized = _size(refined)                           # geometry + DC + interface
    if bias_feasible and not resized[2] and not hold_gm2:
        hold_gm2 = True; continue                      # undo: retry with gm2 frozen
    plan, (sizing, size_warnings, bias_feasible) = refined, resized
```
([gmid_sizer.py:136–L147](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L136))

In plain words:

- Each pass re-plans, then re-sizes. A second pass "settles what the first
  moved": a new `gm2` means a new width, which means a new gate cap, which the
  next pass sees.
- The catch is the [stage interface](00_background.md#stage-interface). The
  second stage's input `Vgs` *is* the first-stage output level. A big `gm2`
  raise changes that `Vgs` and can push a sound bias out of its window.
- So if the circuit **was** sound (`bias_feasible`) and the re-size is
  **not** (`resized[2]` is False), the re-size is thrown away (`continue`), and
  the next pass runs with `hold_gm2=True`. Now `gm2` is frozen and only `Cc2`
  and `gm3` may move.
- The `continue` uses up one of the three tries, so with a hold there are at
  most two accepted passes.
- After the hold, a re-size is accepted even if it is infeasible. The hold is
  tried only once. A circuit that was already unsound accepts every re-size.
  That can even turn `bias_feasible` back to `True`, because it is a new
  sizing.

**Running example.** `circuit_0010` is a two-stage circuit
(`view.compensation_scheme is None`), so `refine_rnmc_plan` returns
`(plan, False)` at L81 and the loop breaks on its first try. For a case where
it does fire, use the test fixture `three_stage_rnmc_se_fbr`
([test_sizer.py:803](../../../tests/test_sizer.py#L803)) on ptm45 with
`examples/three_stage_se_specs/spec_ptm45.yaml`:

| Pass | `changed` | What moved |
|---|---|---|
| 1 | True | `mp1_third_stage` gm requirement 1200 µS → 300 µS (gm3 capped); Cc2 stays 0.125 pF |
| 2 | False | nothing, loop ends |

The result is `bias_feasible=True`, gain 102 dB, GBW 36 MHz, PM 60.1°. It
carries the advisories `"RNMC compensation: third-stage gm capped at 0.3 mS —
the second stage (gm 0.538 mS) cannot …"` and `"RNMC compensation: no
gm2/Cc2/gm3 choice reaches ζ ≥ 0.7 and PM ≥ 60° …"`. (Measured 2026-10-31 by
wrapping `gmid_sizer.refine_rnmc_plan` with a print.)

**Test to read.**

- [test_sizer.py:951](../../../tests/test_sizer.py#L951)
  `test_rnmc_requirements_damp_the_inner_pole_pair`. This is the plan-time
  half: without the RNMC scheme the pole pair is in the right half plane
  (`gm3 > gm2·(1+CL/Cc1)`). With it, `gm2` is raised and ζ > 0.5.
- [test_sizer.py:889](../../../tests/test_sizer.py#L889)
  `test_three_stage_se_specs_met` and
  [test_sizer.py:1649](../../../tests/test_sizer.py#L1649)
  `test_ptm45_example_three_stage_se_met` are end-to-end. No test calls
  `refine_rnmc_plan` directly, and none forces the `hold_gm2` branch.

**Check yourself.**

1. Why can the input pair end up with far more `gm` than the plan asked for?
2. What does `hold_gm2=True` freeze, and which knobs are left?
3. A circuit was infeasible before the RNMC loop. Can the loop make it
   feasible?

<details><summary>Answers</summary>

1. The `gm` requirement is a *minimum*. The tail headroom repair and the
   table's gm/Id floor can both leave the pair at a gm/Id that gives much more.
2. `gm2` is capped at its sized value
   ([rnmc_refine.py:103](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L103)).
   Only `Cc2` and the `gm3` cap remain.
3. Yes. With `bias_feasible` False, the guard at
   [gmid_sizer.py:144](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L144)
   never fires, so every re-size is accepted together with its own flag, which
   may be `True`.

</details>

## 4.9 `gmid/two_stage_refine.py` (83 lines)

New since the #250 checklist (#274, PR #279).

**Job in one sentence.** For a plain two-stage Miller amplifier, find the
`gm1` and `gm2` that the *full* loop model needs to meet GBW and phase margin
on the sized circuit, and hand back a plan that asks for them.

**Design knowledge it encodes.**

- [GBW](00_background.md#gbw) and [phase margin](00_background.md#phase-margin)
  closed forms: `GBW = gm1/(2π·Cc)` and `PM ≈ 90° − atan(gm1·CL/(gm2·Cc))`.
- [The RHP zero](00_background.md#rhp-zero) of a bare Miller cap, and the
  first-stage node's own capacitance. The closed forms leave out both, and also
  the output pole's pull on the 0 dB crossing.
- [Nodal analysis](00_background.md#nodal-analysis): `equations.two_stage_loop`
  (Stop 3) solves the small circuit numerically instead of using the closed
  form.

**Closed forms vs full model (plain words).** Phase 3 sizes `gm1` and `gm2`
from the two one-line formulas. They are like the rule "one hour of driving per
100 km". It is good for planning, but it ignores traffic lights. Phase 5 then
grades the design with the full model, which sees the traffic lights: the RHP
zero, the extra node cap, and the output pole dragging the crossing down. A
design sized exactly to the rule can read 2–5° short of the PM it was sized
for (issue #274). The fix is not to change the grader. The fix is to plan
against the grader: after geometry, ask the full model what `gm1` and `gm2` it
needs, then re-size.

**Walkthrough.**

1. [two_stage_refine.py:44](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L44)
   is the gate. It needs a Miller cap and `compensation_scheme is None` (no
   NMC/RNMC). It also needs a second-stage signal device in the sizing and a
   chain of exactly two stages with node caps
   ([L53](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L53)).
2. [L59](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L59) `c1_at(gm)`
   is the first-stage output's capacitance if the second stage were re-sized
   for `gm`. A higher `gm2` means a wider device and a bigger gate, which loads
   the first-stage node. It uses `cgg` at the device's inversion level (#274).
3. [L66](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L66) sets the
   search limits. `gm1` may grow up to what the widest single PDK device (or
   the ceiling) delivers. `gm2` may grow up to the weak-inversion ceiling.
4. [L69](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L69) calls
   [`metrics.two_stage_replan`](../../../circuitgenome/sizer/physics/metrics.py#L97).
   That function:
   - fixes GBW first: if the full-model GBW is short, it finds the *least*
     `gm1` that reaches `gbw_min · 1.005`;
   - then fixes PM at that `gm1`: it finds the least `gm2` that reaches
     `pm_min + 0.5°`;
   - searches with `least(lo, hi, ok)`
     ([metrics.py:131](../../../circuitgenome/sizer/physics/metrics.py#L131)),
     a 40-step geometric bisection. Picture halving a range on a log scale.
     It first checks the top of the range
     ([L133](../../../circuitgenome/sizer/physics/metrics.py#L133)). If even
     the maximum fails, it returns `None` and gives up;
   - returns `None` for any `gm` that needs no raise or cannot be reached.
5. [L71–L83](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L71) raises
   the input-pair and second-stage requirements (never lowers them). A raised
   second stage joins `wide_refs`, so it can grow past one PDK device as
   fingers.

**The loop that calls it** ([gmid_sizer.py:151–L158](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L151)).
There are two passes. A pass stops when nothing changed. If the circuit was
sound and the re-size breaks the bias, the loop **stops and keeps the old
sizing** (`break`). Compare RNMC, which retries with `gm2` frozen. The reason
is the same: the second stage's gate sets the first-stage output level, so a
new `gm2` moves the [stage interface](00_background.md#stage-interface).

**Running example: does it change anything for `circuit_0010`?** No. The
probe reproduced `_size_planned` up to the first `_size(plan)`, then called
`refine_two_stage_plan` (gf180, `spec_gf180.yaml`, 2026-10-31):

| Quantity | Value |
|---|---|
| Planned gm requirement, `m1_input_pair` | 15.71 µS |
| Planned gm requirement, `mn1_second_stage` | 415.69 µS |
| Sized `gm1` / `gm2` (chain) | 60.0 µS / 575.0 µS |
| Full-model GBW / PM on that chain | 7.06 MHz / 63.19° |
| `refine_two_stage_plan` | `changed=False`, returns the same `plan` object |

`gm1` sits at 60 µS because 15.71 µS / 10 µA asks for gm/Id ≈ 1.6, below the
gf180 table's floor of 6. The pair is clamped to gm/Id = 6, which gives
6 × 10 µA = 60 µS. GBW then lands at 3.5x the spec, and PM is still 3° clear,
so the re-plan has nothing to do.

To watch it work, tighten the spec (same circuit, one field changed):

| Spec change | What the refine does | Final result |
|---|---|---|
| `gbw_min_hz: 8e6` | raises `gm1` request to 69.5 µS and `gm2` request to 593.5 µS; `m1_input_pair` 3.07 → 4.9 µm, `mn1_second_stage` 17.86 → 19.46 µm | GBW 8.07 MHz, PM 60.55° |
| `phase_margin_min_deg: 70` | **nothing** (`changed=False`) | PM 65.97°, short of 70°, and **no warning** |

The second row is worth understanding. With the extra gate cap counted, PM as a
function of `gm2` is not monotone. It peaks at about 71.9° near
`gm2 ≈ 1000 µS`, then falls back to 67.1° at the 1200 µS ceiling, because the
huge device's gate loads the first-stage node. `least` assumes "more `gm2` is
always better". It tests only the ceiling, sees 67.1° < 70.5°, and gives up,
although a `gm2` near 1000 µS would have met the spec. See the simplification
notes.

A contrast where the default spec does trigger it: in
`test_stage_interface_repairs_telescopic_cs` (telescopic load, gf180), the
first pass raises the `mn1_second_stage` request from 415.7 µS to 515.6 µS.
The final PM is 60.54°, which is the 60° spec plus the 0.5° aim.

**Test to read.** The unit tests are in Stop 3's `test_stage_chain.py`:

- [test_stage_chain.py:181](../../../tests/test_stage_chain.py#L181)
  `test_two_stage_replan_raises_gm2_for_the_phase_margin`: a chain reads below
  60°. The re-plan returns `gm1=None` and a larger `gm2`, which reads 60.5°.
- [test_stage_chain.py:194](../../../tests/test_stage_chain.py#L194)
  `…_raises_gm1_for_gbw_first`: GBW is fixed through `gm1` first.
- [test_stage_chain.py:207](../../../tests/test_stage_chain.py#L207)
  `…_leaves_an_unreachable_spec`: PM 89° is unreachable, so it returns
  `(None, None)`.

No test calls `refine_two_stage_plan` itself.

**Check yourself.**

1. Which three effects do the closed forms leave out?
2. Why is `c1_at` a function and not a number?
3. When a two-stage re-size breaks a sound bias, what does the loop keep?

<details><summary>Answers</summary>

1. The output pole's pull on the 0 dB crossing, the bare Miller cap's RHP
   zero, and the first-stage node's capacitance.
2. Because the node cap depends on the `gm2` being tried: a bigger `gm2` means a
   wider second-stage device and more gate cap on the first-stage output.
3. The previous plan and sizing (`break` at
   [gmid_sizer.py:157](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L157)).
   The PM or GBW shortfall then stays.

</details>

## 4.10 `gmid/resistors.py` (308 lines)

**Job in one sentence.** Give every resistor that is *not* a first-stage load
a real value (degeneration, resistor tail, bias legs, CMFB sense,
compensation), and report how those resistors change the metrics.

**Design knowledge it encodes.**

- [Source degeneration](00_background.md#degeneration):
  `R = factor / gm1`, and the input-pair `gm` is derated by `1/(1+factor)`.
- [Resistor tail](00_background.md#diff-pair): `R = |rail − V_tail| / ibias`.
  Its conductance `1/R` is a leaky tail, which costs
  [CMRR](00_background.md#cmrr).
- [Bias generator](00_background.md#bias-generator) legs: `R = V_rail / ibias`,
  where `V_rail` is what the rail's consumers need, one `|Vgs|` from a supply
  or above a cascode stack's saturation floor (#100).
- [Nulling resistor](00_background.md#rhp-zero):
  `R = (Cc + C)/(gm_out · Cc)` places the compensation zero on the output pole
  (#108). For RNMC, `C` is the follower's gate or nothing (#262, #269).

**Walkthrough.** `size_resistors`
([L60](../../../circuitgenome/sizer/gmid/resistors.py#L60)) is five
independent blocks, one per resistor role:

1. Degeneration ([L90–L101](../../../circuitgenome/sizer/gmid/resistors.py#L90)):
   reads the sized pair's `gm`, sets `R = factor/gm1`, and sets `gm1_factor`.
2. Resistor tail ([L104–L118](../../../circuitgenome/sizer/gmid/resistors.py#L104)):
   the tail node sits one pair `|Vgs|` from Vcm, and `R` drops the rest of the
   way to the rail at `ibias`. It sets `gd_tail_override = 1/R`.
3. Bias legs ([L126–L138](../../../circuitgenome/sizer/gmid/resistors.py#L126)):
   `_bias_rail_target_v`
   ([L217](../../../circuitgenome/sizer/gmid/resistors.py#L217)) averages
   what each consumer gate needs. For a cascode consumer it walks the stack
   with `_stack_node_v`
   ([L252](../../../circuitgenome/sizer/gmid/resistors.py#L252)), adding
   Vdsat plus a 0.1 V margin per device
   ([`_CASCODE_SAT_MARGIN_V`, L34](../../../circuitgenome/sizer/gmid/resistors.py#L34)).
   If no level can be derived, it falls back to a representative bias `|Vgs|`.
4. CMFB sense ([L141–L147](../../../circuitgenome/sizer/gmid/resistors.py#L141)):
   a large fixed `intent.cmfb_sense_r`. It sets `gd_out_extra = 1/R`.
5. Compensation ([L163–L175](../../../circuitgenome/sizer/gmid/resistors.py#L163)):
   `eq.compensation_resistor_ohm` against the last gain stage's real `gm`.

**The hidden link to `evaluate.py`.** `MetricModifiers`
([L37](../../../circuitgenome/sizer/gmid/resistors.py#L37)) is a small
envelope with three numbers. Phase 4 writes it here, and Phase 5 opens it in
[evaluate.py:117](../../../circuitgenome/sizer/gmid/evaluate.py#L117):
`chain = modifiers.apply(chain)`. `apply`
([L52](../../../circuitgenome/sizer/gmid/resistors.py#L52)) calls three
`StageChain` methods (Stop 3): `with_first_stage_gm`, `with_tail_conductance`,
`with_output_loading`. The link is easy to miss for two reasons:

- It travels through `gmid_sizer.py` as a plain return value
  ([L159](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L159) →
  [L173](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L173)). No type in
  `evaluate.py` says "this came from resistors".
- Degeneration reaches the metrics through **two** channels. `gm1_factor`
  derates `gm` through the modifier. The resistor *value* travels separately
  as `resistor_ohms=extra_r` into `build_stage_chain`, which uses it for the
  `ro·(1+gm·R)` output-resistance boost (#226). One envelope cannot carry the
  second effect, because it lands inside a parallel combination
  ([evaluate.py:104–L108](../../../circuitgenome/sizer/gmid/evaluate.py#L104)).

Also note the ordering. The two refine loops (4.8, 4.9) run *before* this
file, so their re-plans see neither the modifiers nor the resistor values.

**Running example.** `circuit_0010` has no resistors at all, so
`size_resistors` returns `({}, MetricModifiers(1.0, None, 0.0))`.
`circuit_0001`'s `r1_load`/`r2_load` (76.04 kΩ) are *load* resistors. Phase 2
(`currents.load_resistors`) sized them, not this file, so here too
`extra_r == {}`.

**Test to read.** [test_resistors.py](../../../tests/test_resistors.py):

- [L59](../../../tests/test_resistors.py#L59)
  `test_size_resistors_degeneration_factor`: `R = 0.5/1 mS = 500 Ω`,
  `gm1_factor = 1/1.5`.
- [L93](../../../tests/test_resistors.py#L93)–[L139](../../../tests/test_resistors.py#L139):
  bias-leg rail levels (NMOS cascode, PMOS anchored at the input pair,
  conflicting consumers take the mean, fallback).
- [L280](../../../tests/test_resistors.py#L280)
  `test_comp_resistor_zero_on_output_pole`: `(2p+2p)/(1 mS·2p) = 2 kΩ`;
  [L335](../../../tests/test_resistors.py#L335) the unbuffered RNMC case,
  `R = 1/gm`.

**Check yourself.**

1. Which `MetricModifiers` field does a resistor tail set, and which metric
   does it hurt?
2. Why does degeneration need *both* the modifier and `resistor_ohms`?

<details><summary>Answers</summary>

1. `gd_tail_override = 1/R`, which is the tail's finite output conductance. It
   lowers CMRR.
2. The modifier derates `gm1`. The value is needed for the `ro·(1+gm·R)` rout
   boost inside the first stage's parallel combination, which a scalar factor
   cannot express.

</details>

---

## 4.11 `gmid/bias_levels.py` (236 lines)

**Job in one sentence.** Re-size the few bias-generator devices whose job is
to set a *voltage level* rather than copy a current: cascode-leg level diodes
and the wide-swing `ncasc` diode.

**Design knowledge it encodes.**

- [Bias generator](00_background.md#bias-generator): most of its devices are
  mirrors (ratios, sized by geometry). A few exist to make a voltage.
- [Cascode](00_background.md#cascode) gate rail: it must sit at the cascode's
  `Vgs` **plus** the saturation floor of the stack below it.
- [Vth tracking](00_background.md#vth-body): making the `Vgs` part with a
  diode of the same kind means the rail follows Vth over process and
  temperature. A resistor alone would not.

**Analogy.** A shelf held up by a bracket and a shim. The diode is the
bracket: it is cut to the same height as the cascode's own `Vgs`, so if the
floor (Vth) moves, both move together. The small resistor is the shim that adds
the last few tens of millivolts (the Vdsat floor).

**Walkthrough.**

1. `tune_bias_levels`
   ([L210](../../../circuitgenome/sizer/gmid/bias_levels.py#L210)) works on a
   copy and lists every non-bias-gen MOSFET as a possible consumer.
2. `_tune_cascode_legs`
   ([L82](../../../circuitgenome/sizer/gmid/bias_levels.py#L82)) finds legs
   by shape: a bias_gen resistor from a supply to a `mid` net, with a diode
   whose source is `mid`. The diode's drain is the rail.
   - The target level comes from `resistors._bias_rail_target_v` (the same
     stack walk as 4.10).
   - The diode is re-sized so its `|Vgs|` is closest to the consumers' mean
     `|Vgs|` (`_size_diode_for_vgs`,
     [L66](../../../circuitgenome/sizer/gmid/bias_levels.py#L66), a scan
     over the table's gm/Id points).
   - The floor resistor gets `R = (target − Vgs_diode) / I_leg`
     ([L126–L129](../../../circuitgenome/sizer/gmid/bias_levels.py#L126)).
3. `_tune_pref_cascode`
   ([L132](../../../circuitgenome/sizer/gmid/bias_levels.py#L132)) finds the
   chain master diode → pref mirror → cascode on its drain → PMOS pref diode,
   plus the `ncasc` diode on the cascode gate. The cascode goes to the
   weak-inversion end (lowest `Vgs`). The `ncasc` diode is sized narrow (high
   `Vgs`) so the mirror's `Vds` lands near the master's `Vgs`, capped by the
   pref node's headroom. That fixes the about 4 % extra-mirror-hop error of
   #103.
4. It returns `(sizing, level_r)`. `gmid_sizer` merges `level_r` *over*
   `size_resistors`' values
   ([gmid_sizer.py:165](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L165)).

**Running example.** `circuit_0010`'s bias generator (`mnref`, `mn5`, `mp5`,
`mn7`) has no resistors, so there are no cascode legs. `_tune_pref_cascode`
finds the master (`mnref`) and its mirrors (`mn5`, `mn7`), but no cascode sits
on their drains. Both helpers do nothing: zero devices changed and
`level_r == {}`.

**Test to read.** [test_bias_levels.py](../../../tests/test_bias_levels.py):

- [L37](../../../tests/test_bias_levels.py#L37)
  `test_cascode_gnd_leg_diode_tracks_consumer_vgs`: diode `Vgs` ≈ consumer
  `Vgs`, and `R = floor/15 µA`.
- [L87](../../../tests/test_bias_levels.py#L87)
  `test_pref_cascode_pins_mirror_below_master_vds`: the mirror stays saturated
  and never overshoots the master's `Vgs`.
- [L106](../../../tests/test_bias_levels.py#L106) (clamps out at 1 V) and
  [L117](../../../tests/test_bias_levels.py#L117)
  (`test_no_constructed_bias_shapes_is_a_noop`, the `circuit_0010`-like case).

**Check yourself.**

1. Why make the cascode rail with a diode plus a small resistor rather than one
   big resistor?
2. How does the code find a cascode leg without a name?

<details><summary>Answers</summary>

1. The diode's `Vgs` tracks Vth like the consumer's does, so the rail follows
   process and temperature. Only the small Vdsat floor rests on the
   non-tracking resistor.
2. By shape: a bias_gen resistor between a supply and a net that is the source
   of a diode-connected bias_gen device
   ([L90–L103](../../../circuitgenome/sizer/gmid/bias_levels.py#L90)).

</details>

---

## 4.12 `gmid/evaluate.py` (122 lines), Phase 5

**Job in one sentence.** Build the final stage chain from the sizing, fold in
the resistor effects, blank out the gain metrics if the DC bias makes them
meaningless, and compute metrics and margins.

**Design knowledge it encodes.**

- [Analytical vs SPICE](00_background.md#analytical-vs-spice): these numbers
  are a fast, deterministic estimate. On a real technology the CLI shows
  ngspice numbers instead.
- [Bias point vs small signal](00_background.md#small-signal): a small-signal
  gain is only real if the bias point exists. A railed output still yields a
  `gm·rout` number, but it is a lie.

**Walkthrough.** `evaluate_circuit`
([L89](../../../circuitgenome/sizer/gmid/evaluate.py#L89)):

1. [L112](../../../circuitgenome/sizer/gmid/evaluate.py#L112) builds the chain
   with `resistor_ohms` and `bias_aware=True`. The first stage's devices are
   read at their own `Vds`/`Vsb` (#273). The refine loops build theirs
   *without* `bias_aware`, which is deliberate (see `build_stage_chain`'s
   docstring), so they judge a slightly different chain.
2. [L117](../../../circuitgenome/sizer/gmid/evaluate.py#L117) applies the
   `MetricModifiers` (4.10).
3. [L118](../../../circuitgenome/sizer/gmid/evaluate.py#L118)
   `_resistor_load_bias` ([L35](../../../circuitgenome/sizer/gmid/evaluate.py#L35))
   handles a single-ended, rail-referenced resistor first-stage load driving a
   common-source stage. The load holds the node `I·R` from its rail
   (`I = ibias/2`). The driven gate needs `|Vgs|`. The bias exists only while
   `|Vgs| < 2·I·R`, meaning the branch current fits inside the whole tail.
   Past that the output rails (#148, #286). It always adds a corner-fragility
   note, because a fixed resistor cannot track Vth.
4. [L119–L120](../../../circuitgenome/sizer/gmid/evaluate.py#L119): if the
   bias is invalid, `chain.withheld()` sets `gain_measurable=False`.
   `evaluate_metrics` then omits gain, GBW, PM, CMRR and PSRR instead of
   reporting them optimistically.
5. [L121](../../../circuitgenome/sizer/gmid/evaluate.py#L121)
   `evaluate_metrics` (Stop 3) returns metrics and signed margins (positive
   means met).

Note what step 4 does **not** do: it does not touch `bias_feasible`. A railed
resistor-load design keeps `bias_feasible=True` and loses its gain metrics
instead. See the exit check.

**Running example.**

| | `circuit_0010` | `circuit_0001` |
|---|---|---|
| `gd_load_r` | 0, so `_resistor_load_bias` returns `(False, [])` | 1/76.04 kΩ |
| Bias test | n/a | `I·R = 10 µA × 76.04 kΩ = 0.760 V`; `|Vgs(mn1)| = 0.690 V < 1.52 V`, valid |
| Notes | none | the corner-fragility advisory (seen in its `warnings`) |
| GBW / PM, evaluated (`bias_aware`) | 7.057 MHz / 63.22° | 3.515 MHz / 81.40° |
| GBW / PM, refine's chain (not `bias_aware`) | 7.062 MHz / 63.19° | 3.515 MHz / 81.41° |
| Margins | gain +21.5 dB, GBW +5.06 MHz, PM +3.2° | gain −7.9 dB (52.1 vs 60 dB spec) |

**Test to read.**

- [test_sizer.py:1543](../../../tests/test_sizer.py#L1543)
  `test_resistor_load_bias_gate_fires_when_tail_cannot_feed_load` calls
  `_resistor_load_bias` directly on stubs. It checks four cases: the gate fires
  when `|Vgs|/R` needs the whole tail; it stands down with only the fragility
  note when the levels agree; it stands down for the #286 weak-inversion false
  positive; and it stays quiet when the levels are far apart but the current
  still fits.
- [test_sizer.py:1526](../../../tests/test_sizer.py#L1526)
  `test_resistor_load_lut_seed_biases_interstage` is the end-to-end case, a
  `circuit_0001`-like circuit on gf180. Gain is reported, and only the
  `corner-fragile` note remains.
- The chain side (`withheld`, modifiers) is in Stop 3's
  [test_stage_chain.py](../../../tests/test_stage_chain.py).

**Check yourself.**

1. What does `withheld()` change, and what does it leave alone?
2. Why is `circuit_0001` valid even though its load is a fixed resistor?

<details><summary>Answers</summary>

1. It sets `gain_measurable=False`, so the gain-derived metrics are dropped.
   Power, swing and slew are still reported, and `bias_feasible` is untouched.
2. `|Vgs| = 0.69 V` is below `2·I·R = 1.52 V`, so the load branch current the
   DC loop asks for fits inside the 20 µA tail.

</details>

---

## 4.13 `docs/theory/gmid_sizing_flow.rst` (629 lines)

**What it adds.** Read it alongside 4.1–4.12. It is the one page that runs
the pipeline *phase by phase* with a snippet and the printed output at each
step (ptm45, 1.0 V, `enumerate_circuits(...)[15]`, a mirror-loaded two-stage
with a resistor tail). It also has the clearest explanation of **roles vs
functional blocks**
([L117–L211](../../../docs/theory/gmid_sizing_flow.rst#L117)) and a good
"interpreting the verdict" table for Phase 4b
([L431–L458](../../../docs/theory/gmid_sizing_flow.rst#L431)).

**Stale against the code** (checked by running its snippets, 2026-10-31):

| rst location | Says | Code now |
|---|---|---|
| [L67–L85](../../../docs/theory/gmid_sizing_flow.rst#L67) flow chart; [L87–L90](../../../docs/theory/gmid_sizing_flow.rst#L87) | Phase 4 = geometry, bias, (RNMC/two-stage re-plan), resistors; "one explicit repair (the tail re-size)" | Phase 4 also runs `check_stage_interface` inside every `_size`, `tune_bias_levels`, and the follower/CMFB-sense swing checks; the CMRR target wraps Phases 3–5 in a re-size loop (`lift_nodal_cmrr`, #296). Several repairs, not one. |
| [L240](../../../docs/theory/gmid_sizing_flow.rst#L240) | `view.blocks.n_stages` | `AttributeError`: `OpAmpBlocks` has no `n_stages` |
| [L282](../../../docs/theory/gmid_sizing_flow.rst#L282) | `assign_currents(view, spec, tech)` | `TypeError`: `intent` is now required ([plan.py:83](../../../circuitgenome/sizer/gmid/plan.py#L83)) |
| [L477–L478](../../../docs/theory/gmid_sizing_flow.rst#L477) | the pre-check "does not yet check, e.g., second-stage headroom" | the stage-interface check (4.7) now covers the inter-stage window for cascode loads |
| [L583–L590](../../../docs/theory/gmid_sizing_flow.rst#L583) | GBW 15.96 MHz, PM 77.44°, slew 30 V/µs, CMRR −8.2 dB, PSRR 49.47 dB; "the poor `cmrr_db` traces back to the resistor tail" | GBW 15.08 MHz, PM 74.63°, slew 15 V/µs, **CMRR 38.39 dB**, PSRR 38.50 dB; the −8 dB story no longer holds (nodal CMRR, #290/#296) |
| [L600](../../../docs/theory/gmid_sizing_flow.rst#L600) | warnings = topology + ceiling + geometry + DC | also swing verdicts, stage interface, RNMC advisories, evaluate notes, adoption warnings ([gmid_sizer.py:189](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L189)) |
| [L228](../../../docs/theory/gmid_sizing_flow.rst#L228) | `analyze.CircuitView` | the class is `GmIdCircuitView` (`CircuitView` is its base, imported there) |

Still accurate: Phase 2 and 3 printed values, the final
`GMID True 9 1` line, and the description of the RNMC/two-stage re-plans
([L484–L501](../../../docs/theory/gmid_sizing_flow.rst#L484)).

---

## Tests for this stop

| File | Pins |
|---|---|
| [test_loader.py](../../../tests/test_loader.py) | tech/spec YAML → `TechParams`/`SizingSpec` (Stop 1, the inputs) |
| [test_stage_chain.py](../../../tests/test_stage_chain.py) | the chain Phase 5 grades, `two_stage_replan` (4.9) |
| [test_gmid_lut.py](../../../tests/test_gmid_lut.py) | the gm/Id table every phase reads |
| [test_headroom.py](../../../tests/test_headroom.py) | tail headroom repair and cascode budget (`bias.py`) |
| [test_gmid_geometry.py](../../../tests/test_gmid_geometry.py) | LUT → W/L, mirrors, fingers (`geometry.py`) |
| [test_gmid_blocks.py](../../../tests/test_gmid_blocks.py) | the block view (`analyze.py`) |
| [test_block_intent.py](../../../tests/test_block_intent.py) | block → role → gm/Id (`intent.py`) |
| [test_resistors.py](../../../tests/test_resistors.py) | 4.10 |
| [test_bias_levels.py](../../../tests/test_bias_levels.py) | 4.11 |
| [test_fd_three_stage_gmid.py](../../../tests/test_fd_three_stage_gmid.py) | FD and three-stage end-to-end on ptm45 (#75) |

Run them (from the repo root):

```bash
PYTHONPATH=$PWD pytest -q tests/test_loader.py tests/test_stage_chain.py \
  tests/test_gmid_lut.py tests/test_headroom.py tests/test_gmid_geometry.py \
  tests/test_gmid_blocks.py tests/test_block_intent.py tests/test_resistors.py \
  tests/test_bias_levels.py
# 221 passed in 11.3 s (12.0 s wall), 2026-10-31

PYTHONPATH=$PWD pytest -q tests/test_fd_three_stage_gmid.py
# 5 passed in 5.6 s
```

None of these need ngspice. The stage-interface tests live in `test_sizer.py`.
Run them by name, for example
`PYTHONPATH=$PWD pytest -q tests/test_sizer.py::test_stage_interface_repairs_telescopic_cs`
(about 3 s).

---

## Debugger pass

Goal: stop at each phase hand-off of `circuit_0010` and look at what flows
between them. You need **no edits**: `pdb` takes breakpoints by `file:line`.

**1. A five-line driver.** Save it outside the repo, e.g. as `/tmp/size_0010.py`
(it is the [Stop 7](07_end_to_end.md) comparison snippet without the SPICE part):

```python
from pathlib import Path
from circuitgenome.recognizer import parse, recognize, assign_slots
from circuitgenome.synthesizer.loader import load_topologies
from circuitgenome.sizer import load_spec, load_tech, size_circuit

netlist = Path("circuits/two_stage_se/circuit_0010_flat.ckt").read_text()
topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
parsed = parse(netlist); sr = recognize(parsed); fbr = assign_slots(sr, topology)
result = size_circuit(parsed, sr, fbr, topology, load_tech("gf180mcu"),
                      load_spec("examples/two_stage_se_specs/spec_gf180.yaml"))
print(result.bias_feasible, result.capacitors, round(result.metrics["gbw_hz"]))
```

**2. Start it under pdb and set the breakpoints** before the first `c`:

```text
$ PYTHONPATH=$PWD python -m pdb /tmp/size_0010.py
(Pdb) b circuitgenome/sizer/gmid/gmid_sizer.py:94
(Pdb) b circuitgenome/sizer/gmid/gmid_sizer.py:131
(Pdb) b circuitgenome/sizer/gmid/gmid_sizer.py:136
(Pdb) b circuitgenome/sizer/gmid/gmid_sizer.py:153
(Pdb) b circuitgenome/sizer/gmid/gmid_sizer.py:163
(Pdb) b circuitgenome/sizer/gmid/gmid_sizer.py:176
(Pdb) c
```

**3. What to inspect at each stop**, and what you should see (tested
2026-10-31, Python 3.13):

| Stop | Line | Type | You should see |
|---|---|---|---|
| view, currents | [L94](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L94) | `p view.compensation_scheme, view.blocks.load.load_kind` | `(None, <LoadKind.MIRROR: 'mirror'>)` |
| | | `p {r: i*1e6 for r, i in currents.ids_map.items()}` | pair and load 10 µA each, tail and bias 20 µA, second stage 50 µA |
| plan | [L131](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L131) | `p plan.cc_pf, plan.cc2_pf, {r: round(g*1e6,2) for r, g in plan.gm_req_map.items() if g}` | `(1.25, None, {'m1_input_pair': 15.71, 'm2_input_pair': 15.71, 'mn1_second_stage': 415.69})` |
| | | `p plan.tintents["m1_input_pair"]` | `block='input_stage', role='signal', gm_id=None, l_mult=2.0` |
| sizing | [L136](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L136) | `p bias_feasible, size_warnings` | `(True, [])` |
| | | `p sizing["m1_input_pair"]` | `w_um=3.07, l_um=0.56, ids_a=1e-05, vgs_v=-1.083` |
| | | `p sizing["mn1_second_stage"].w_um` | `17.855` |
| refined plan | [L153](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L153) | `p changed, refined is plan` | `(False, True)`: the two-stage re-plan asks for nothing |
| resistors done | [L163](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L163) | `p bias_feasible, extra_r, modifiers` | `(True, {}, MetricModifiers(gm1_factor=1.0, gd_tail_override=None, gd_out_extra=0.0))` |
| metrics | [L176](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L176) | `p {k: round(float(v),3) for k, v in metrics.items()}` | `gain_db 81.53, gbw_hz 7057230.6, phase_margin_deg 63.224, …`; `p eval_notes` gives `[]` |

To see the three flags that
[line 129](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L129) combines, add
`b circuitgenome/sizer/gmid/gmid_sizer.py:128` (the `return` inside `_size`)
and type `p geom_feasible, bias_feasible, si_feasible`. For `circuit_0010` you
get `(True, True, True)`. This breakpoint fires on *every* `_size` call, so
`disable` it once you have seen it.

**Two pdb traps found while testing this:**

- **A breakpoint on a multi-line statement stops before the assignment.**
  `n` then walks through each argument line and comes back to the first line
  before the assignment happens. At L159 (`extra_r, modifiers =
  size_resistors(`) and L172 (`evaluate_circuit(`), the names are still
  undefined. Break on the *next* statement instead (L163, L176). Those two
  also span two lines, so each one stops **twice**. Just press `c` again.
- **Do not break on a list-comprehension line.** In Python 3.12+ comprehensions
  are inlined, so `c` stops again on every iteration (for example
  [stage_interface.py:335](../../../circuitgenome/sizer/gmid/stage_interface.py#L335)).
  Pick the next line.

**4. The repair in action (pytest variant).** `circuit_0010` repairs nothing,
so watch a circuit that does. `--trace` stops at the first line of the test;
set the breakpoints there:

```text
$ PYTHONPATH=$PWD pytest -q -s --trace \
    tests/test_sizer.py::test_stage_interface_repairs_telescopic_cs
(Pdb) b circuitgenome/sizer/gmid/stage_interface.py:336
(Pdb) b circuitgenome/sizer/gmid/stage_interface.py:368
(Pdb) c
(Pdb) p round(pin,3), round(lo,3), round(hi,3)     # (0.887, 1.008, 2.304): pin below the floor
(Pdb) c
(Pdb) p gm_m, gm_s, round(sl,3), round(lo2,3)      # (15.5, None, 0.052, 0.836): mirrors moved
(Pdb) p [d.ref for d in mirror_devs]               # ['mn1_load', 'mn2_load', 'mn3_load', 'mn4_load']
(Pdb) c                                            # 2nd _size, after the two-stage refine raised gm2
(Pdb) p round(pin,3)                               # 0.833
(Pdb) c
(Pdb) p gm_m, round(sl,3)                          # (18.0, 0.058)
(Pdb) c                                            # 1 passed
```

If you prefer editing, insert a temporary `breakpoint()` line directly above
each listed line in `gmid_sizer.py`. Then run the driver with plain `python`,
or a test with `pytest -s` (without `-s`, pytest captures stdin and pdb cannot
read your input). Delete the lines afterwards.

---

## Exit check

**#250: "How does one `feasible=False` in Phase 4 reach
`SizingResult.bias_feasible`?"**

The old hint: one line in `gmid_sizer.py` combines the flags. Notice which
Phase-4 steps are *not* in it, and what `cli.py` does to the flag afterwards.

<details><summary>Answer</summary>

On current code the flag is combined in **four places**, not one.

1. **Inside `_size`**,
   [gmid_sizer.py:128–L129](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L128):
   `bias_feasible and si_feasible and geom_feasible`. Three Phase-4 steps feed
   it:
   - geometry: an output device cannot fit its swing budget,
     [geometry.py:327](../../../circuitgenome/sizer/gmid/geometry.py#L327);
   - DC bias: the tail headroom check, decided by matching `"headroom"` in the
     warning text, plus the cascode-tail budget,
     [bias.py:238](../../../circuitgenome/sizer/gmid/bias.py#L238) and
     [L266](../../../circuitgenome/sizer/gmid/bias.py#L266);
   - stage interface,
     [stage_interface.py:390](../../../circuitgenome/sizer/gmid/stage_interface.py#L390).

   [L131](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L131) stores the
   result in the local `bias_feasible`.
2. **The RNMC loop**,
   [L144–L147](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L144). An
   accepted re-size *replaces* the flag with the re-size's own
   (`resized[2]`). A re-size that turns True into False is first rejected once
   (`hold_gm2`). After that, False is accepted.
3. **The two-stage loop**,
   [L156–L158](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L156). Same
   replacement, but a True→False re-size is rejected outright (`break`), so the
   old flag stays. In both loops a flag that was already False can come back
   True, because a re-size is a new sizing.
4. **After the loops**,
   [L166–L169](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L166): `and
   fol_feasible and cm_feasible` (follower swing and DDA CMFB sense range,
   from `physics.stage_chain`). The result goes into
   `SizingResult(bias_feasible=…)` at
   [L184](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L184).

**Phase-4 steps that are *not* in any combination:**

- `size_resistors`
  ([L159](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L159)) and
  `tune_bias_levels`
  ([L163](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L163)) return no
  flag at all. A bias leg that cannot reach its level just falls back
  silently.
- The Phase-3 ceiling advisories are warnings only.
- The two refine modules' "spec unreachable" outcomes (4.9 returns
  `changed=False`) set no flag.
- The Phase-5 resistor-load check
  ([evaluate.py:118–L120](../../../circuitgenome/sizer/gmid/evaluate.py#L118))
  is a real DC failure (the output rails), yet it only withholds the gain
  metrics. `bias_feasible` stays True.

**One level up**, a CMRR target runs the whole of Phases 3–5 several times.
`lift_nodal_cmrr` keeps a re-size only if it does not lose a sound bias
([preprocess.py:703](../../../circuitgenome/sizer/physics/preprocess.py#L703)),
so the flag of the kept `SizingResult` is what comes out.

**What `cli.py` does afterwards**
([cli.py:243–L248](../../../circuitgenome/cli.py#L243)): it acts only when
the result has transistors, `bias_feasible` is True, and ngspice is installed.
In that case it runs `check_bias_soundness` (SPICE `.op`,
[verify/op.py:264](../../../circuitgenome/sizer/verify/op.py#L264)). If that
fails, it **overrides** the flag to False and appends the reason. It never
turns False into True. Then
[cli.py:304](../../../circuitgenome/cli.py#L304) prints "INFEASIBLE" and
suppresses the metrics. Under that heading it echoes only the warnings that
contain `cascode`, `headroom`, `collapse` or `SPICE bias`. A stage-interface,
follower-swing or CMFB-sense rejection is printed earlier as a plain `⚠` line,
but not under the INFEASIBLE heading.

So the short answer: one `feasible=False` from geometry, DC bias or the stage
interface is AND-ed at L129. It survives the two re-plan loops unless an
accepted re-size replaces it. It is AND-ed again with the swing checks at
L169, and is stored at L184. The CLI can only make it stricter.

</details>

---

## Simplification notes

### 4.1–4.6

Logged only. Nothing was changed.

| Location | Observation |
|---|---|
| `gmid/intent.py` `_SIGNAL_BLOCK` / `functional_block`, with `physics/taxonomy.is_signal_device` | A current-mirror load's gates sit on a signal-looking net (`net_diff1`), so both load devices resolve to `gain_stage` (SIGNAL, L × 2), never `active_load` (CURRENT_SOURCE, L × 4), although `active_load`'s rationale says "First-stage active current-mirror load". In `circuit_0010`, `m1_load`/`m2_load` get L 0.56 µm and gm/Id 10 from the SIGNAL-without-target fallback. `compute_requirements` also estimates the load's gds with the SIGNAL role. Either intended (SPICE agrees well) or the `active_load` row is misleading. |
| `gmid/intent.py` `GmIdIntent` / `make_intent` docstrings | Say the flat role knobs "feed only the pre-geometry gds estimate". They also set the resistor-load bias seed (`plan._load_seed_v` reads `signal_gm_id`, `signal_l_mult`), and `current_source_gm_id` (as `GmIdPolicy.cs_gmid`) is the gm/Id `geometry_for` gives a SIGNAL device with no gm target, e.g. the mirror load above. |
| `gmid/bias.py:69` `_apply_headroom` | Takes `ids_map` but never uses it. `check_dc_operating_point` receives `ids_map` only to forward it, and takes both `blocks` and the raw `slot_transistors`. |
| `gmid/bias.py:238`, `:259` | `bias_feasible` is computed by searching the warning text for `"headroom"`, and the cascode check skips if any warning contains `"cascode"`. The verdict depends on how the warnings are worded. |
| `gmid/bias.py:34` | Imports the private `physics.stage_chain._tail_current_net`: one more cross-module private import (add to README #2). |
| `gmid/gmid_sizer.py:136-147` vs `:151-158` | The two re-plan loops treat a bias-breaking re-size differently. The two-stage loop always discards it. The RNMC loop discards it only once (setting `hold_gm2`); after that, a re-size that still breaks the bias is adopted. Maybe intended (Cc2/gm3 are the last knobs), but the comment "a gm2 raise that breaks a sound bias is undone" reads as stricter. |
| `gmid/geometry.py` module docstring | Lists 5 steps. The code has 7 numbered steps plus the swing gm/Id floor (step 1) and the raw swing check on mirror outputs (step 7), which the list leaves out. (The "for PTM nodes" wording is README #6.) |
| `gmid/analyze.py` `_has_cascode` vs `cascode_device_refs` | Two cascode detectors with slightly different rules: `_has_cascode` accepts any same-slot source-on-drain pair; `cascode_device_refs` also excludes signal devices and rail sources. |
| `docs/theory/gmid_sizing_flow.rst` | "Flow at a glance" leaves out `stage_interface.py`, `bias_levels.py` and the follower / CMFB swing checks, and names Phase 1's output `CircuitView` (it is `GmIdCircuitView`). The block-registry table says `output_stage` gm/Id is "solved"; the code fixes it at 15 (`_FOLLOWER`). Its worked numbers are ptm45 at 1.0 V, not gf180. |
| `docs/_extra/walkthrough/gmid/analyze_explained.html`, `blocks_explained.html`, `dc-bias-feasibility_explained.html` | Behind the code. `analyze_explained` covers `_adopt_orphan_mosfets` and the `CircuitView` assembly, now in `physics/circuit_view.py`. `blocks_explained` Parts 4–5 (`_looking_in_drain`, `node_rout`) are now in `physics/stage_chain.py`. `dc-bias-feasibility` presents `_apply_headroom` as the repair; the body is now `_repair_tail_headroom`, shared with the CMFB tail. |

### 4.7–4.13

| Location | Observation |
|---|---|
| `physics/metrics.py:131` `two_stage_replan.least` | Assumes PM rises monotonically with `gm2`, but `c1_at` makes it peak. For `circuit_0010` with `phase_margin_min_deg: 70`, PM is about 71.9° at `gm2 ≈ 1000 µS` but 67.1° at the 1200 µS ceiling, so `least` tests only the ceiling, gives up, and the design ships at 65.97° (behaviour, worth an issue). |
| `gmid/two_stage_refine.py` | An unreachable GBW/PM spec is silent: no advisory, unlike RNMC's `design_warnings`. Only the negative margin shows it. |
| `rnmc_refine._node_cap_at` vs `two_stage_refine.c1_at` | Two copies of "node cap after re-sizing this device for gm". One uses `cgs`, the other `cgg` at the inversion level (#274). Only the first clamps the width to `width.max`. |
| `gmid_sizer.py:136–158` | The RNMC and two-stage loops are near-duplicates with different "breaks a sound bias" policies (retry with `hold_gm2` vs `break`). They could be one helper with a policy argument. |
| `two_stage_refine.py` docstring | "`changed` is True when either requirement rose", but it returns True whenever `two_stage_replan` returns a value, even if `max(gm_req, gm)` leaves the request unchanged (a capped device whose request already exceeds its sized `gm`). In that case it re-sizes identically. |
| refine chains (`rnmc_refine.py:84`, `two_stage_refine.py:51`) | Built without `bias_aware`, `resistor_ohms` or `MetricModifiers`, which is deliberate per the `build_stage_chain` docstring. So the re-plan judges a different chain than Phase 5 grades (`circuit_0010`: 7.062 vs 7.057 MHz), and a degenerated pair's `gm1_factor` is invisible to it (effect not measured). |
| `stage_interface.py` module docstring | Still describes the FD interface-sensing CMFB as the live case. Since #165 the shipped CMFBs sense the outputs, so `_check_fd_interface` returns early for them (per `test_fd_stage_interface_exempts_output_sensing_cmfb`). |
| `stage_interface._pin_device` / `evaluate._driven_cs_device` | The same slot scan for "the next stage's signal device", written twice. |
| `stage_interface._MARGIN_V`, `bias_levels._MARGIN_V`, `resistors._CASCODE_SAT_MARGIN_V` | Three saturation-margin constants (0.05, 0.05, 0.1 V) for related walks. |
| `resistors.size_resistors` | The `ids_map` and `tech` parameters are never read in the body; `model` is untyped. |
| `bias_levels._tune_cascode_legs` | The leg current comes from `ids_map` for `R`, but from `s.ids_a` for the diode width. Two sources for one current. |
| `bias.py:238` | The DC verdict is `not any("headroom" in w for w in warnings)`, so feasibility is decided by warning *wording*. |
| `evaluate._resistor_load_bias` | A railed resistor-load output withholds metrics but leaves `bias_feasible=True`. |
| `models.py:274` `SizingResult.bias_feasible` docstring | Names only the DC operating-point check. Six sources now set it (geometry, DC, stage interface, follower swing, CMFB sense, CLI SPICE `.op`). |
| `cli.py:306` | The INFEASIBLE reason filter (`cascode/headroom/collapse/SPICE bias`) misses the stage-interface, swing and geometry rejection wordings. |
| `docs/theory/gmid_sizing_flow.rst` | Two snippets crash (`n_stages`, `assign_currents` without `intent`). The Phase 5 output and CMRR narrative are stale. The flow chart misses stage_interface, bias_levels, the swing checks and the CMRR lift loop. See the 4.13 table. |
