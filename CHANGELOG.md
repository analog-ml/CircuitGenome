# Changelog

All notable changes to CircuitGenome are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each entry is a one-line summary linking the pull request that made the change;
open the PR for the full root-cause / design detail. Emoji legend:
✨ feature · 🐛 fix · ♻️ refactor · ⚡ performance · 🔥 removal · 📝 docs ·
🧪 tests · 🔧 chore/build.

## [Unreleased]

### Added

- ✨ Recognizer parser accepts *sized* SPICE netlists — MOSFET `W/L/nf/m` params, `sky130_fd_pr__*`/foundry model names via a configurable model-name table, and preserved R/C value tokens; sizes ride along on `Device.params` and `recognize()` is unchanged ([#168](https://github.com/analog-ml/CircuitGenome/pull/168)).
- ✨ SKY130 1.8 V core PDK for the gm/Id sizer — trimmed vendored PDK, `device_handle`/`wl_units` tech fields, LUT monotone-envelope fix ([#159](https://github.com/analog-ml/CircuitGenome/pull/159)).

### Docs

- 📝 Restructure this changelog to the Keep a Changelog + SemVer format (one line per PR).
- 📝 Clarify the overview template table and buffered op-amps ([#154](https://github.com/analog-ml/CircuitGenome/pull/154)).

## [0.2.0] – 2026-07-08

The Sizer (Layer 3) and Designer (Layer 4): CP-SAT and gm/Id transistor sizing,
ngspice verification, the GF180MCU PDK, demand-driven bias construction, and the
railed-bias correctness campaign that took the GF180 benchmark from 0 accepted
cores to hundreds.

### Added

- ✨ Initial sizing module — Layer 3, OR-Tools CP-SAT W/L solver ([#52](https://github.com/analog-ml/CircuitGenome/pull/52)).
- ✨ Fully-differential two-stage op-amp sizing ([#53](https://github.com/analog-ml/CircuitGenome/pull/53)).
- ✨ Three-stage op-amp sizing — NMC + RNMC, SE + FD ([#54](https://github.com/analog-ml/CircuitGenome/pull/54)).
- ✨ PTM 45/32/22/16 nm technology configs, ngspice-extracted ([#58](https://github.com/analog-ml/CircuitGenome/pull/58)).
- ✨ ngspice metric verification — analytical vs SPICE via `--simulate` ([#60](https://github.com/analog-ml/CircuitGenome/pull/60)).
- ✨ gm/Id-based sizing for PTM technologies ([#72](https://github.com/analog-ml/CircuitGenome/pull/72)).
- ✨ gm/Id cascode-aware output resistance + `CASCODE` sizing role (phase 2a) ([#82](https://github.com/analog-ml/CircuitGenome/pull/82)).
- ✨ gm/Id sizing of degeneration / tail / bias resistors (phase 2b) ([#83](https://github.com/analog-ml/CircuitGenome/pull/83)).
- ✨ gm/Id CMFB sizing + FD/three-stage coverage (phase 3) ([#84](https://github.com/analog-ml/CircuitGenome/pull/84)).
- ✨ PTM gm/Id-only dispatch + bias-aware metric reporting ([#85](https://github.com/analog-ml/CircuitGenome/pull/85)).
- ✨ gm/Id redesign — block-based PTM pipeline + SPICE-grounded metrics ([#86](https://github.com/analog-ml/CircuitGenome/pull/86)).
- ✨ GF180MCU foundry PDK for the gm/Id sizer ([#87](https://github.com/analog-ml/CircuitGenome/pull/87)).
- ✨ Export the synthesizer public API from the package root ([#88](https://github.com/analog-ml/CircuitGenome/pull/88)).
- ✨ Expose recognizer and sizer public APIs from the package root ([#89](https://github.com/analog-ml/CircuitGenome/pull/89)).
- ✨ Structure design intent as a Spec → Block → Transistor hierarchy ([#92](https://github.com/analog-ml/CircuitGenome/pull/92)).
- ✨ Designer (Layer 4) — spec-driven synthesis + sizing + SPICE verification ([#95](https://github.com/analog-ml/CircuitGenome/pull/95)).
- ✨ SPICE measurement of CMRR, PSRR+, output swing and two-edge slew rate ([#97](https://github.com/analog-ml/CircuitGenome/pull/97)).
- ✨ Honest rejection diagnostics + SPICE measurement-gap fixes ([#98](https://github.com/analog-ml/CircuitGenome/pull/98)).
- ✨ Demand-driven bias construction from a typed leg library ([#103](https://github.com/analog-ml/CircuitGenome/pull/103)).
- ✨ Cascode leg kinds + cascoded `pref` branch (bias phase 2) ([#107](https://github.com/analog-ml/CircuitGenome/pull/107)).
- ✨ Deliberate current margin for knife-edge current-source loads ([#115](https://github.com/analog-ml/CircuitGenome/pull/115)).
- ✨ Stage-interface level compatibility filter + PMOS common-source second stage ([#118](https://github.com/analog-ml/CircuitGenome/pull/118)).
- ✨ Re-wire `common_drain` as a true follower + add the NMOS complement ([#119](https://github.com/analog-ml/CircuitGenome/pull/119)).
- ✨ Wide-swing telescopic loads + cascode-leg bias-anchor fix ([#133](https://github.com/analog-ml/CircuitGenome/pull/133)).
- ✨ `output_stage` category + buffered op-amp topologies (un-park #125 followers) ([#134](https://github.com/analog-ml/CircuitGenome/pull/134)).
- ✨ Re-add stacked-diode cascode tails behind `bias_infeasible`/`include_infeasible` for DSE ([#136](https://github.com/analog-ml/CircuitGenome/pull/136)).
- ✨ Non-inverting current-mirror stage to unlock the NMC templates ([#140](https://github.com/analog-ml/CircuitGenome/pull/140)).

### Changed

- ♻️ Separate gm/Id sizing into its own block-based pipeline (phase 1) ([#81](https://github.com/analog-ml/CircuitGenome/pull/81)).
- ♻️ Restructure the sizing workflow as 5 phases with typed hand-offs ([#94](https://github.com/analog-ml/CircuitGenome/pull/94)).
- ♻️ Rename `common_source`/`common_drain` to explicit polarity suffixes ([#135](https://github.com/analog-ml/CircuitGenome/pull/135)).
- ♻️ Group the compatibility filters into a subpackage + split docs ([#137](https://github.com/analog-ml/CircuitGenome/pull/137)).
- ♻️ Unify the buffered-topology naming convention ([#143](https://github.com/analog-ml/CircuitGenome/pull/143)).

### Fixed

- 🐛 CLI printed sub-micron W/L as `0µm` — now 3 decimals ([#59](https://github.com/analog-ml/CircuitGenome/pull/59)).
- 🐛 Size and model resistor loads (was a hardcoded 1 kΩ placeholder) ([#66](https://github.com/analog-ml/CircuitGenome/pull/66)).
- 🐛 Enforce current-mirror ratios so the bias network produces the assumed currents ([#68](https://github.com/analog-ml/CircuitGenome/pull/68)).
- 🐛 Cap modeled gm at the weak-inversion ceiling `gm ≤ Id/(n·φt)` ([#70](https://github.com/analog-ml/CircuitGenome/pull/70)).
- 🐛 Close the gm/Id gain/GBW analytical-vs-SPICE gap ([#78](https://github.com/analog-ml/CircuitGenome/pull/78)).
- 🐛 Honest `--simulate` reporting — measured gain + bias diagnostic instead of `n/a` ([#79](https://github.com/analog-ml/CircuitGenome/pull/79)).
- 🐛 Discard AC extractions with an implausible phase margin ([#96](https://github.com/analog-ml/CircuitGenome/pull/96)).
- 🐛 Resolve spurious bias infeasibility — rig ibias direction + bias-flavor pruning ([#101](https://github.com/analog-ml/CircuitGenome/pull/101)).
- 🐛 Size the `tunable` bias legs per consumer ([#104](https://github.com/analog-ml/CircuitGenome/pull/104)).
- 🐛 Plan cascode-load currents from KCL at the folding node ([#105](https://github.com/analog-ml/CircuitGenome/pull/105)).
- 🐛 Pick Cc from the stability floor + size compensation resistors ([#116](https://github.com/analog-ml/CircuitGenome/pull/116)).
- 🐛 Re-wire cascode tails as wide-swing mirrors on a new cascode-level rail ([#120](https://github.com/analog-ml/CircuitGenome/pull/120)).
- 🐛 Define the untapped differential branch for current-source loads ([#121](https://github.com/analog-ml/CircuitGenome/pull/121)).
- 🐛 Park `inverter_based_input` as unsupported pending a fixed-Vgs sizing path ([#123](https://github.com/analog-ml/CircuitGenome/pull/123)).
- 🐛 Park `differential_ota_second_stage` + compensation inversion-parity filter ([#127](https://github.com/analog-ml/CircuitGenome/pull/127)).
- 🐛 Stage-interface window check + gm/Id repair for cascode loads ([#128](https://github.com/analog-ml/CircuitGenome/pull/128)).
- 🐛 Park follower second stages + gate on the analytic gain estimate ([#130](https://github.com/analog-ml/CircuitGenome/pull/130)).
- 🐛 Fold grid steps into the CP-SAT coefficients; clean up the analytical path ([#131](https://github.com/analog-ml/CircuitGenome/pull/131)).
- 🐛 Swing-aware gm/Id floors + fixed-CM swing bench ([#132](https://github.com/analog-ml/CircuitGenome/pull/132)).
- 🐛 Make analytical CMRR cascode-aware for cascode tails ([#146](https://github.com/analog-ml/CircuitGenome/pull/146)).
- 🐛 Gate resistor-loaded gain when the inter-stage bias is invalid ([#149](https://github.com/analog-ml/CircuitGenome/pull/149)).

### Removed

- 🔥 Drop the PTM 32/22/16 nm nodes (no gm/Id LUT), keep ptm45 ([#138](https://github.com/analog-ml/CircuitGenome/pull/138)).

### Docs

- 📝 Restructure `docs/papers` + `examples/acst_results`; fix doc & test references ([#55](https://github.com/analog-ml/CircuitGenome/pull/55)).
- 📝 Slim the README, enrich references, add a Contributing section ([#56](https://github.com/analog-ml/CircuitGenome/pull/56)).
- 📝 Correct the Initial Sizer scope in the overview (all seven topologies) ([#57](https://github.com/analog-ml/CircuitGenome/pull/57)).
- 📝 gm/Id sizing-workflow theory page (roles vs functional blocks) ([#93](https://github.com/analog-ml/CircuitGenome/pull/93)).
- 📝 Show the number of generated circuits per template ([#139](https://github.com/analog-ml/CircuitGenome/pull/139)).
- 📝 Add GF180MCU difficulty-ladder sizing specs ([#142](https://github.com/analog-ml/CircuitGenome/pull/142)).
- 📝 Overhaul the Sphinx docs — Furo nav, per-module pages, theory, landing page ([#144](https://github.com/analog-ml/CircuitGenome/pull/144)).
- 📝 Simplify the README and point detail to the Sphinx docs ([#152](https://github.com/analog-ml/CircuitGenome/pull/152)).

### Internal / Build

- 🔧 Release 0.2.0 — complete package data + numpy dependency ([#153](https://github.com/analog-ml/CircuitGenome/pull/153)).
- 🧪 Include the cascode saturation margin in bias-level test expectations ([#117](https://github.com/analog-ml/CircuitGenome/pull/117)).
- 🔧 Track `uv.lock`; add an explanation guideline to CLAUDE.md ([#122](https://github.com/analog-ml/CircuitGenome/pull/122)).
- 🔧 Ignore `.mem/` and `.copilot/` directories ([#90](https://github.com/analog-ml/CircuitGenome/pull/90)).

## [0.1.0] – 2026-06-20

The Synthesizer (topology enumeration) and the Subcircuit / Functional Block
Recognizer (Phase 1 + Phase 2 MVP): all seven op-amp templates, the 7-rail bias
model, CMFB for fully-differential outputs, the SR/FBR round-trip pipeline, and
the `recognize` CLI.

### Added

- ✨ Analog circuit topology synthesizer — Phase 1 ([#1](https://github.com/analog-ml/CircuitGenome/pull/1)).
- ✨ Three-stage op-amp topologies — NMC and RNMC compensation ([#4](https://github.com/analog-ml/CircuitGenome/pull/4)).
- ✨ Polarity compatibility filter for input_pair/load/tail_current ([#8](https://github.com/analog-ml/CircuitGenome/pull/8)).
- ✨ `output_cardinality` tag + output-compatibility filter for cascode loads ([#13](https://github.com/analog-ml/CircuitGenome/pull/13)).
- ✨ CMFB module for fully-differential op-amps + `vcm_ref` port (Phase B) ([#14](https://github.com/analog-ml/CircuitGenome/pull/14)).
- ✨ Distinct load in/out nets merged via `alias_of` (`net_aliasing`) ([#16](https://github.com/analog-ml/CircuitGenome/pull/16)).
- ✨ Topology visualizer — Streamlit + pyvis explorer (Phase 1) ([#22](https://github.com/analog-ml/CircuitGenome/pull/22)).
- ✨ PyPI packaging metadata + `__version__` ([#25](https://github.com/analog-ml/CircuitGenome/pull/25)).
- ✨ Read the Docs build configuration ([#26](https://github.com/analog-ml/CircuitGenome/pull/26)).
- ✨ Subcircuit Recognizer + Functional Block Recognizer — Phase 2 MVP ([#37](https://github.com/analog-ml/CircuitGenome/pull/37)).
- ✨ SR pattern coverage for all `one_stage_opamp` variants (24 patterns) + R/C parsing ([#38](https://github.com/analog-ml/CircuitGenome/pull/38)).
- ✨ SR patterns for `two_stage_opamp_single_ended` (compensation + second_stage) ([#39](https://github.com/analog-ml/CircuitGenome/pull/39)).
- ✨ SR patterns + round-trip tests for `two_stage_opamp_fully_differential` (34 patterns) ([#40](https://github.com/analog-ml/CircuitGenome/pull/40)).
- ✨ Round-trip tests for all four three-stage topologies ([#41](https://github.com/analog-ml/CircuitGenome/pull/41)).
- ✨ `circuitgenome recognize` CLI subcommand ([#42](https://github.com/analog-ml/CircuitGenome/pull/42)).
- ✨ Multi-level SR recognition with primitive patterns ([#44](https://github.com/analog-ml/CircuitGenome/pull/44)).
- ✨ Topology-free FBR via `circuit_block` grouping ([#46](https://github.com/analog-ml/CircuitGenome/pull/46)).
- ✨ Show device refs in FBR CLI output ([#47](https://github.com/analog-ml/CircuitGenome/pull/47)).

### Changed

- ♻️ Redesign the load category — canonical interface; fix folded/telescopic cascode ([#5](https://github.com/analog-ml/CircuitGenome/pull/5)).
- ♻️ Split `tail_current` into PMOS/NMOS variant pairs ([#6](https://github.com/analog-ml/CircuitGenome/pull/6)).
- ♻️ `bias_generation`: ladder-based diode/resistor generators ([#7](https://github.com/analog-ml/CircuitGenome/pull/7)).
- ♻️ Prune unused `bias_generation` output rails per circuit ([#10](https://github.com/analog-ml/CircuitGenome/pull/10)).
- ♻️ Unify `bias_generation` leg structure + add the tail-current bias rail ([#11](https://github.com/analog-ml/CircuitGenome/pull/11)).
- ♻️ Static 7-rail `bias_generation` base with independent rails ([#12](https://github.com/analog-ml/CircuitGenome/pull/12)).
- ♻️ Gate the `cmfb` slot by load `output_cardinality` ([#15](https://github.com/analog-ml/CircuitGenome/pull/15)).
- ♻️ Rename `compatibility.py` → `polarity_compatibility.py` ([#20](https://github.com/analog-ml/CircuitGenome/pull/20)).

### Fixed

- 🐛 Swapped polarity/display_name for telescopic cascode loads ([#9](https://github.com/analog-ml/CircuitGenome/pull/9)).
- 🐛 Prune `tail_current`/rail-7 for `inverter_based_input` ([#19](https://github.com/analog-ml/CircuitGenome/pull/19)).
- 🐛 SPICE device-ref naming — suffix the slot name instead of prefixing it ([#24](https://github.com/analog-ml/CircuitGenome/pull/24)).
- 🐛 Correct `gain_stage_1` candidate ranking in topology-free FBR ([#49](https://github.com/analog-ml/CircuitGenome/pull/49)).

### Docs

- 📝 Add the README ([#2](https://github.com/analog-ml/CircuitGenome/pull/2)).
- 📝 Add Sphinx documentation (Alabaster theme) ([#3](https://github.com/analog-ml/CircuitGenome/pull/3)).
- 📝 Add the module gallery and embed `all.svg` in the module-categories docs ([#23](https://github.com/analog-ml/CircuitGenome/pull/23)).
- 📝 Fix stale bias-rail and circuit-count docs after the 7-rail refactor ([#27](https://github.com/analog-ml/CircuitGenome/pull/27)).
- 📝 SR Milestone 2 design doc — primitive patterns & multi-level composition ([#43](https://github.com/analog-ml/CircuitGenome/pull/43)).
- 📝 Remove MVP references and update recognizer coverage ([#50](https://github.com/analog-ml/CircuitGenome/pull/50)).

[Unreleased]: https://github.com/analog-ml/CircuitGenome/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/analog-ml/CircuitGenome/releases/tag/v0.2.0
[0.1.0]: https://github.com/analog-ml/CircuitGenome/releases/tag/v0.1.0
