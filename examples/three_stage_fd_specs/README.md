# Three-stage fully-differential op-amp sizing specs

Feasible per-node specs for the three-stage fully-differential op-amp. Usage names
the **NMC** topology (`three_stage_opamp_nmc_fully_differential`); the same spec
also fits the **RNMC** variant. Each spec sets `third_stage_current_ratio: 5.0`.

| Spec | `--tech` | VDD | gain_min |
|------|----------|-----|----------|
| `spec_generic.yaml` | `generic` | 5.0 V | 100 dB |
| `spec_ptm45.yaml` | `ptm45` | 1.0 V | 70 dB |

Like the SE three-stage specs, these use 0.15 V output-swing headroom at the PTM
nodes. The generic spec also raises `power_max_w` to 2 mW because the
fully-differential design duplicates the second/third stages (≈2× quiescent
current). The circuits aren't pre-shipped; generate via `circuitgenome synthesize
--topology three_stage_opamp_nmc_fully_differential --output-dir circuits/`.

```bash
circuitgenome size circuits/three_stage_opamp_nmc_fully_differential/circuit_0001_flat.ckt \
  --topology three_stage_opamp_nmc_fully_differential \
  --spec examples/three_stage_fd_specs/spec_ptm45.yaml --tech ptm45
```
