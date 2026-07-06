# GF180MCU difficulty ladder

Five **feasible** sizing specs for the GlobalFoundries 180 nm (`gf180mcu`, 3.3 V
core) node, arranged from easy to stretch. Same fixed operating point
(`vdd 3.3 V`, `ibias 20 µA`, `cl 5 pF`) across all five — only the *performance
targets* tighten. Each rung was verified to still accept at least one
SPICE-checked design.

| # | Spec | Difficulty | gain | GBW | power | Binding constraint |
|---|------|-----------|------|-----|-------|--------------------|
| 1 | `spec_01_easy.yaml` | Easy | 50 dB | 1 MHz | 2.5 mW | (none — warm-up) |
| 2 | `spec_02_moderate.yaml` | Moderate | 65 dB | 2.5 MHz | 1.8 mW | DC gain |
| 3 | `spec_03_hard.yaml` | Hard | 92 dB | 5 MHz | 0.9 mW | high gain (needs cascode) |
| 4 | `spec_04_very_hard.yaml` | Very hard | 92 dB | 5 MHz | **0.6 mW** | gain **+ power** |
| 5 | `spec_05_stretch.yaml` | Stretch | 104 dB | 8 MHz | 0.65 mW | gain + GBW + power |

Specs 3 and 4 share the same 92 dB gain target and differ only in the power cap:
tightening it from 0.9 mW to 0.6 mW forces the **gain-vs-power tradeoff** —
power-hungry cascode stages that pass rung 3 are rejected at rung 4.

## Usage

```bash
circuitgenome design --topology two_stage_opamp_single_ended \
  --spec examples/gf180_difficulty_specs/spec_03_hard.yaml \
  --tech gf180mcu -o out/
```

## How difficulty was calibrated

Difficulty is the **acceptance rate** measured against the reference topology
`two_stage_opamp_single_ended` on `gf180mcu`, over the first 60 enumerated
circuits (`--limit 60`):

| Spec | Accepted (of 60) |
|------|------------------|
| `spec_01_easy` | 60 (100%) |
| `spec_02_moderate` | 42 (70%) |
| `spec_03_hard` | 27 (45%) |
| `spec_04_very_hard` | 9 (15%) |
| `spec_05_stretch` | 3 (5%) |

The reference frontier for this topology/node is ~121 dB gain, ~11.5 MHz GBW,
and ~0.30 mW minimum power, so rung 5 sits close to the achievable edge.

## Important caveat — difficulty is topology-relative

These specs use only the **universal** schema fields (no topology-specific
knobs), so the same file can be pointed at any topology via `--topology`. But
**the difficulty labels are calibrated to the two-stage single-ended reference
only.** The same spec accepts at a different rate on a different topology, and
*not* in a simple "more stages = easier" way. For example, `spec_03_hard`
(92 dB) accepts ~45% on `two_stage_opamp_single_ended` but only ~10% on
`three_stage_opamp_nmc_single_ended`: the 92 dB gain is trivial for a three-stage,
but its nested-Miller stability / GBW gates bind harder. A one-stage topology has
no Miller capacitor, so its GBW / phase-margin / slew-rate targets are not
modelled at all and are silently ignored.

Treat the difficulty column as "difficulty on the reference topology," and
re-measure if you retarget these specs at another topology.
