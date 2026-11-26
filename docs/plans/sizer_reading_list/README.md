# Sizer reading list

**Date**: 2026-10-31 (written against `main` @ `8571bf9`)
**Scope**: `circuitgenome/sizer` (Layer 3, ~10,100 lines of Python in 37 files)
**Tracking**: [#250](https://github.com/analog-ml/CircuitGenome/issues/250)
**Supersedes**: `docs/plans/2026_09_30_sizer_walkthrough.md` on branch
`docs/sizer-walkthrough` (written on `a193951`, about 3,800 lines of sizer code ago)

A guided, plain-language reading list for the sizer, written for a software
engineer who is new to analog design. It keeps the stop order agreed in #250,
adds an analog primer in front, and walks through each file's main code path,
linking every step back to the design idea behind it.

---

## The files

| Stop | File | What you read | Time |
|---|---|---|---|
| 00 | [Analog background](00_background.md) | the circuit ideas the code relies on: the glossary every other stop links to | 1 h |
| 0 | [Orientation](00_orientation.md) | `docs/modules/sizer.rst`, `sizer_explained.html`: the big picture, no code | 30 min |
| 1 | [Contracts](01_contracts.md) | `models.py`, `loader.py`, tech + spec YAML, `test_loader.py` | 1 h |
| 2 | [Dispatcher](02_dispatcher.md) | `sizer.py`, `__init__.py` | 20 min |
| 3 | [`physics/`](03_physics.md) | taxonomy → equations → gmid_lut → device_model → circuit_view → rnmc → preprocess → node_levels → stage_chain → metrics | 6–8 h |
| 4 | [`gmid/`](04_gmid.md) | gmid_sizer (the map) → analyze → intent → plan → geometry → bias → stage_interface → rnmc_refine → two_stage_refine → resistors → bias_levels → evaluate | 6–8 h |
| 5 | [`analytical/`](05_analytical.md) | constraints → level1 | 1 h |
| 6 | [`verify/`](06_verify.md) | simulate → deck → rig → measure → op | 2–3 h |
| 7 | [Run it end to end](07_end_to_end.md) | synthesize, size, compare analytical vs SPICE, run tests | 1 h |

The order differs from the #250 checklist in four places. Each was checked
against the files' actual imports and the order `size_gmid` runs them:

- `physics/rnmc.py` moves **before** `preprocess.py`, because `preprocess`
  imports it (`compute_requirements` calls `design_rnmc`). The checklist order
  had it after.
- `physics/node_levels.py` (new since the checklist) sits right **before**
  `stage_chain.py`, its only caller, so you read it and then see it used. It
  needs only `device_model` and `taxonomy`.
- `gmid/stage_interface.py` moves **before** the two refine files. `_size` runs
  geometry → bias → stage interface first, and the refine loops re-run that
  trio and refer back to the stage-interface window.
- `gmid/two_stage_refine.py` (new since the checklist) sits next to
  `rnmc_refine.py`. Both are "re-plan on the sized circuit" loops.

---

## The big picture in one diagram

```
                 size_circuit()  (sizer.py — dispatcher)
                 /              \
  tech has gm/Id LUT?          no LUT, no SPICE model (generic)?
        |                             |
   gmid/  (5-phase pipeline)    analytical/  (Level-1 square-law + CP-SAT)
        \                             /
         physics/  (topology math shared by both)

   verify/  (ngspice re-simulation — consumes SizingResult; called by the CLI and the designer, never by the sizers)
```

Every file does exactly one of four jobs. When lost, ask which one:

| Layer | Job | Decides widths? | Runs a simulator? |
|---|---|---|---|
| `models.py`, `loader.py` | Contracts: what goes in, what comes out | No | No |
| `physics/` | Circuit math: formulas, LUT, stage chain | **No** | No |
| `gmid/`, `analytical/` | Decisions: pick W/L | **Yes** | No |
| `verify/` | Referee: re-measure in ngspice | No | **Yes** |

---

## How every stop file is laid out

1. **Background for this stop**: a short recap of the concepts it needs, linked
   to [00_background.md](00_background.md), plus any extra concept only this stop
   uses (with an analogy).
2. **One section per source file**, in reading order:
   - *Job in one sentence*
   - *Design knowledge it encodes*: links back to the background
   - *Walkthrough*: the main code path, key functions only, each step as
     "code at `file:line` → what it does → which design idea it is". Helpers are
     named, not explained.
   - *Running example*: what this file does to `circuit_0010`
   - *Test to read*: which test pins the behaviour, and what it asserts
   - *Check yourself*: two or three questions
3. **Exit check**: the #250 question for the stop, with a hidden answer
   (`<details>`). Try it before you peek.
4. **Simplification notes**: things noticed while writing; logged, not fixed.

Line references are links like
[`stage_chain.py:42`](../../../circuitgenome/sizer/physics/stage_chain.py#L42),
taken at `8571bf9`. If a line has drifted, search for the function name.

---

## How to read

1. **Per file**: module docstring → public signatures → the file's test → the
   bodies. The tests say which behaviour matters.
2. **Dependency order**: each stop only uses code from earlier stops.
3. **Code over HTML**: the `docs/_extra/walkthrough/` pages are good for
   intuition but several are behind the code. Skip `walkthrough/shared/`: it is
   a "Moved" stub left from before the `physics/` rename.
4. **Log, don't fix**: add simplification ideas to the table below and file
   issues after the walkthrough.
5. **Environment**: run from the repo root and prefix `PYTHONPATH=$PWD`, so the
   working tree is imported, not an older installed copy.

---

## Running example

Follow one circuit through every stop: **`circuit_0010`** of
`two_stage_opamp_single_ended`, the textbook two-stage op-amp. It has a PMOS
input pair, an NMOS current-mirror load, an NMOS common-source second stage and
a Miller cap.

```
.subckt circuit_0010 ibias in1 in2 out vdd! gnd!
m1_input_pair net_diff1 in1 net_tail net_tail pmos     ┐ PMOS differential pair
m2_input_pair net_mid   in2 net_tail net_tail pmos     ┘
m1_load net_diff1 net_diff1 gnd! gnd! nmos             ┐ NMOS current-mirror load
m2_load net_mid   net_diff1 gnd! gnd! nmos             ┘ (m1 is the diode)
m1_tail_current net_bias7 net_bias7 vdd! vdd! pmos     ┐ PMOS tail mirror
m2_tail_current net_tail  net_bias7 vdd! vdd! pmos     ┘
mnref_bias_gen ibias ibias gnd! gnd! nmos              ┐
mn5_bias_gen net_bias5 ibias gnd! gnd! nmos            │ bias generator:
mp5_bias_gen net_bias5 net_bias5 vdd! vdd! pmos        │ ibias → net_bias5, net_bias7
mn7_bias_gen net_bias7 ibias gnd! gnd! nmos            ┘
c1_compensation net_mid out 1p                         Miller cap Cc
mn1_second_stage out net_mid gnd! gnd! nmos            ┐ NMOS common-source stage
mp1_second_stage out net_bias5 vdd! vdd! pmos          ┘ with PMOS current-source load
.ends
```

Its contrast is **`circuit_0001`**: the same circuit with a **resistor load**
instead of the mirror.

Measured on gf180mcu with `examples/two_stage_se_specs/spec_gf180.yaml`
(2026-10-31, `8571bf9`):

| Metric | Spec | `circuit_0010` analytical | `circuit_0010` ngspice | `circuit_0001` analytical | `circuit_0001` ngspice |
|---|---|---|---|---|---|
| Gain | ≥ 60 dB | 81.5 dB | 81.2 dB | 52.1 dB | 52.3 dB |
| GBW | ≥ 2 MHz | 7.06 MHz | 6.93 MHz | 3.52 MHz | 3.41 MHz |
| Phase margin | ≥ 60° | 63.2° | 63.5° | 81.4° | 81.8° |
| Slew | ≥ 0.3 V/µs | 8.0 V/µs | 8.9 V/µs | 8.0 V/µs | 6.4 V/µs |
| Power | ≤ 2 mW | 0.363 mW | 0.378 mW | 0.363 mW | 0.378 mW |
| Swing max | ≥ 3.0 V | 3.15 V | 3.29 V | 3.15 V | 3.20 V |
| Swing min | ≤ 0.3 V | 0.14 V | 0.04 V | 0.08 V | 0.07 V |
| CMRR | — | 80.1 dB | 78.2 dB | 38.9 dB | 39.2 dB |
| PSRR | — | 90.5 dB | 86.8 dB | 35.0 dB | 35.0 dB |

The sizing itself: Cc = 1.25 pF. Each input-pair device is 3.07/0.56 µm at
10 µA, the tail is 42.1/1.12 µm at 20 µA, the second-stage NMOS is
17.9/0.56 µm at 50 µA, and its PMOS load is 105.2/1.12 µm at 50 µA, drawn as
2 fingers.

**What changed since #250's table** (measured on `a193951`): the large gaps
(PM 67° vs 83°, power 0.56 vs 0.37 mW, CMRR 41 vs 78 dB, PSRR 58 vs 86 dB) are
gone. The sizing itself barely changed. What changed is how each number is
computed, on both sides:

| Gap | Fixed by |
|---|---|
| PM, SPICE side (~20°) | the old AC bench had no load cap; PR #237 attached `CL` (`verify/`) |
| PM, model side (67.3° → 63.2°) | full nodal loop model `equations.two_stage_loop` (#274, PR #279): sees the RHP zero and the lower 0 dB crossing |
| Power (0.56 → 0.36 mW) | `stage_chain.vdd_supply_currents` (PR #291): stopped counting `ibias` once per bias-generator device |
| CMRR (41 → 80 dB), PSRR (58 → 90 dB) | nodal CMRR/PSRR solve in `stage_chain.py` (PR #294) replaced the closed forms |
| Gain (82.7 → 81.5 dB) | reading each device at its own `Vds` (`node_levels.py`, #273, PR #277) |

Analytical and SPICE now agree within a few percent on every metric except slew
and swing. Stop 3's exit check walks through each fix.

---

## Known gaps (don't rediscover)

- gm/Id GBW and swing gaps vs SPICE on some topologies are real model
  inaccuracy (LUT measured at one bias point, `Vds = Vdd/2`).
- sky130 shows a ~+17 % GBW over-prediction on main.
- Fully differential circuits depend on the CMFB fixes from #167 / #236.

---

## Simplification candidates

Each stop file ends with its own **Simplification notes** table (about 100
entries in all, each with a code link). This section keeps the cross-cutting
summary. File each as an issue before refactoring.

### Possible bugs: worth an issue first

| # | Where | What | Stop |
|---|---|---|---|
| B1 | `tests/test_cli.py` | 7 of 10 tests skip silently unless netlists exist in `circuits/two_stage_opamp_single_ended/` (the guides use `circuits/two_stage_se/`). With the fixtures present, `test_size_ptm45_infeasible_verdict` and `test_size_spice_bias_infeasible` **fail**: they pick variants by number, and those variants have changed | [7](07_end_to_end.md) |
| B2 | `physics/metrics.py::two_stage_replan` | Assumes PM rises steadily with gm2, so it only tests the gm2 ceiling. On `circuit_0010` with a 70° PM spec, PM peaks at ~71.9° near gm2 ≈ 1000 µS, but the search gives up and ships 66° with no warning | [4](04_gmid.md) |
| B3 | `sizer.py:62` | The "SPICE model without a LUT → error" guard checks only `spice_model`; a `spice_lib` PDK without a LUT but with square-law numbers falls through to Level-1 (verified) | [2](02_dispatcher.md) |
| B4 | `gmid/two_stage_refine.py`, `gmid/gmid_sizer.py` | An analytical PM miss (sky130 −11.4°) prints no warning; the RNMC path warns in the same situation | [4](04_gmid.md), [7](07_end_to_end.md) |
| B5 | `gmid/gmid_sizer.py` | The RNMC loop rejects a bias-breaking re-size only once (`hold_gm2`); the two-stage loop always rejects. An accepted re-size can flip `bias_feasible` from False back to True | [4](04_gmid.md) |
| B6 | `loader.py:20` | `load_spec` silently drops a misspelt target key, so that target becomes unconstrained | [1](01_contracts.md) |
| B7 | `gmid/intent.py::functional_block` | Mirror-load gates sit on a signal net, so the mirror load resolves to `gain_stage` intent (L×2), never `active_load` (L×4). It may be intended: SPICE agrees | [4](04_gmid.md) |

### Structure and seams

| # | Where | What |
|---|---|---|
| S1 | many | Private helpers imported across modules: `resistors._bias_rail_target_v`, `preprocess._first_stage_gain_factor` / `_mirror_reference`, `equations._poles`, `stage_chain._tail_current_net`, `plan._model_for`, and many `deck._*` / `rig._*` inside `verify/` |
| S2 | `physics/equations.py:18` | `physics/` imports constants from `verify.measure`: the shared math depends on the referee, contradicting `verify/__init__.py` |
| S3 | `sizer.py`, `gmid/gmid_sizer.py`, `analytical/level1.py` | `parsed` and `sr_result` are threaded through both sizers but never read; `time_limit_s` is ignored on the gm/Id route |
| S4 | `analytical/level1.py` vs `gmid/two_stage_refine.py`; RNMC vs two-stage loops in `gmid_sizer.py`; `rnmc_refine._node_cap_at` vs `two_stage_refine.c1_at` | Parallel copies of the same re-plan logic that have drifted apart |
| S5 | `physics/stage_chain.py` | Three separate nodal-matrix builders (`_se_rejection_db`, `_cmfb_coupling`, `equations._Network`); `StageChain` has 20 fields; `build_stage_chain` always runs every solve, even for callers that need only the stages |
| S6 | `physics/preprocess.py` | `compute_requirements` is ~340 lines returning a 5-tuple; `lift_nodal_cmrr` is a sizing loop living in `physics/` |
| S7 | `gmid/bias.py:238,259` | The feasibility verdict is decided by searching warning text for "headroom" / "cascode" |
| S8 | `verify/op.py`, `verify/measure.py` | Decks hand-built outside `rig._deck`; "try both polarities" logic implemented twice, with a third rule in the AC bench |
| S9 | `gmid_lut.max_gm_id`, `size_resistors`, `bias._apply_headroom` | Parameters that are accepted but never read |

### Stale docs and docstrings

| # | Where | What |
|---|---|---|
| D1 | `sizer/__init__.py`, `analytical/__init__.py`, `gmid_lut.py`, `device_model.py`, `geometry.py`, `sizer.py` error text, `cli.py:272` | "CP-SAT" / "for PTM nodes" wording; gf180mcu and sky130 take the gm/Id path too |
| D2 | `physics/equations.py` | Module docstring still says "Level-1 equations" and that only RNMC is numeric; nodal-model docstrings use undefined terms (VCCS, false position, "returns"); `psrr_db_approx` formula and parameter names disagree |
| D3 | `models.py` | `MosfetParams` / `TechParams` / `spice_model` / `bias_feasible` docstrings are behind the code (your open #250 TODO) |
| D4 | `physics/device_model.py`, `gmid/resistors.py`, `physics/__init__.py` | Wrong module paths (`sizer.sizer`, `spice.deck`); the package map omits `node_levels` and `rnmc` |
| D5 | `docs/modules/sizer.rst`, `docs/theory/sizing_flow.rst`, `docs/theory/gmid_sizing_flow.rst` | Seven metrics listed (no PSRR); no sky130; old flow charts; two `gmid_sizing_flow.rst` snippets crash; stale Phase-5 numbers |
| D6 | `docs/_extra/walkthrough/` | `shared/` is a "Moved" stub; `sizer_explained`, `models_explained`, `loader_explained`, `analyze_explained`, `blocks_explained`, `dc-bias-feasibility`, `level1_explained` describe moved or changed code |
| D7 | `examples/two_stage_se_specs/` | README lists only `generic`/`ptm45`; `spec_generic.yaml` claims "Feasible" (`circuit_0010` misses PM and slew); `spec_sky130.yaml` header numbers are stale |
| D8 | `cli.py` | The sizing table drops `fingers`; the INFEASIBLE hint says "run --simulate" (a no-op on SPICE techs); the reason filter misses stage-interface and swing rejections |
