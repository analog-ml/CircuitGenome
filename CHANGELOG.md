# Changelog

All notable changes to the Topology Synthesizer are documented here, most
recent first.

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
