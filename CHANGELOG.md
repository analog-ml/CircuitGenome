# Changelog

All notable changes to the Topology Synthesizer are documented here, most
recent first.

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
