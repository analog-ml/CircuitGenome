# Sizer Walkthrough — Reading Guide and Simplification Log

**Date**: 2026-09-30
**Scope**: `circuitgenome/sizer` (Layer 3, ~6,300 lines)
**Tracking**: [#250](https://github.com/analog-ml/CircuitGenome/issues/250)
**Purpose**: a fixed reading order for understanding the sizer end to end, plus a
place to record simplification candidates found along the way.

---

## Big picture

`size_circuit` (`sizer.py`) takes the Layer-0 parsed netlist, the Layer-2 FBR
result, a topology template, a tech and a spec, and returns a `SizingResult`.
Two sizers sit on one shared physics layer; `verify/` is a peer that only
consumes the result.

```
                 size_circuit()  (sizer.py — dispatcher)
                 /              \
  tech has gm/Id LUT?          tech == "generic"?
        |                             |
   gmid/  (5-phase pipeline)    analytical/  (Level-1 square-law + CP-SAT)
        \                             /
         physics/  (topology math shared by both)

   verify/  (ngspice re-simulation — consumes SizingResult, not used by sizers)
```

A SPICE-model tech without a LUT raises `UnsupportedTechError` by design (#73).

Every file does exactly one of four jobs. When lost, ask which one:

| Layer | Job | Decides widths? | Runs a simulator? |
|---|---|---|---|
| `models.py`, `loader.py` | Contracts: what goes in, what comes out | No | No |
| `physics/` | Circuit math: formulas, LUT, stage chain | **No** | No |
| `gmid/`, `analytical/` | Decisions: pick W/L | **Yes** | No |
| `verify/` | Referee: re-measure in ngspice | No | **Yes** |

---

## How to read

1. **Per file**: module docstring → public signatures → the file's test → the
   bodies. The tests say which behaviour matters.
2. **Dependency order**: every stop below only uses code from earlier stops.
3. **Code over HTML**: several walkthrough pages predate their file's last change
   (`sizer_explained` 2026-07-22 vs code 2026-08-25; `geometry`,
   `stage_interface`, `resistors`, `bias`, `stage_chain` a few days behind). Use
   pages for intuition, code for truth.
4. **Log, don't fix**: add simplification ideas to the table at the bottom; file
   issues after the walkthrough, not during it.
5. **Environment**: run from the repo root. If imports look stale, prefix
   `PYTHONPATH=$PWD` so the working tree is imported, not an older installed copy.

---

## Running example

Follow one circuit through every stop: **`circuit_0010`** of
`two_stage_opamp_single_ended` — PMOS input pair, current-mirror load, NMOS
common-source second stage, Miller Cc. The textbook two-stage op-amp. Generate it
in Stop 7 step 1 (or right now) and keep its numbers in view.

Measured on gf180mcu with `examples/two_stage_se_specs/spec_gf180.yaml`
(2026-09-30):

| Metric | Analytical (`result.metrics`) | ngspice | Question to answer while reading |
|---|---|---|---|
| Gain | 82.7 dB | 81.2 dB | — close |
| GBW | 7.64 MHz | 7.47 MHz | — close |
| Phase margin | 67.3° | 83.3° | Which pole does the analytical PM over-weight? (Stop 3 `equations.py`) |
| Slew | 16.0 V/µs | 15.5 V/µs | — close |
| Power | 0.56 mW | 0.37 mW | Which branch does the analytical sum count that SPICE doesn't draw? (Stop 3 `metrics.py`) |
| CMRR | 40.6 dB | 77.6 dB | Why is the analytical CMRR ~37 dB pessimistic? (Stop 3 `stage_chain.py`, Stop 4 `evaluate.py`) |
| PSRR+ | 58.4 dB | 86.3 dB | Same question for PSRR |

These gaps are observations, not known causes; answering them is the best test
of whether Stops 3–4 made sense.

---

## Reading order

Tick each box when done. Walkthrough pages live under `docs/_extra/walkthrough/`.
Skip `walkthrough/shared/` — it is a "Moved" stub left from before the
`physics/` rename.

### Stop 0 — Orientation (no code)
- [ ] `docs/modules/sizer.rst` — Overview, Path selection, SPICE verification
- [ ] `walkthrough/sizer_explained.html`
- **Exit check**: say from memory which path each tech (`generic`, `gf180mcu`,
  `sky130`, `ptm45`) takes, and why.

### Stop 1 — Contracts
- [ ] `models.py` (262) — `SizingSpec` (the wish list), `TechParams` (its
      `gmid_lut` / `spice_model` fields decide the path), `MosfetParams`,
      `GridSpec`, `SpiceLib`, `TransistorSizing`, `SizingResult` (the answer)
- [ ] `loader.py` (152) — `load_tech` takes a built-in name (`"gf180mcu"`), a
      YAML path, or nothing (→ `generic`); `load_spec`
- [ ] `circuitgenome/sizer/config/tech_gf180mcu.yaml` +
      `examples/two_stage_se_specs/spec_gf180.yaml`
- [ ] `walkthrough/models_explained.html`, `walkthrough/loader_explained.html`
- [ ] Tests: `tests/test_loader.py`
- **Exit check**: name every `SizingResult` field and which phase fills it.

### Stop 2 — Dispatcher
- [ ] `sizer.py` (71) — dispatch is on tech *fields*, not the tech name;
      `intent_for_tech` looks up `INTENT_BY_TECH`, which is empty, so every
      tech gets `DEFAULT_INTENT`
- **Exit check**: explain why "SPICE model, no LUT → error" is policy, not a gap.

### Stop 3 — `physics/` (shared foundation; read before either sizer)
Dependency order — each file only imports files above it:
- [ ] `taxonomy.py` (54) — slot and net naming conventions; the single place a
      new topology's naming is taught to the sizer
- [ ] `equations.py` (287) — Level-1 device formulas + op-amp formulas (gm, rout,
      GBW, PM, slew, CMRR, PSRR). Skim; come back when a later file calls one
- [ ] `gmid_lut.py` (100) — `GmIdLut`: bilinear interpolation over
      `(gm/Id, L)` from `config/models/*_gmid.npz`
- [ ] `device_model.py` (311) — `DeviceModel` protocol; `Level1Model` vs
      `GmIdModel` (wraps `GmIdLut`); roles `SIGNAL` / `CURRENT_SOURCE` / `CASCODE`
- [ ] `circuit_view.py` (195) — `CircuitView`: per-slot devices, dedup map,
      orphan adoption, topology-mismatch warnings
- [ ] `preprocess.py` (431) — `assign_ids` (KCL), `size_load_resistors`,
      `compute_requirements` (spec → gm targets, Cc)
- [ ] `stage_chain.py` (428) — `node_rout` (cascode-aware walk: a cascode
      multiplies the resistance below it by `1 + gm·R`), `Stage`, `StageChain`,
      `build_stage_chain`. Densest file; budget the most time here
- [ ] `metrics.py` (133) — `evaluate_metrics`: pure algebra over a `StageChain`
- [ ] `walkthrough/physics/physics-*.html`
- [ ] Tests: `pytest tests/test_stage_chain.py tests/test_gmid_lut.py`
- **Exit check**: "Nothing here decides a width. Nothing here runs a simulator."
  holds — and you can answer at least one running-example question above.

### Stop 4 — `gmid/` (main production path)
Keep `gmid/gmid_sizer.py` (101) open the whole time; it is the map. Read it
first, then return to it after each file below.

| Phase | File(s) | Output |
|---|---|---|
| 1 Analyze | `analyze.py` | `GmIdCircuitView` |
| 2 Bias currents | `plan.py` → `assign_currents` | `CurrentPlan` |
| 3 Plan | `plan.py` → `plan_devices`, `intent.py` | `SizingPlan` |
| 4 Size | `geometry.py` → `bias.py` → `stage_interface.py` → `resistors.py` → `bias_levels.py` | W/L, repairs, resistors |
| 5 Evaluate | `evaluate.py` | metrics, margins, notes |

- [ ] `gmid_sizer.py` — the five phases and their hand-offs
- [ ] `analyze.py` (185) — Phase 1: `GmIdCircuitView`, the block view
      (`OpAmpBlocks`, `build_blocks`, `LoadKind`/`classify_load`), cascode refs.
      Pages: `analyze_explained.html` **and** `blocks_explained.html` (the block
      view lives in `analyze.py`). Test: `test_gmid_blocks.py`
- [ ] `intent.py` (327) — read before `plan.py`: both phases 2 and 3 take
      `intent`. Three levels: spec → `BlockIntent` (per block, with rationale) →
      `TransistorIntent` (per device). Test: `test_block_intent.py`
- [ ] `plan.py` (117) — Phase 2 `assign_currents` → `CurrentPlan` (what KCL
      fixes); Phase 3 `plan_devices` → `SizingPlan` (gm requirements, Cc,
      per-device intent). No geometry exists yet
- [ ] `geometry.py` (323) — deterministic forward pass: LUT `Id/W` → W, snap to
      grid, symmetry, mirror ratios, load margin. Test: `test_gmid_geometry.py`
- [ ] `bias.py` (218) — tail headroom check and repair, cascode stack budget.
      Page: `dc-bias-feasibility_explained.html`. Test: `test_headroom.py`
- [ ] `stage_interface.py` (390, largest in `gmid/`) — first-stage output pin
      vs the load's saturation window (#124). Test:
      `test_sizer.py::test_stage_interface_repairs_telescopic_cs`
- [ ] `resistors.py` (274) — non-load resistors (bias, tail, degeneration,
      compensation). **Hidden link**: returns `MetricModifiers`, which
      `evaluate.py` consumes — resistor sizing changes the reported metrics.
      Test: `test_resistors.py`
- [ ] `bias_levels.py` (236) — level devices of the constructed bias generator.
      Test: `test_bias_levels.py`
- [ ] `evaluate.py` (115) — Phase 5: builds the `StageChain`, folds in the
      resistor modifiers, withholds gain metrics when the DC bias makes them
      meaningless
- [ ] `docs/theory/gmid_sizing_flow.rst` (read alongside, not before)
- [ ] `walkthrough/gmid/*.html`
- [ ] Tests (all of Stops 1, 3, 4 together: 130 tests, ~32 s):
      `pytest tests/test_loader.py tests/test_stage_chain.py tests/test_gmid_lut.py tests/test_headroom.py tests/test_gmid_geometry.py tests/test_gmid_blocks.py tests/test_block_intent.py tests/test_resistors.py tests/test_bias_levels.py`
      — plus `tests/test_fd_three_stage_gmid.py` for fully differential
- [ ] **Debugger pass**: put a temporary `breakpoint()` after each phase in
      `size_gmid`, run
      `pytest tests/test_sizer.py::test_stage_interface_repairs_telescopic_cs -s`,
      and inspect `view`, `currents`, `plan`, `sizing` at each stop
- **Exit check**: trace how a single `feasible=False` in phase 4 reaches
  `SizingResult.bias_feasible`. Hint: one line in `gmid_sizer.py` combines the
  flags — notice which phase-4 steps are *not* in it, and what `cli.py` does to
  the flag afterwards.

### Stop 5 — `analytical/` (Level-1 CP-SAT, `generic` tech only)
- [ ] `analytical/constraints.py` (206) — `build_model`; the docstring's
      linearisation (gm and Vdsat bounds become linear in integer W, L) is the
      key idea
- [ ] `analytical/level1.py` (110) — `size_level1`; uses
      `physics.circuit_view.analyze_circuit` directly (no block view), then the
      same `preprocess` / `stage_chain` / `metrics` as gm/Id
- [ ] `docs/theory/sizing_flow.rst`
- [ ] `walkthrough/analytical/*.html`
- [ ] Tests: the `generic` cases in `tests/test_sizer.py` (fixture at line 35)
- **Exit check**: contrast grid search (here) with the forward pass (Stop 4), and
  list which `physics/` functions both paths share.

### Stop 6 — `verify/` (ngspice cross-check)
The sizers never import `verify/`; the CLI does (`check_bias_soundness` at
`cli.py:244`, `simulate_metrics` at `cli.py:320`).
- [ ] `simulate.py` (95) — `simulate_metrics`: same keys as `evaluate_metrics`
- [ ] `deck.py` (254) — model emission, subckt parsing, `_inject_sizes`, ngspice
      runners; public `ngspice_available`, `sized_netlist`, `pdk_netlist`
- [ ] `rig.py` (100) — port map, input polarity, bias-current direction
- [ ] `measure.py` (396) — one testbench per metric; best-effort
- [ ] `op.py` (246) — `.op` reading and the `check_bias_soundness` verdict
- [ ] `walkthrough/verify/verify-*.html`
- [ ] Tests (need ngspice, slow — run single tests with `-k`):
      `tests/test_spice_sim.py`, `tests/test_dc_op.py`,
      `tests/test_sized_netlist_parser.py`
- **Exit check**: explain why a failed measurement returns `None` instead of raising.

### Stop 7 — Run it end to end
1. [ ] Generate the variants (162 files; `circuits/` is git-ignored):
   ```bash
   circuitgenome synthesize --topology two_stage_opamp_single_ended \
     --output-dir circuits/two_stage_se
   ```
2. [ ] Size the running example (~20 s including the corner sweep):
   ```bash
   circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
     --topology two_stage_opamp_single_ended --tech gf180mcu \
     --spec examples/two_stage_se_specs/spec_gf180.yaml
   ```
   Expect `Feasibility: FEASIBLE`, gain ≈ 81 dB. For LUT techs the printed metrics
   are already ngspice-measured; `--simulate` is redundant and says so.
3. [ ] Contrast: same command on `circuit_0001` (resistor load) → `MARGINAL`,
   gain ≈ 52 dB < 60 dB spec. Explain why from Stop 3.
4. [ ] Analytical vs SPICE — the CLI never prints the analytical numbers for a
   LUT tech, so compare in Python (produced the running-example table):
   ```python
   from pathlib import Path
   from circuitgenome.recognizer import parse, recognize, assign_slots
   from circuitgenome.synthesizer.loader import load_topologies
   from circuitgenome.sizer import load_spec, load_tech, size_circuit, simulate_metrics

   netlist = Path("circuits/two_stage_se/circuit_0010_flat.ckt").read_text()
   topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
   parsed = parse(netlist)
   sr = recognize(parsed)
   fbr = assign_slots(sr, topology)
   tech = load_tech("gf180mcu")
   spec = load_spec("examples/two_stage_se_specs/spec_gf180.yaml")

   result = size_circuit(parsed, sr, fbr, topology, tech, spec)
   spice = simulate_metrics(netlist, result, tech, spec)
   for k, v in result.metrics.items():
       print(f"{k:22s} analytical={v!s:>12.10}  spice={spice.get(k)!s:>12.10}")
   ```
5. [ ] `pytest tests/test_sizer.py` (71 tests, ~15 min; no `slow` marks in this
   file, so `-m "not slow"` does not shorten it — pick single tests with `-k`
   while reading)

---

## Known gaps (don't rediscover)

- Analytical cascode gain reads 6–21 dB above ngspice (GF180, telescopic and folded).
- gm/Id GBW and swing gaps vs SPICE are real model inaccuracy.
- FD circuits depend on the CMFB fixes from #167 / #236.

---

## Simplification candidates

Record findings here while reading; file each as an issue before refactoring.

| # | Location | Observation | Issue |
|---|---|---|---|
| 1 | `sizer/__init__.py` docstring | Says the sizer uses OR-Tools CP-SAT; only true for the `generic` path | — |
| 2 | `gmid/bias_levels.py:36`, `physics/stage_chain.py:27`, `verify/{simulate,op,measure}.py` | Private helpers imported across modules (`resistors._bias_rail_target_v`, `preprocess._first_stage_gain_factor`, `deck._run`/`_inject_sizes`, `rig._deck`/`_rig`); "private" is not private at these seams | — |
| 3 | `sizer.py`, `gmid/gmid_sizer.py`, `analytical/level1.py` | `parsed` and `sr_result` are threaded through both sizers but never read; `size_circuit`'s own docstring calls `sr_result` "unused directly" | — |
| 4 | `physics/device_model.py:11,45` | Docstrings place `compute_requirements` / `evaluate_metrics` in `circuitgenome.sizer.sizer`; they live in `physics.preprocess` / `physics.metrics` | — |
| 5 | `gmid/resistors.py:11` | Refers to `spice.deck._inject_sizes`; the module is `verify.deck` | — |
| 6 | `physics/gmid_lut.py:1`, `physics/device_model.py:8`, `gmid/geometry.py:3` | Docstrings describe the LUT path as "for PTM nodes"; gf180mcu and sky130 use it too | — |
| 7 | `examples/two_stage_se_specs/README.md` | Table lists only `generic` and `ptm45` (the folder also has gf180 and sky130 specs); explains ptm45 via Level-1 λ, but ptm45 now takes the gm/Id path | — |
| 8 | `docs/_extra/walkthrough/shared/` | "Moved" stub folder left from the `physics/` rename | — |
