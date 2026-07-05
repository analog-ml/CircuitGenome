# Two-stage fully-differential op-amp sizing specs

Feasible per-node specs for `two_stage_opamp_fully_differential` (folded-cascode
differential-output load + CMFB + per-path compensation and second stage). Same
field set as the two-stage SE specs.

| Spec | `--tech` | VDD | gain_min |
|------|----------|-----|----------|
| `spec_generic.yaml` | `generic` | 5.0 V | 80 dB |
| `spec_ptm45.yaml` | `ptm45` | 1.0 V | 60 dB |

The fully-differential circuits aren't pre-shipped under `circuits/`; generate
one first with `circuitgenome synthesize --topology
two_stage_opamp_fully_differential --output-dir circuits/`.

```bash
circuitgenome size circuits/two_stage_opamp_fully_differential/circuit_0001_flat.ckt \
  --topology two_stage_opamp_fully_differential \
  --spec examples/two_stage_fd_specs/spec_ptm45.yaml --tech ptm45
```
