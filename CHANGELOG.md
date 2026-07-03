# Changelog

All notable changes to the Topology Synthesizer are documented here, most
recent first.

## 2026-07-04 (demand-driven bias construction — typed leg library)

PR (`feat/demand-driven-bias-construction`). Redesigns bias generation from
enumerate-then-filter-then-prune to construct-from-consumer-demands (the
redesign issue #99 parked and issue #102 anticipated).

The three monolithic `bias_generation` variants delivered the *same* flavor
of voltage on all seven rails, so mixed-flavor consumer sets — notably every
real-cmfb fully-differential circuit, whose rail 4 is gnd-referenced while
rails 1/5 are vdd-referenced — could only enumerate with `resistor_bias`,
whose one-global-value sizing is the #100 bug. The flavor filter (#101)
pruned the structurally unbiasable pairings but could not give FD circuits a
correct generator.

### Changed

- **The bias generator is constructed, not enumerated.** `bias_generation`
  leaves the slot product; `build_circuit` derives a per-combination variant
  (`constructed_bias`) from a typed demand analysis of the other slots
  (`circuitgenome/synthesizer/bias_construction.py`): every consumed rail is
  classified as `gate_vdd`/`gate_gnd` (consumer gate with source on a
  supply → diode leg that is the mirror *master* of its consumers),
  `current_source`/`current_sink` (mirror tails' own reference diode → bare
  current leg, no bias-side diode to duplicate or fight it), or `tunable`
  (cascode gates / conflicting demands → resistor leg). Leg templates live
  in `config/bias_legs.yaml` (multi-reference core: NMOS master on `ibias`,
  plus a `pref` branch emitted only when a PMOS-referenced leg needs it).
- **Enumeration counts drop ~3x** (no bias factor, no filtered residue):
  1-stage 142→70, 2-stage SE 954→630, 2-stage FD 6 759→5 670, 3-stage SE
  7 290→5 670, 3-stage FD 491 427→459 270. Every mixed-flavor consumer set
  now gets structurally correct per-rail legs instead of routing to
  `resistor_bias`.
- **Recognizer**: new `constructed_bias` pattern + `constructed_bias_legs`
  hook (per-leg discovery: NMOS-referenced pairs, the `pref` branch,
  gnd-referenced/current/resistor legs off the PMOS-side reference). Purely
  NMOS-referenced shapes resolve to the historical
  `diode_connected_mosfet_bias` pattern; the three legacy monolith patterns
  remain for external netlists. The B1 mis-recognition
  (`resistor_bias` + `current_mirror_tail_nmos` → spurious
  `magic_battery_bias`) no longer affects synthesized circuits.
- **Sizer taxonomy**: `is_signal_device` now recognizes the constructed
  generator's internal `*_pref` mirror-reference gate as a bias net —
  without it, pref-gated legs were sized as signal devices (short L,
  ~5 % mirror error from channel-length modulation) instead of
  current sources (`sizer/shared/taxonomy.py`).
- **Designer verdicts (gf180 two-stage SE benchmark)**: unique core
  combinations reaching the metric gates rise 93 → 102. 18 mixed-flavor
  cores (`current_source_load_nmos` × `current_mirror_tail_pmos`) that
  previously only enumerated with mis-sized `resistor_bias` now bias; 9 are
  honestly re-condemned — 6 whose old pass was powered by the #100 rail-7
  corruption (a hot tail supplied the current their `common_drain` stage
  needed), and 3 knife-edge cascode-tail cores (~3 mV of Vdsat margin)
  tipped by the multi-reference core's extra mirror hop (~4 % cumulative
  λ error vs the retired 2-hop `magic_battery_bias`).

### Removed

- `synthesizer/bias_compatibility.py` (`is_bias_flavor_compatible` — flavor
  mismatches are now unconstructable; decision record: #102) and
  `synthesizer/bias_pruning.py` (`prune_bias_generation` — only consumed
  rails are built; `prune_redundant_tail_diode` — current legs carry no
  diode). The demand analysis (`required_rail_kinds`) subsumes
  `needed_bias_outputs`/`required_rail_flavors`.
- The `diode_connected_mosfet_bias`/`magic_battery_bias`/`resistor_bias`
  module variants from `opamp_modules.yaml` (their recognizer patterns
  remain).

### Follow-ups

- #100 narrows to sizing the `tunable` legs (cascode-consumer rails); a
  cascode-appropriate leg kind is the natural phase 2 of this design.

## 2026-06-24 (gm/Id pipeline redesign — PTM-only dispatch + bias-aware metrics)

PR (`feat/gmid-ptm-only-bias-gating`). Targets `feat/gmid-sizing-redesign`.

Running `size … --tech ptm45 --simulate` on a headroom-starved design (e.g.
`circuit_0110`, whose cascode tail cannot bias at 1.0 V) showed a huge
analytical-vs-SPICE gap on GBW / slew rate / power: the assumed currents never
flow, so the analytical metrics are optimistic. The sizer already flagged this
(`bias_feasible=False`) but reported the numbers as if valid.

### Changed

- **PTM techs are a gm/Id-only path.** A PTM/SPICE-model node *without* a gm/Id
  LUT (currently ptm32/22/16) now raises `UnsupportedTechError`
  (`sizer/models.py`) instead of silently falling through to the Level-1
  square-law sizer — the very numbers gm/Id exists to avoid. Only the card-less
  `generic` tech uses the analytical sizer. The `size` CLI and `tools/spice_verify.py`
  catch the error and exit cleanly. (LUTs for these nodes are tracked in #73.)
- **Feasibility-verdict reporting.** The `size` CLI now leads the metrics section
  with a mode-aware verdict instead of always printing a ✓/✗ table:
  - **INFEASIBLE** (`bias_feasible=False`, e.g. circuit_0110's cascode tail): the
    performance table is **suppressed** — its numbers are meaningless once the
    bias point collapses — and the bias reason is stated inline. Under
    `--simulate` the `analytical` column shows `n/a` and the `SPICE` column is the
    measured operating point.
  - **MARGINAL** (`bias_feasible=True` but a target is missed, e.g. the gm
    weak-inversion ceiling caps GBW): the **real** metrics are shown with ✗ on the
    failing rows.
  - **FEASIBLE**: the normal ✓ table.
- **SPICE-grounded feasibility verdict.** The analytical bias check only validates
  the input-pair tail, so it false-positives on circuits whose downstream stages
  don't bias (e.g. circuit_0010, whose output stage is current-mismatched 58 µA vs
  0.02 µA → rails to vdd). The `size` CLI now grounds the verdict in the reliable
  SPICE DC `.op` (`check_bias_soundness`, reusing `read_op_operating_point`): if the
  operating point rails or shows starved/triode devices, the circuit is reported
  **INFEASIBLE** instead of MARGINAL-with-optimistic-metrics. Automatic when ngspice
  is on PATH; the analytical verdict stands when it isn't. The core `size_circuit`
  stays analytical/fast — the SPICE check is CLI-level only.

Parity: feasible ptm45 designs, the `generic` Level-1 path, and the ✓/✗ table
itself are unchanged.

## 2026-06-24 (gm/Id pipeline redesign — phase 3: FD + three-stage + CMFB)

PR [#84](https://github.com/analog-ml/CircuitGenome/pull/84)
(`feat/gmid-fd-cmfb`). Stacked on #83; targets `feat/gmid-sizing-redesign`.
Closes #75.

### Added

The gm/Id pipeline already produced metrics for fully-differential and
three-stage op-amps (shared physics + the phase-2a cascode `rout`); this phase
closes the CMFB gap and adds coverage.

- **CMFB resistive-sense averager sized** (`gmid/resistors.py`): the `cmfb`-slot
  sense resistors `r1/r2` are sized to `intent.cmfb_sense_r` (~1 MΩ) instead of
  the 1 kΩ placeholder (which would short the differential output), and their
  loading `1/R_sense` is folded into the FD output resistance via a new optional
  `_evaluate_metrics(gd_out_extra=…)` (default 0 → SE / Level-1 unchanged). New
  `GmIdIntent.cmfb_sense_r`.
- **FD + three-stage gm/Id coverage** (`tests/test_fd_three_stage_gmid.py`):
  two-stage fully-differential with both `resistive_sense_cmfb` and `dda_cmfb`,
  and three-stage NMC/RNMC single-ended — all size via the gm/Id path
  (`status="GMID"`), set the three-stage inner cap `cc2_pf`, and size the CMFB
  resistors. **Closes #75** (FD / three-stage gm/Id support).

Parity: single-ended and non-CMFB circuits, and the Level-1 path, are unchanged.
At 1.0 V these (folded-cascode) FD/three-stage stacks remain headroom-tight and
flag `bias_feasible=False` honestly. Phase-3 completes the planned gm/Id redesign
(phases 1, 2a, 2b, 3).

### Fixed

The gm/Id path only sized rail-referenced `load` resistors; `resistor_bias`,
`resistor_tail`, and source-**degeneration** r1/r2 kept the 1 kΩ placeholder →
wrong bias and a wrong (un-degenerated) gm1.

- **`gmid/resistors.py::size_resistors`** sizes each by role:
  - **degeneration** `R = degeneration_factor / gm1` → reported `gm1` scaled by
    `1/(1+factor)` (a constant), applied to gain/GBW/PM/CMRR;
  - **`resistor_tail`** `R = |Vrail − V_tail| / ibias` (V_tail from the input-pair
    Vgs), with `gd_tail = 1/R` for a realistic CMRR;
  - **`resistor_bias`** legs `R_i = Vgs / ibias` (approximate — a sensible
    ~0.5–0.6 V rail instead of the placeholder ~10 mV).
- `_evaluate_metrics` gains optional `gm1_factor` / `gd_tail_override` (defaults →
  unchanged, Level-1 untouched); `gmid_sizer` merges the sized resistors into
  `SizingResult.resistors` (which `spice_sim._inject_sizes` already injects) and
  passes the metric factors.
- New `GmIdIntent.degeneration_factor` (default 0.5 = moderate).

Parity: circuits with no degeneration/resistor-tail/resistor-bias are unchanged.
Verified: a degenerated input pair drops gain by exactly `20·log10(1+factor)`;
full suite green.

### Fixed

The gm/Id path mis-modelled cascode loads: `_evaluate_metrics` took a single load
device's `gds` for `rout1`, ignoring the cascode `gm·ro·ro` boost (so every
folded-/telescopic-cascode load got a far-too-low gain).

- **Cascode-aware output resistance** (`gmid/blocks.py::node_rout`): traces each
  branch into the stage output node — a cascode device (source on another
  device's drain) contributes `ro·(1+gm·R_below)`, a plain device just `ro`;
  branches combine in parallel, with the input-pair tail treated as an AC ground.
- **Metrics override** (`_evaluate_metrics` gains optional `rout{1,2,3}_override`,
  default `None` → Level-1 path byte-identical); `gmid_sizer` computes the
  first-stage `rout` cascode-aware via the blocks and passes it in.
- **`CASCODE` sizing role** (`device_model`, `intent.cascode_gm_id`/`cascode_l_mult`):
  cascode devices are now sized in a smaller-Vdsat region (strong inversion) for
  headroom, instead of as plain current sources.

Parity: non-cascode circuits unchanged; the Level-1 path is untouched (no override
passed). Note: at the 1.0 V PTM supply most cascode stacks are headroom-tight and
flag `bias_feasible=False` (the gain is computed correctly but won't be reached
until the supply/CM allows the stack to bias). Full cascode-stack headroom in
`dc_op` (beyond the tail) and resistor sizing land in phase 2b.

### Changed

Phase 1 of separating the gm/Id sizer from the Level-1 analytical sizer into its
own **block-based pipeline** (`circuitgenome/sizer/gmid/`), so the two paths can
evolve independently and gm/Id can grow cascode / resistor / CMFB / FD support.

- **`gmid/gmid_sizer.py`** (`size_gmid`) — the gm/Id orchestration is lifted out
  of `size_circuit`, which now simply **dispatches** (`tech.gmid_lut` → gm/Id
  pipeline, else Level-1 CP-SAT). `size_circuit`'s `is_gmid` branch is gone; the
  Level-1 path is explicit (`Level1Model`). The **model-independent op-amp
  physics** (`_compute_requirements`/`_evaluate_metrics`) stays shared and
  model-injected — called by both pipelines, not duplicated.
- **`gmid/blocks.py`** — a functional-block view (input pair, load, gain stages,
  tail, bias, compensation) that **classifies** each load/tail (mirror / cascode /
  resistor / current-source). The structural layer and extension point for later
  phases.
- **`gmid/intent.py`** (`GmIdIntent`) — explicit per-role **inversion-region
  (gm/Id) and L** design choices (strong/moderate/weak), replacing the ad-hoc
  `GmIdPolicy` constants as the user-facing knob.
- **`gmid/dc_op.py`** — DC operating-point / headroom check that is now
  **cascode-aware**: a stacked tail's budget is the *sum* of its devices' Vdsat
  (a single-device check missed e.g. circuit_0110). Sets a new
  `SizingResult.bias_feasible` flag (and a cascode-collapse warning) so callers
  can tell when the assumed bias current won't actually flow.

Parity: identical gm/Id results on the existing tests (the new pipeline calls the
same physics with the same model); the Level-1 path is unchanged. Follow-up
phases: cascode + resistor blocks (phase 2), FD + three-stage + CMFB (phase 3).

## 2026-06-24 (honest --simulate reporting)

PR [#79](https://github.com/analog-ml/CircuitGenome/pull/79)
(`fix/spice-report-honest`). Stacked on #78.

### Fixed

- **`--simulate` no longer hides a non-amplifying circuit behind `n/a`.** A
  circuit that can't bias (e.g. a folded-cascode stage that doesn't fit the 1.0 V
  PTM headroom budget — input pair/cascodes in triode, bias legs starved) has a
  measured open-loop gain ≤ 0 dB. `_measure_ac` (`spice_sim.py`) previously
  discarded any result with `gain_db ≤ 0`, so gain/GBW/PM all printed `n/a`,
  looking like a measurement glitch.
  - `_measure_ac` now keeps the higher-gain input polarity and **reports the
    measured gain regardless of sign** (GBW/PM stay `n/a` when there is no 0-dB
    crossing), returning a `reason` string.
  - `simulate_metrics` adds a **bias diagnostic** when AC finds no gain — it
    reuses `read_op_operating_point` to name the devices in triode / starved
    (<0.1 µA) — and carries the AC reason + diagnostic out via a new `notes`
    list, printed beneath the table by `circuitgenome size --simulate` and
    `tools/spice_verify.py`.
  - So the reported command now shows e.g. `Open-loop gain −45.5 dB` plus
    "measured gain ≤ 0 dB — circuit does not amplify as biased" and
    "bias diagnostic — in triode: m1_input_pair, …; starved: mp1_bias_gen, …",
    instead of a bare `n/a`. Root-cause headroom feasibility stays tracked under
    #74 / #76.

## 2026-06-24 (gm/Id gain/GBW accuracy)

PR [#78](https://github.com/analog-ml/CircuitGenome/pull/78)
(`fix/gmid-bias-headroom`). Closes #76.

### Fixed

Diagnosed (op-point + AC ngspice probing) and closed the residual gm/Id
analytical-vs-SPICE gap. The gm/Id LUT itself was already accurate
(`GBW = gm1/(2πCc)` is exact in SPICE; active-load gain predicts to +0.5 dB);
the gap had two distinct causes, now addressed:

- **First-stage gain over-counted 2× for non-mirror loads (gain).** The sizer
  used the active-mirror formula `gm1·Rout1` for every first stage, but a
  resistor- or current-source-loaded differential pair tapped single-ended only
  delivers `gm1·Rout1/2` (and a Miller-loop transconductance of `gm1/2`). A new
  `_first_stage_gain_factor` (`sizer.py`) applies `k_fs = 1.0` for a
  current-mirror or fully-differential first stage and `0.5` for a single-ended
  non-mirror load, in both `_compute_requirements` and `_evaluate_metrics`
  (corrects the Level-1 path too).
- **Bias current collapses from unmodeled DC headroom (GBW).** At low supply the
  input pair lifts its source node toward the rail, leaving the tail current
  source below its `Vdsat` → triode → the assumed KCL current doesn't flow, so
  `gm1` (hence GBW) falls several×.
  - **A1 — analytical headroom budget** (`headroom.py`): estimates the tail's
    saturation headroom from the LUT `Vgs`/`Vdsat`, lowers the tail mirror
    group's `Vdsat` (raises its gm/Id, keeping mirror ratios) when that fits, and
    otherwise emits an honest warning. Runs in the gm/Id path after geometry.
  - **A2 — SPICE-in-the-loop op-point refinement** (`refine.py`,
    `circuitgenome size --refine`): runs one feedback-biased `.op`, reads the
    actual per-device current, and re-evaluates the metrics at that operating
    point (flagging any triode device). On the active-load ptm45 two-stage the
    refined GBW tracks SPICE to ~1% (was −79%). Reuses a new
    `spice_sim.read_op_operating_point` helper; single-ended, skips gracefully
    without ngspice.

## 2026-06-23 (gm/Id sizing for PTM)

PR [#72](https://github.com/analog-ml/CircuitGenome/pull/72)
(`feat/gmid-sizing`).

### Added

- **gm/Id-based sizing for PTM technologies** — a procedural sizing path that
  drives transistor geometry from a SPICE-characterized lookup table instead of
  the Level-1 square law. Selected per-tech: a tech carrying a gm/Id LUT (PTM
  nodes) uses it; the card-less `generic` tech keeps the unchanged Level-1
  CP-SAT sizer. On ptm45 two-stage this cuts the analytical-vs-SPICE
  gain-prediction error from ~41 dB to ~9.5 dB and ~triples the achieved SPICE
  gain; phase-margin prediction tracks SPICE to ~1°.
  - **`tools/extract_tech.py --gm-id`** — sweeps the BSIM4 card in ngspice
    (`.save @m1[gm/gds/cgg/id/vdsat]`), inverts onto a uniform gm/Id axis, and
    writes a committed `config/models/ptm45_gmid.npz` (10 lengths × 37 gm/Id
    points, both polarities) plus the `gmid_lut:` line in the tech YAML.
  - **`gmid_lut.py`** (`GmIdLut`) — bilinear `(gm/Id, L)` interpolation of
    `id_w`/`gm_gds`/`ft`/`vdsat`/`vgs`, the `gm_id_from_idw` inverse, and the
    `max_gm_id` weak-inversion ceiling.
  - **`device_model.py`** — a `DeviceModel` interface so the topology math in
    `_compute_requirements`/`_evaluate_metrics` stays single-source.
    `Level1Model` wraps `equations.*` byte-identically (generic path unchanged);
    `GmIdModel` reads the primitives from the LUT — most importantly
    `gds = gm/(gm/gds)` with the intrinsic-gain ratio a real function of `L`
    rather than a constant `λ`. Includes the geometry inversion and the
    `GmIdPolicy` L-selection policy.
  - **`gmid_geometry.py`** (`assign_geometry_gmid`) — replaces CP-SAT for the
    gm/Id path: geometry is computed, not searched. A deterministic forward pass
    — per-device geometry from the LUT, grid snap, matched-pair symmetry, and
    **exact** current-mirror ratios.
  - **`gmid_lut` tech field** (`TechParams`, `loader.py`, `tech_ptm45.yaml`) —
    points a tech at its committed LUT; `build_device_model` switches paths on
    its presence.
  - **Docs** — Sphinx API pages for the three new modules
    (`docs/api/sizer/{device_model,gmid_lut,gmid_geometry}.rst` + index toctree)
    and a "gm/Id model (PTM nodes)" subsection in
    `docs/theory/sizing_flow.rst`.
  - **Tests** — `tests/test_gmid_lut.py` (interpolation/inverse round-trip,
    `Level1Model`-equals-`equations.*` regression guard) and
    `tests/test_gmid_geometry.py` (grid alignment, matched pairs, exact mirror
    ratios, ceiling-clamp warning); `tests/test_sizer.py` asserts ptm45 routes
    through the `"GMID"` path.

### Notes

- Scope: LUT characterized for **ptm45** only (32/22/16 nm still fall back to
  Level-1); validated on **two-stage single-ended** and **one-stage**. The
  gm/Id path reports `solver_status = "GMID"` (no solver).
- Follow-ups tracked as issues #73 (LUTs for 32/22/16 nm), #74 (re-tune example
  specs now that the weak-inversion gm ceiling is enforced), #75 (FD /
  three-stage gm/Id coverage), #76 (residual gain/GBW gap & SPICE AC-rig
  convergence), #77 (configurable L-policy).

## 2026-06-23 (gm weak-inversion ceiling)

PR [#70](https://github.com/analog-ml/CircuitGenome/pull/70)
(`fix/gm-ceiling`). Closes #69.

### Fixed

- **Modelled gm is now capped at the weak-inversion ceiling** `gm ≤ Id/(n·φt)`
  (`equations.gm_ceiling`, ≈ 25·Id). The square-law model had no gm ceiling, so
  the sizer could hit a gm target by growing W/L until the device slid into weak
  inversion — promising a gm the device can't deliver and over-reporting gain/GBW
  (e.g. analytical 80 dB vs SPICE 15 dB).
  - `_evaluate_metrics` caps gm1/gm2/gm3 → reported gain/GBW now track SPICE far
    more closely.
  - `_compute_requirements` clamps each gm *requirement* to the ceiling and emits
    a warning when it binds (e.g. "input-pair gm requirement exceeds the
    weak-inversion ceiling at ibias/2 — increase ibias or relax GBW/gain").
  - **Behaviour change**: a spec that needs an impossible gm now yields a
    best-effort design with an honest negative margin + warning (was a silent
    optimistic pass; the impossible-gain test was updated accordingly).
  - Residual analytical-vs-SPICE gap is now dominated by the inherent
    reference↔output current/VDS mismatch (Level-1 limit; see #65/#67), not a
    missing gm bound.

## 2026-06-23 (enforce current-mirror ratios)

PR [#68](https://github.com/analog-ml/CircuitGenome/pull/68)
(`fix/enforce-mirror-ratios`). Closes #67. Stacked on #66.

### Fixed

- **Current-mirror ratios are now enforced** so the bias network produces the
  assumed currents. Previously a current-source device sized by the output-swing
  constraint became an arbitrary-ratio mirror of its reference (e.g.
  `mp1_second_stage` was a **142×** mirror of `mp5_bias_gen`, sourcing ~1 mA
  instead of 25 µA; the tail mirror starved the input pair to 0.6 µA), so SPICE
  bias currents were 8–43× off and the analytical metrics were far above SPICE.
  - `build_model` (`constraints.py`) now groups MOSFETs by (gate-net, type),
    treats the diode-connected member as the mirror reference, and constrains each
    output to matched length + `(W/L)_out = (I_out/I_ref)·(W/L)_ref`.
  - Effect: SPICE quiescent **power now matches** analytical (was +140%), and the
    bias currents drop from 8–43× errors to ~2× (the residual is the inherent
    reference↔output VDS/λ mismatch the Level-1 model can't capture — distinct
    from this ratio bug).

## 2026-06-22 (size resistor loads)

PR [#66](https://github.com/analog-ml/CircuitGenome/pull/66)
(`fix/size-resistor-loads`). Closes #64. Stacked on #60.

### Fixed

- **Resistor loads are now sized and modelled.** Previously the load resistors of
  `resistor_load_gnd`/`vdd` (and one-stage resistor loads) kept a hardcoded 1 kΩ
  placeholder, which the sizer ignored — so the analytical gain was wildly
  optimistic (the resistor was absent from `Rout1`) and the circuit didn't bias
  in SPICE (5 mV at the first-stage output → next stage off → `--simulate` showed
  `n/a`).
  - **Size**: `size_circuit` now chooses each load resistor so its DC drop biases
    the driven device on (`R = (Vth + 0.15 V) / (ibias/2)`), returned in the new
    `SizingResult.resistors` and printed by the `size` CLI.
  - **Model**: the load-resistor conductance is included in `Rout1` in both
    `_compute_requirements` and `_evaluate_metrics`, so gm requirements and the
    reported gain account for it.
  - **Verify**: `spice_sim` injects the sized R, so resistor-load circuits bias
    and `--simulate` reports real gain/GBW/PM instead of `n/a`.
  - Out of scope (kept at placeholder): source-degeneration, CMFB-sense, and bias
    resistors. Active-load circuits are unaffected.

## 2026-06-22 (SPICE verification)

PR [#60](https://github.com/analog-ml/CircuitGenome/pull/60)
(`feat/spice-verify`).

### Added

- **ngspice metric verification** (`circuitgenome/sizer/spice_sim.py`) —
  re-simulates a *sized* circuit with the **same technology** and reports the
  analytical `_evaluate_metrics` results next to SPICE-measured ones (gain, GBW,
  phase margin, slew rate, power, output swing). Surfaced via
  `circuitgenome size --simulate` and the standalone `tools/spice_verify.py`.

- **`spice_model` tech field** (`TechParams`, `loader.py`, the four
  `tech_ptm*.yaml`, `tools/extract_tech.py`) — points a tech config at its BSIM4
  card. When absent (e.g. `generic`) a Level-1 `.model` is synthesised from
  `mu_cox`/`vth`/`lam`, so every tech is simulatable.

### Notes

- Best-effort cross-check, not sign-off: gain/GBW/PM via an open-loop
  AC-coupled-feedback testbench (auto-detecting the inverting input), power from
  the DC op point, slew rate from a unity-gain step. Single-ended is the most
  robust; fully-differential AC metrics and any non-converging measurement are
  reported as `n/a`. The Level-1 (`generic`) run validates the formulas; the
  BSIM4 (PTM) run exposes the Level-1-vs-device gap (e.g. ~80 dB predicted vs
  ~20 dB simulated at 45 nm). Requires ngspice on `PATH`.

## 2026-06-22 (CLI W/L precision)

PR [#59](https://github.com/analog-ml/CircuitGenome/pull/59)
(`fix/cli-submicron-wl-precision`).

### Fixed

- **`size` CLI printed sub-micron W/L as `0µm`** — the transistor-sizing table
  formatted W and L with `%.0f` µm, which was fine for the ~0.25 µm `generic`
  config but collapsed the sub-micron geometries of the nm-node PTM configs
  (e.g. `L = 0.045 µm`) to `0µm`. Now formatted with three decimals
  (`%.3f` µm) so values like `W=0.100µm L=0.045µm` print correctly. Display-only
  change — the solved sizes in `SizingResult.transistors` were always correct.

## 2026-06-22 (PTM technologies)

PR [#58](https://github.com/analog-ml/CircuitGenome/pull/58)
(`feat/sizer-tech-ptm`).

### Added

- **PTM technology configs** (`circuitgenome/sizer/config/`) — `tech_ptm45`,
  `tech_ptm32`, `tech_ptm22`, and `tech_ptm16`, planar-bulk nodes from the ASU
  Predictive Technology Model. The Level-1 sizer parameters (`mu_cox`, `vth`,
  `lam`) are **extracted with ngspice** from the BSIM4 cards
  (`config/models/ptm_*nm_HP.pm`, vendored with attribution). `tech_ptm16` is a
  predictive planar extrapolation — real 16 nm silicon is FinFET.

- **`tools/extract_tech.py`** — reusable ngspice-driven extractor that fits the
  effective Level-1 `mu_cox` / `vth` / `lam` from a BSIM4 card via transfer and
  output DC sweeps and writes a `tech_*.yaml`.

- **Built-in tech-name resolution** — `load_tech()` (and `size --tech`) now
  accept a short config name (`ptm45`, `generic`, …) in addition to a file path.

- **Docs** — Overview "Technology configurations" table and CLI `--tech` notes
  listing the available configs and the planar-bulk/square-law scope.

- **Tests** — `tests/test_sizer.py` parametrizes a feasible two-stage sizing
  across all four PTM nodes.

- **Per-node example specs** — ready-to-run, feasible specs for every
  opamp type × technology node (generic + ptm45/32/22/16), grouped one directory
  per type under `examples/`: `one_stage_specs/`, `two_stage_se_specs/`,
  `two_stage_fd_specs/`, `three_stage_se_specs/`, `three_stage_fd_specs/` (each
  with a README index). 25 specs total, all verified to size OPTIMAL.

## 2026-06-22 (sizer docs)

PR [#57](https://github.com/analog-ml/CircuitGenome/pull/57)
(`docs/sizer-overview-scope`).

### Changed

- **Initial Sizer overview** (`docs/overview.rst`) — corrected the stale scope:
  the sizer no longer "targets two-stage Miller-compensated op-amps" but
  **supports all seven topology templates** (one-stage, two-stage single-ended
  and fully differential, and the four three-stage NMC/RNMC variants). Replaced
  the single-entry topology list with all seven names.

- **`SizingSpec` field table** — added the missing ``third_stage_current_ratio``
  row (``iDS_3 = ratio × ibias``, three-stage only, default 5.0).

- **Sizing-algorithm walkthrough** — scoped the step-by-step derivation as a
  two-stage illustration and pointed readers to the Sizing Flow theory page
  (`docs/theory/sizing_flow.rst`) for the complete all-topologies derivation.

## 2026-06-22 (docs restructure)

PR [#55](https://github.com/analog-ml/CircuitGenome/pull/55)
(`docs/restructure-papers-examples`).

### Changed

- **Reference PDFs moved** — `literatures/` → `docs/papers/`. The
  Constraint-Programmed Initial Sizing paper (the sizer reference) is now tracked
  alongside the three previously-committed papers.

- **ACST reference artifacts grouped** —
  `examples/functional_blocks.xml`, `examples/netlist.ckt`, and
  `examples/subcircuits.xml` moved under `examples/acst_results/`.

- **`docs/references.rst`** now points at ``docs/papers/`` instead of
  ``literatures/``.

- **`tests/test_sr_netlist.py`** resolves the test netlist at its new location
  (`examples/acst_results/netlist.ckt`).

### Notes

- The dated design docs under `docs/plans/` keep their original `examples/…`
  paths as historical records — they are not part of the Sphinx build.

## 2026-06-21 (three-stage)

PR [#54](https://github.com/analog-ml/CircuitGenome/pull/54)
(`feat/sizer-three-stage`).

### Added

- **Three-stage opamp sizing** — `size_circuit()` now supports all four
  three-stage topologies: `three_stage_opamp_nmc_single_ended`,
  `three_stage_opamp_rnmc_single_ended`,
  `three_stage_opamp_nmc_fully_differential`,
  `three_stage_opamp_rnmc_fully_differential`.
  The same conservative equations are applied to both NMC and RNMC.

- **`third_stage_current_ratio` field** (`SizingSpec`) — quiescent
  drain current for the third stage as a multiple of `ibias`
  (``ids_3 = ratio × ibias``; default 5.0).

- **`cc2_pf` field** (`SizingResult`) — inner Miller capacitor value in pF,
  set to ``cc_pf / 4`` for three-stage topologies, ``None`` otherwise
  (Eschauzier–Huijsing heuristic, no user-facing API parameter needed).

- **`_THIRD_STAGE_SLOTS` constant** (`circuitgenome/sizer/sizer.py`) —
  frozenset ``{"third_stage", "third_stage_p", "third_stage_n"}`` used at
  all dispatch sites (IDS assignment, gm requirement mapping, VDS_sat
  constraints, power accounting).

- **Phase-margin split** (`_compute_requirements`) — for three-stage, the
  allowed phase lag is split equally between the inner pole
  (``gm2/Cc2``) and the output pole (``gm3/CL``):
  ``gm2 ≥ gm1·Cc2/(Cc1·tan(θ))`` and ``gm3 ≥ gm1·CL/(Cc1·tan(θ))``
  where ``θ = (90° − PM_min) / 2``.

- **Three-stage gain formula** (`_compute_requirements`) —
  ``A0 = gm1·Rout1·gm2·Rout2·gm3·Rout3``; gm3 is solved after gm2 is
  determined from the inner-pole PM condition.

- **`phase_margin_three_stage_deg()`** (`circuitgenome/sizer/equations.py`) —
  evaluates the actual PM post-sizing:
  ``PM = 90° − arctan(ωt·Cc2/gm2) − arctan(ωt·CL/gm3)``.

- **Cross-slot symmetry for third stage** (`circuitgenome/sizer/constraints.py`) —
  CP-SAT constraints tie ``third_stage_p`` and ``third_stage_n`` to identical
  W and L, enforcing balanced differential outputs.

- **Twelve new integration tests** (`tests/test_sizer.py`) — covering NMC SE,
  RNMC SE, NMC FD, RNMC FD; assertions include Cc2/Cc1 ratio, all four
  performance specs, cross-slot symmetry for both second and third stages,
  power (tail + 2×ids_2 + 2×ids_3 for FD), plus PMOS-common-source metric
  reporting (`test_three_stage_pmos_cs_metrics_present`) and the
  topology-mismatch guard (`test_topology_mismatch_warns`).

- **Three-stage section in `docs/theory/sizing_flow.rst`** — block diagram,
  design variable table, PM derivation with LaTeX, numerical example, FD
  power formula, and operating-point mapping.

- **Example performance specs** (`examples/`) —
  `spec_three_stage_opamp_single_ended.yaml` and
  `spec_three_stage_opamp_fully_differential.yaml`, ready-to-run targets that
  demonstrate `third_stage_current_ratio` and the three-stage sizing flow.

- **`warnings` field** (`SizingResult`) — advisory messages surfaced by the
  `size` CLI with a `⚠` prefix; empty when the netlist cleanly matches the
  topology.

### Fixed

- **Metrics dropped for PMOS-common-source stages** — `_evaluate_metrics` read
  `gm2`/`gm3` only from the NMOS device, so any stage whose signal transistor is
  the PMOS (~1/3 of enumerated three-stage circuits, plus two-stage PMOS-CS)
  silently lost `gain_db`, `phase_margin_deg`, and `psrr_db`. It now reads
  `gm2`/`gm3` from the **signal transistor regardless of polarity** and uses the
  current-source load's `gd` for the PSRR estimate. The CP-SAT sizing was already
  correct; only the reported metrics were affected.

- **Silent topology mismatch** — sizing a netlist against the wrong `--topology`
  (e.g. a single-ended netlist as fully-differential) shoehorns bias devices into
  `*_p` stage slots and dropped the gain/PM/PSRR metrics with no explanation.
  `size_circuit()` now detects gain-stage slots that contain no signal transistor
  and reports a warning instead of failing silently.

### Notes

- Supported topologies: all seven (one-stage, SE/FD two-stage, four three-stage).
- NMC and RNMC share the same conservative PM formula; RNMC circuits are
  slightly over-designed at the sizing level (acceptable for initial sizing).
- ``cc2_pf`` is always ``None`` for one- and two-stage results —
  backward compatible.

## 2026-06-21

Issue [#51](https://github.com/analog-ml/CircuitGenome/issues/51), PR
[#52](https://github.com/analog-ml/CircuitGenome/pull/52)
(`feat/initial-sizer`).

### Added

- **`circuitgenome.sizer` module** — Layer 3 of the CircuitGenome pipeline:
  computes minimum transistor W/L values (on a technology grid) that meet a
  set of DC performance specifications.  Powered by an OR-Tools CP-SAT
  integer-programming solver.

- **`SizingSpec` dataclass** (`circuitgenome/sizer/models.py`) — performance
  specification: ``vdd``, ``vss``, ``ibias``, ``cl``,
  ``second_stage_current_ratio``, ``gain_min_db``, ``gbw_min_hz``,
  ``phase_margin_min_deg``, ``slew_rate_min_vps``, ``cmrr_min_db``,
  ``power_max_w``, ``output_swing_max_v``, ``output_swing_min_v``.

- **`TechParams` / `MosfetParams` / `GridSpec`** (`circuitgenome/sizer/models.py`) —
  technology description: per-type µCox, Vth, λ and discrete W/L/Cap grids.

- **`SizingResult`** (`circuitgenome/sizer/models.py`) — return type holding
  ``status`` (``"OPTIMAL"`` / ``"FEASIBLE"`` / ``"INFEASIBLE"``),
  ``sizes_um`` (dict ``ref → (W_µm, L_µm)``), ``cc_pf``, and computed
  performance metrics.

- **`size_circuit()`** (`circuitgenome/sizer/sizer.py`) — top-level entry
  point.  Derives the required gm1/gm2/Cc from the spec (in the order
  CMRR → SR → GBW → gain → PM) and delegates W/L solving to the CP-SAT model
  builder.

- **`build_model()`** (`circuitgenome/sizer/constraints.py`) — translates the
  ``gm ≥ gm_req`` and ``VDS_sat ≤ VDS_sat_max`` constraints into linear integer
  constraints (``2·µCox·IDS·W ≥ gm_req²·L``) with an integer-scaled coefficient
  scheme (scale = 10¹²).  Symmetry constraints enforce matched pairs within
  ``input_pair``, ``load``, and ``tail_current`` slots.

- **`load_tech()`** (`circuitgenome/sizer/loader.py`) — loads a technology
  YAML configuration from the built-in ``circuitgenome/sizer/config/`` directory.

- **`circuitgenome size` CLI subcommand** (`circuitgenome/cli.py`) — sizes a
  flat SPICE netlist given a topology name and a YAML spec file.

- **`examples/spec_two_stage_opamp.yaml`** — annotated example spec for a
  5 V / 10 µA two-stage Miller-compensated op-amp
  (GBW = 2.5 MHz, PM ≥ 60°, SR ≥ 3.5 V/µs, gain ≥ 80 dB).

- **`ortools>=9.8`** added to ``pyproject.toml`` dependencies.

### Notes

- Currently supported topologies: ``two_stage_opamp_single_ended``.
- CMRR, GBW, and SR specs share the same variables (``ibias``, ``Cc``,
  ``gm1``) and can be mutually exclusive for small bias currents; the solver
  returns ``INFEASIBLE`` in that case.  Specify at most two of the three, or
  relax ``ibias``.
- PyYAML parses bare positive scientific notation (``2.5e6``) as a string;
  use ``2.5e+6`` (explicit ``+``) in YAML spec files.

PR [#53](https://github.com/analog-ml/CircuitGenome/pull/53)
(`feat/sizer-fd-support`).

### Added

- **Fully-differential two-stage opamp sizing** — `size_circuit()` now
  handles `two_stage_opamp_fully_differential` in addition to the existing
  single-ended and one-stage topologies.

- **`_SECOND_STAGE_SLOTS` constant** (`circuitgenome/sizer/sizer.py`) —
  frozenset ``{"second_stage", "second_stage_p", "second_stage_n"}`` used at
  all four per-slot dispatch sites so FD slot names are treated identically
  to the SE ``second_stage`` slot for IDS assignment, gm requirement mapping,
  VDS_sat constraints, and metric evaluation.

- **Cross-slot symmetry constraint** (`circuitgenome/sizer/constraints.py`) —
  CP-SAT equality constraints tie ``second_stage_p`` and ``second_stage_n`` to
  identical W and L for each transistor type, enforcing balanced differential
  outputs.

- **FD power accounting** — ``_evaluate_metrics`` sums ``ids_2 × n_ss`` (where
  ``n_ss`` = number of active second-stage slots: 1 for SE, 2 for FD) so both
  output-stage current paths are reflected in the power estimate.

- **Five new integration tests** (`tests/test_sizer.py`) —
  ``test_size_fd_basic``, ``test_fd_specs_met``,
  ``test_fd_second_stage_symmetry``, ``test_fd_power_two_second_stages``,
  ``test_fd_cc_from_sr``.

### Notes

- Supported topologies now: ``one_stage_opamp``,
  ``two_stage_opamp_single_ended``, ``two_stage_opamp_fully_differential``.
- Physics (GBW, PM, SR, gain derivation order) is identical for SE and FD;
  FD simply runs two second-stage paths simultaneously.

## 2026-06-20

Issue [#45](https://github.com/analog-ml/CircuitGenome/issues/45), PR
[#46](https://github.com/analog-ml/CircuitGenome/pull/46)
(`feat/fbr-net-name-agnostic`).

### Added

- **`group_by_category(sr_result, netlist)`** — topology-free FBR mode that
  works on any netlist without a `TopologyTemplate`. Structures are grouped
  by `circuit_block` (outer) then `category` (inner); candidates within each
  category are ranked by external-port adjacency (count of pins whose net
  connects directly to a subcircuit external port).
- **`CategoryGroupResult`** dataclass — return type for `group_by_category`.
  Holds `groups: dict[str, dict[str, list[RecognizedStructure]]]` and
  `unrecognized_devices`.
- **`circuit_block` field** on `PatternDef` and `RecognizedStructure` —
  encodes position in the signal chain for opamp patterns. Pattern-level
  values: `gain_stage_1` (input_pair, load, tail_current patterns),
  `gain_stage_2` (second_stage patterns), `bias`, `compensation`, `cmfb`.
  The `gain_stage_N` prefix avoids collisions with existing `category` values
  such as `second_stage`. All 34 opamp MVP patterns annotated.
- **CLI topology-free mode**: `circuitgenome recognize <NETLIST>` without
  `--topology` now calls `group_by_category` and prints the grouped output
  (previously returned SR results only with no FBR grouping).

### Changed

- `--topology` is now documented as enhancing accuracy (named-slot assignment
  with connectivity scoring) rather than being required for any FBR output.
  `assign_slots` and its behavior are unchanged.

### Three-stage topology-free support

`group_by_category` now correctly splits a three-stage opamp's two gain stages
into separate `[gain_stage_2]` and `[gain_stage_3]` groups without a topology
template. The implementation uses a two-pass algorithm:

1. **Filter pass** — three classes of spurious `gain_stage_*` candidates are
   dropped:
   - **Class A** — `in` pin on an external port: input-pair nmos transistors
     (gate on `in1`/`in2`) or bias-reference nmos devices (gate on `ibias`)
     re-matched as gain stages.
   - **Class B** — `bias` pin on an external port: pmos leg of a bias mirror
     (gate on `ibias`) re-matched as a gain stage.
   - **Class C** — any nmos device in the candidate has source ≠ `gnd!`:
     cascode load devices (nmos cascode with source at an internal folding
     node) that survive the pin-level checks because their `in` and `bias`
     pins are on internal bias/cascode nets — not external ports. Applied
     only to single-category `gain_stage_*` blocks to avoid incorrectly
     filtering input-pair transistors (nmos source → tail-current net).

2. **Split pass** — single-category `gain_stage_*` blocks with more than one
   remaining candidate after filtering are split into consecutive `gain_stage_N`
   groups ordered by ascending external-port adjacency. The intermediate stage
   (`out` → internal net) stays at `gain_stage_2`; the final stage (`out` →
   external output port) is promoted to `gain_stage_3`.

## 2026-06-17 (3)

Issue [#36](https://github.com/analog-ml/CircuitGenome/issues/36), PR
(this branch: `feat/recognize-cli`).

### Added

- **`circuitgenome recognize <NETLIST>` CLI subcommand** — runs the full
  recognition pipeline (Layer 0 parse → SR → optionally FBR) on a flat SPICE
  netlist file and prints results to stdout:
  - Without `--topology`: prints recognized structures (name, category, devices)
    and any unrecognized devices (SR-only mode).
  - With `--topology NAME`: additionally runs FBR and prints per-slot
    assignments, flagging any unassigned topology slots or unassigned
    structures.
- **`tests/test_cli.py`** with three tests: SR-only output, SR+FBR slot
  assignment, and unknown-topology error handling.
- **`docs/usage/cli.rst`** updated with a "Recognizing circuits" section and
  options reference table for the new subcommand.

## 2026-06-17 (2)

Issue [#32](https://github.com/analog-ml/CircuitGenome/issues/32), PR
(this branch: `feat/sr-three-stage-coverage`).

### Added

- **Parametrized round-trip tests for all four 3-stage topologies** (40 new
  tests, 73 total), completing the MVP scope — all 7 topologies now round-trip
  from `synthesize()` through SR and FBR back to the original `variant_map`:
  - `three_stage_opamp_nmc_single_ended` and
    `three_stage_opamp_rnmc_single_ended`: 9 combos each, covering all 3
    `compensation` variants on `comp1`/`comp2`, all 3 `second_stage` variants
    on `second_stage`/`third_stage`, both input-pair polarities, and
    degenerated variants.
  - `three_stage_opamp_nmc_fully_differential` and
    `three_stage_opamp_rnmc_fully_differential`: 11 combos each, covering
    both `cmfb` variants, all 3 `compensation` variants across the 4 comp
    slots (`comp1_p/comp2_p/comp1_n/comp2_n`), all 3 `second_stage` variants
    across the 4 stage slots, cross-path asymmetry, and degenerated pairs.
- **No new SR patterns required**: the existing 34 patterns are sufficient.
  The `third_stage` slot reuses the `second_stage` pattern category; the 3-
  stage compensation slots reuse the 3 existing `compensation` patterns.
- **FBR handles >2 same-category slots correctly without code changes**:
  the `assigned_ids` mechanism from #31, combined with connectivity scoring
  on distinct per-slot nets, correctly disambiguates 4 `compensation` and
  4 `second_stage` slots in the fully-differential 3-stage topologies.

### Docs

- `docs/overview.rst`: "SR pattern coverage" section extended with four new
  bullets (one per 3-stage topology) explaining category reuse and
  disambiguation; test count updated 33 → 73; deferred-3-stage language
  removed.
- `README.md`: recognizer description updated to mention all seven topologies.

## 2026-06-17

Issue [#31](https://github.com/analog-ml/CircuitGenome/issues/31), PR
(this branch: `feat/sr-fully-diff-coverage`).

### Added

- **4 new SR patterns** for `two_stage_opamp_fully_differential`, bringing the
  total from 30 to 34:
  - *load* (2): `folded_cascode_load_nmos_input_differential_output` and
    `folded_cascode_load_pmos_input_differential_output` (8 devices each — 4
    PMOS + 4 NMOS folded-cascode structures with dual differential outputs
    ``out1``/``out2``). These are the only load variants that produce real CMFB
    instances.
  - *cmfb* (2): `resistive_sense_cmfb` (2 resistors + 5T OTA: resistive
    averager feeds a differential pair whose output current-mirrors onto
    ``out``), `dda_cmfb` (differential-difference amplifier: 4 NMOS + 2 PMOS
    mirror + 2 NMOS tails, two input pairs sharing a diode-connected load).
    Both expose ``{in1, in2, vref, bias, out}`` canonical pins.
- **FBR same-category disambiguation fix** (`assign_slots` line 93): when
  multiple topology slots share a category (e.g. `comp_p`/`comp_n` both in
  `compensation`, `second_stage_p`/`second_stage_n` both in `second_stage`),
  already-assigned SR candidates are now excluded from the pool when filling
  the second slot, preventing double-assignment on equal-score ties.
- **Parametrized round-trip test for `two_stage_opamp_fully_differential`**: 11
  combos in `tests/test_recognizer.py` covering both `cmfb` variants, all 3
  `compensation` variants (independently on `comp_p` and `comp_n`), all 3
  `second_stage` variants (independently on `second_stage_p` and
  `second_stage_n`), both input-pair polarities, and degenerated input pairs;
  each asserting `unrecognized_devices == []` and full `variant_map` recovery.

### Docs

- `docs/overview.rst`: SR pattern table updated — `load` count 10 → 12 with
  note about differential-output variants; new `cmfb` row (2 patterns); total
  count 30 → 34 across seven categories; "SR pattern coverage" section extended
  with `two_stage_opamp_fully_differential` bullet and test suite count 22 → 33.
- `README.md`: pattern count 30 → 34.

## 2026-06-16 (2)

Issue [#30](https://github.com/analog-ml/CircuitGenome/issues/30), PR
(this branch: `feat/sr-two-stage-coverage`).

### Added

- **6 new SR patterns** for `two_stage_opamp_single_ended`'s new slots,
  bringing the total from 24 to 30:
  - *compensation* (3): `miller_cap` (1 capacitor), `miller_cap_with_nulling_resistor`
    (series resistor + capacitor sharing an internal `cn` node),
    `indirect_compensation` (capacitor + series resistor sharing an internal `ind`
    node). Connectivity scoring disambiguates overlapping 1-device subsets without
    any hooks.
  - *second_stage* (3): `common_source` (NMOS input + PMOS load, drains shorted),
    `common_drain` (PMOS source-follower + NMOS tail, distinguished by `[mp1.d,
    mp1.b]` same_net constraint forcing the PMOS drain to vdd),
    `differential_ota_second_stage` (2 PMOS + 2 NMOS, cross-coupled via an internal
    `d1` node with 5 `same_net` groups).
- **Parametrized round-trip test for `two_stage_opamp_single_ended`**: 11 combos
  in `tests/test_recognizer.py` covering all 9 `compensation` × `second_stage`
  pairs and all 5 `input_pair` variants, each asserting `unrecognized_devices ==
  []` and full `variant_map` recovery.

### Docs

- `docs/overview.rst`: SR pattern table extended with `compensation` and
  `second_stage` rows; pattern count updated to 30; "SR pattern coverage" section
  updated to describe coverage of both topologies and the full 22-combo test suite.

## 2026-06-16

Issue [#29](https://github.com/analog-ml/CircuitGenome/issues/29), PR
(this branch: `feat/sr-pattern-coverage`).

### Added

- **Layer 0 resistor/capacitor parsing**: `netlist_parser.parse` now handles
  resistor lines (`r<ref> <t1> <t2> <value>`) and capacitor lines
  (`c<ref> <p> <m> <value>`) in addition to MOSFET lines.  Device type is
  inferred from the leading character of the ref.
- **24 SR patterns** (up from 4): every reachable `one_stage_opamp` variant
  is now covered by a composite pattern in
  `circuitgenome/recognizer/config/subcircuit_patterns.yaml`.
  - *input_pair* (5): `differential_pair_{nmos,pmos}`, degenerated variants
    (with source-degeneration resistors), `inverter_based_input`.
  - *load* (10): resistor (VDD/GND), active current mirror (PMOS/NMOS),
    current-source (PMOS/NMOS), single-output folded cascode (NMOS-input /
    PMOS-input), telescopic cascode (PMOS/NMOS).
  - *tail_current* (6): current mirror (PMOS/NMOS), cascode current mirror
    (PMOS/NMOS), resistor tail (VDD/GND).
  - *bias_generation* (3): `diode_connected_mosfet_bias`,
    `magic_battery_bias`, `resistor_bias`.
- **4 new hooks** in `circuitgenome/recognizer/hooks.py`:
  - `magic_battery_bias_legs`: discovers PMOS-reference + PMOS/NMOS leg pairs
    for `magic_battery_bias` (mirrors `diode_connected_mosfet_bias_legs` with
    polarities flipped).
  - `resistor_bias_legs`: discovers PMOS-reference + PMOS/resistor leg pairs
    for `resistor_bias`.
  - `resistor_tail_vdd_check` / `resistor_tail_gnd_check`: accept a
    single-resistor `tail_current` match only when the resistor's supply
    terminal is the global `vdd!` / `gnd!` rail, preventing spurious matches
    on degeneration and load resistors.
- **Incremental `same_net` checking** in `subcircuit_recognizer._find_assignments`:
  `_check_same_net` now handles partial assignments and is called after every
  tentative device binding (not just at the leaf), pruning invalid branches
  immediately (~1500× speedup on 8-device patterns with 10 `same_net` groups).
- **Parametrized round-trip test**: `tests/test_recognizer.py` is now
  parametrized over 11 representative `one_stage_opamp` combinations covering
  all 24 reachable variants, each asserting `unrecognized_devices == []` and
  full `variant_map` recovery.

### Changed

- `inverter_based_input` pattern moved to the top of
  `subcircuit_patterns.yaml` (was after the `differential_pair_*` patterns).
  Its NMOS pair alone satisfies `differential_pair_nmos`'s 2-device template
  (shared source/bulk = gnd), creating an equal connectivity score; file order
  now breaks that tie in favour of the more-specific 4-device pattern.

### Docs

- `docs/overview.rst` "Subcircuit & Functional Block Recognizer" section
  updated: netlist-parsing paragraph reflects resistor/capacitor support; the
  4-pattern table replaced by a category-level summary of all 24 patterns;
  hook description extended to cover all five hooks; "MVP scope" section
  replaced by "SR pattern coverage" describing the 11-combo parametrized test
  and the known structural ambiguities that guided combo selection.

## 2026-06-15 (2)

PR [#27](https://github.com/analog-ml/CircuitGenome/pull/27).

### Docs

- `opamp_modules.yaml`: header comment and the 3 `bias_generation` variant
  docstrings updated from the stale 4-rail description (`out1-out4`, `vdd`
  optional) to the current 7-rail/4-role model (`out1..out7`, `vdd`/`gnd`
  always present; `out1-4` -> `load`, `out5` -> `second_stage`, `out6` ->
  `third_stage`, `out7` -> `tail_current`).
- `docs/usage/python_api.rst` and `docs/usage/cli.rst`: corrected the stale
  `# 4050` circuit-count comment/sample output for
  `synthesize({"stages": 2, "output_type": "single_ended"})` to the verified
  **1890**; `cli.rst`'s `--stages 2 --dry-run` sample now also shows the
  `two_stage_opamp_fully_differential` line (17010), total 18900.
- `README.md`: regenerated the "Output format" flat and hierarchical SPICE
  examples from real `to_flat_spice`/`to_hierarchical_spice` output -- the
  previous examples predated the 7-rail refactor and used the orphaned
  `net_tail_bias` net and a polarity-invalid variant combination.
- `docs/extending.rst`: fixed the custom-topology example's `connections:`
  list -- `tail_current.bias` was wired to an undriven `net_tail_bias`, and
  `second_stage.bias`/`third_stage.bias` both shared `net_bias1` with
  `load.bias1`. Rewired to the dedicated-rail convention
  (`net_bias5`/`net_bias6`/`net_bias7`).

### Notes

- No source code (`.py`) changes -- documentation/config-comment fixes only,
  found via a full doc-vs-code audit.

## 2026-06-15

PR #25.

### Added

- `circuitgenome.__version__` attribute.
- `pyproject.toml` packaging metadata: `description`, `readme`, `license`,
  `authors`/`maintainers`, `keywords`, `classifiers`, and `[project.urls]`.

### Changed

- `[tool.setuptools.packages.find]` now uses `include = ["circuitgenome*"]`
  (previously `where = ["."]`), so the `tests` package is no longer bundled
  into the built distribution.

### Docs

- README and `docs/installation.rst`: document `pip install circuitgenome`
  (CircuitGenome is now published on PyPI), alongside the existing
  install-from-source instructions.

## 2026-06-14 (2)

PR #20.

### Changed

- Renamed `compatibility.py` to `polarity_compatibility.py` for naming
  consistency with its siblings (`output_compatibility.py`,
  `cmfb_compatibility.py`, `tail_current_compatibility.py`). Updated all
  imports/docstring/comment references (`synthesizer.py`,
  `tests/test_synthesizer.py`, `opamp_modules.yaml`, `README.md`,
  `docs/overview.rst`) -- no behavioral change.

### Docs

- `docs/index.rst`: split the "API Reference" toctree into "Core & I/O"
  (`synthesizer`, `models`, `loader`, `netlist`) and "Pipeline Filters &
  Pruning" (`polarity_compatibility`, `output_compatibility`,
  `cmfb_compatibility`, `tail_current_compatibility`, `bias_pruning`,
  `net_aliasing`, in pipeline order).
- `circuitgenome/synthesizer/CLAUDE.md`: split the file map into "Core
  pipeline & data model" and "Pipeline filters & pruning (internal, not in
  `__all__`)" sections, matching the new toctree grouping.

## 2026-06-14

PR #16.

### Added

- New module `net_aliasing.py`: `compute_alias_net_rename`/
  `apply_net_rename`, a net-merge pass run at the end of `enumerate_circuits`
  for `load` ports declared `alias_of` another `load` port.
- New nets: `net_loadout1`/`net_loadout2` (`fully_differential` topologies,
  the `load`'s cascode-output nodes) and `net_fold2` (`single_ended`/
  1-stage topologies, the `load`'s branch-2 folding node).

### Changed

- Fixed a gain-killing wiring defect: `load.in1`/`in2` (the folding nodes fed
  by `input_pair.out1`/`out2`) and `load.out`/`out1`/`out2` (the load's
  actual output node(s)) are now wired to *separate* nets in all 7
  topologies. Previously, the 6 cascode `load` variants' cascode-output
  devices had drain == source (degenerate, Vds=0), and `cmfb`/
  `second_stage*`/`comp*` sensed the low-impedance folding node instead of
  the cascode's high-impedance output.
  - `fully_differential` topologies: `load.out1`/`out2`, `cmfb.in1`/`in2`,
    `second_stage_p`/`_n.in`, and the corresponding `comp*_p`/`_n.in` now
    read `net_loadout1`/`net_loadout2` (previously `net_diff1`/`net_diff2`).
  - `single_ended`/1-stage topologies: `input_pair.out2`/`load.in2` now read
    `net_fold2` (previously the stage-output net); `load.out`/`out2`/
    `second_stage.in`/`comp*.in` are unchanged.
- For the 6 `load` variants whose `out1`/`out2` are declared `alias_of:
  in1`/`in2` (resistor/active/current-source loads), the new net-merge pass
  collapses `out1`/`out2`'s net back onto `in1`/`in2`'s, restoring their
  previous shared in/out connectivity unchanged.
- Circuit counts unchanged -- only net assignments/connectivity within
  existing combinations change.

### Docs

- `output_compatibility.py`, `models.py` (`output_cardinality` docstring),
  `circuitgenome/synthesizer/CLAUDE.md`, `docs/overview.rst`, `README.md`
  updated for the new distinct-in/out-nets + `alias_of`-merge model. Added
  `docs/api/net_aliasing.rst`.

### Notes

- No changes to `opamp_modules.yaml`, `loader.py`, `compatibility.py`,
  `cmfb_compatibility.py`, `bias_pruning.py`, or `netlist.py` -- the 6
  existing `alias_of: in1`/`in2` declarations on the resistor/active/
  current-source loads' `out1`/`out2` (previously cosmetic, consumed only by
  `netlist.py::_recover_port_nets`) become functionally load-bearing.

## 2026-06-13 (4)

PR #15.

### Added

- `cmfb_compatibility.py`: `is_cmfb_compatible(variant_map)` and
  `prune_cmfb(variant, load)`, called from `enumerate_circuits` right after
  `is_output_type_compatible`.

### Changed

- Of the 12 `load` variants, only the 2 tagged `output_cardinality:
  "differential"` (`folded_cascode_load_{nmos,pmos}_input_differential_output`)
  declare `bias_cmfb` as a real consumer; the other 10 declare it `optional`
  and never reference it, so `cmfb.out -> net_cmfb_out -> load.bias_cmfb` drove
  nothing for those loads. For such combinations, `is_cmfb_compatible` now
  restricts `cmfb` to a single canonical variant (`resistive_sense_cmfb`,
  avoiding duplicate-circuit enumeration of `dda_cmfb`), and `prune_cmfb`
  replaces it with an empty placeholder (`cmfb_absent`, no ports, no devices).
- Rail 4 (`net_bias4`/`cmfb.bias`) is "needed" only when `load`'s
  `output_cardinality` is `"differential"` -- reverting to the pre-CMFB
  condition (previously rail 4 was always needed for FD circuits).
- Per-topology circuit counts: `two_stage_opamp_fully_differential` 46,656 ->
  29,160 (120 x 3^5); `three_stage_opamp_{nmc,rnmc}_fully_differential`
  ~3,779,136 -> 2,361,960 each (120 x 3^9).

### Docs

- `docs/overview.rst`, `circuitgenome/synthesizer/CLAUDE.md`, `README.md`
  updated with the new filter, the 48+72=120 effective load/cmfb combination
  count, and corrected FD circuit counts. Added `docs/api/cmfb_compatibility.rst`.

### Notes

- For circuits where `cmfb` is pruned (~75% of FD circuits), the `vcm_ref`
  external port is declared in the SPICE subckt header but left
  unconnected -- the first "sometimes NC" external port in this codebase.
- Out of scope: `active_load_*`/`current_source_*` loads also have
  CM-undefined FD outputs and could genuinely benefit from CMFB, but giving
  them a real `bias_cmfb` consumer would require redesigning those variants'
  internals -- a separate, larger follow-up.
- Follow-up to Phase B (PR #14). No changes to `models.py`, `loader.py`,
  `compatibility.py`, `output_compatibility.py`, or `bias_pruning.py`.

## 2026-06-13 (3)

PR [#14](https://github.com/analog-ml/CircuitGenome/pull/14).

### Added

- New `cmfb` module category (2 variants): `resistive_sense_cmfb` (resistive
  averager + 5T OTA, 7 devices) and `dda_cmfb` (differential-difference
  amplifier, 8 devices). Canonical ports: `in1, in2, vref, bias, out, vdd,
  gnd`. Both untagged for `polarity`/`output_cardinality` (compatible with
  any combination).
- New `vcm_ref` external port on all 3 `fully_differential` topologies
  (`two_stage_opamp_fully_differential`,
  `three_stage_opamp_{nmc,rnmc}_fully_differential`), wired to `cmfb.vref`.

### Changed

- All 3 FD topologies gain a `cmfb` slot, wired:
  `cmfb.in1`/`in2 -> net_diff1`/`net_diff2` (the `load`'s first-stage
  differential outputs), `cmfb.vref -> vcm_ref`, `cmfb.bias -> net_bias4`
  (`bias_gen.out4`), `cmfb.out -> net_cmfb_out`.
- `load.bias_cmfb` repointed from `net_bias4` to `net_cmfb_out` (driven by
  `cmfb.out`), closing the common-mode feedback loop for
  differential-output cascode loads.
- Rail 4 (`bias_gen.out4`/`net_bias4`) is now always needed for FD circuits
  (via `cmfb.bias`), regardless of `load`'s `bias_cmfb` usage.
- Per-topology circuit counts: `two_stage_opamp_fully_differential` 23,328 ->
  46,656; `three_stage_opamp_{nmc,rnmc}_fully_differential` ~1,889,568 ->
  ~3,779,136 each (x2 for the 2 `cmfb` variants).

### Docs

- `docs/overview.rst`, `circuitgenome/synthesizer/CLAUDE.md`, `README.md`,
  and `docs/extending.rst` updated with the new `cmfb` category, `vcm_ref`
  external port, and updated FD circuit counts.

### Notes

- Phase B of a two-phase plan (Phase A: PR #13). No changes to `models.py`,
  `loader.py`, `compatibility.py`, `output_compatibility.py`, or
  `bias_pruning.py`.

## 2026-06-13 (2)

PR [#13](https://github.com/analog-ml/CircuitGenome/pull/13), commit `5ebcaae`.

### Added

- `output_cardinality: "single" | "differential" | None` tag on `load`
  variants (mirrors the existing `polarity` tag) and a new
  `output_compatibility.is_output_type_compatible(topology, variant_map)`
  filter, called from `enumerate_circuits` right after the existing polarity
  check.

### Changed

- Tagged the 6 cascode `load` variants whose mandatory output port's net
  assignment depends on the topology's `output_type`:
  `folded_cascode_load_{nmos,pmos}_input_single_output` and
  `telescopic_cascode_load_{pmos,nmos}` → `"single"` (mandatory `out`, wired
  only by `single_ended` topologies — otherwise floating);
  `folded_cascode_load_{nmos,pmos}_input_differential_output` →
  `"differential"` (mandatory `out1`/`out2`, kept distinct from `in1`/`in2`
  only by `fully_differential` topologies — otherwise the cascode device's
  drain shorts to its source). The other 6 `load` variants are untagged and
  remain compatible with either output type.
- Per-topology circuit counts updated to reflect the new filter:
  `one_stage_opamp`=360, `two_stage_opamp_single_ended`=3240,
  `two_stage_opamp_fully_differential`=23328,
  `three_stage_opamp_{nmc,rnmc}_single_ended`=29160 each,
  `three_stage_opamp_{nmc,rnmc}_fully_differential`=1,889,568 each.

### Docs

- `docs/overview.rst`, `circuitgenome/synthesizer/CLAUDE.md`, and
  `README.md` updated with the new filter, the 120/96
  polarity-and-cardinality-valid split, and the new circuit counts. Added
  `docs/api/output_compatibility.rst`.

### Notes

- Phase A of a two-phase plan. Phase B (a CMFB module sensing `outp`/`outn`
  and driving the differential-output cascode loads' `bias_cmfb`) is
  deferred to a follow-up PR.

## 2026-06-13

PR [#12](https://github.com/analog-ml/CircuitGenome/pull/12), commit `112cb81`.

### Changed

- Every `bias_generation` variant (`diode_connected_mosfet_bias`,
  `magic_battery_bias`, `resistor_bias`) extended from 4 to 7 independent legs
  (`out1`..`out7`, 15 devices: 1 shared reference + 7 legs of 2 devices each).
- Topology YAML statically wires `out5`/`out6`/`out7` to
  `second_stage*.bias`/`third_stage*.bias`/`tail_current.bias` (replacing the
  orphaned `net_tail_bias`), in addition to the existing
  `out1-4 -> load.bias1/bias2/bias3/bias_cmfb`. `load`, `second_stage`,
  `third_stage`, and `tail_current` now each get their own independent bias
  rail.
- `needed_bias_outputs`/`prune_bias_generation` generalized from a contiguous
  `out1..out_max` prefix over `{1..4}` to any subset of `{1..7}` (not
  necessarily contiguous, e.g. `{1, 5, 7}`). If all 7 rails are needed, the
  variant is returned unchanged.

### Removed

- The dynamic per-combination tail-rail assignment and overflow-leg cloning
  from PR #11 (`tail_current_needs_bias`, `assign_tail_bias_rail`,
  `extend_bias_generation`, `_extend_independent_legs`, and the
  `slot_connections` override in `enumerate_circuits`) — superseded by the
  static 7-rail base.

### Docs

- `docs/overview.rst` and `circuitgenome/synthesizer/CLAUDE.md` updated to
  describe the static 7-rail/4-role model and the simplified
  `enumerate_circuits` pipeline.

### Notes

- Circuit counts unchanged (432 / 3,888 / 34,992 ×2) — only per-circuit device
  internals and bias-rail assignments changed.

## 2026-06-12

PR [#11](https://github.com/analog-ml/CircuitGenome/pull/11), commit `ea2bd00`.

### Changed

- `diode_connected_mosfet_bias` and `resistor_bias` redesigned from a
  5-device "ladder" to the same "shared reference + 4 independent legs"
  shape as `magic_battery_bias`. Each leg now has its own complete current
  path. Both gained a `vdd` port.
- `bias_pruning.py` rewritten around this single shared structure; removed
  the old ladder-specific pruning code (`_is_ladder`, `_prune_ladder`).

### Added

- `tail_current` (current-mirror / cascode-current-mirror variants) now gets
  its own dedicated `net_bias{N}` rail (N = 1-5), fixing the previously
  unwired `net_tail_bias`. Resistor-tail variants are unaffected.
- New `bias_pruning` functions: `tail_current_needs_bias`,
  `assign_tail_bias_rail`, `extend_bias_generation` (adds a 5th leg/`out5`
  in the rail overflow case).

### Docs

- `docs/overview.rst` and `circuitgenome/synthesizer/CLAUDE.md` updated to
  describe the uniform leg structure and dedicated tail bias rail.

### Notes

- Circuit counts unchanged (432 / 3,888 / 34,992) — only per-circuit device
  internals changed.
