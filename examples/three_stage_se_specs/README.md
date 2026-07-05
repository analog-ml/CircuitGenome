# Three-stage single-ended op-amp sizing specs

Feasible per-node specs for the three-stage single-ended op-amp. Usage names the
**NMC** topology (`three_stage_opamp_nmc_single_ended`); the same spec also fits
the **RNMC** variant (`three_stage_opamp_rnmc_single_ended`) — the sizer treats
both schemes identically. Each spec sets `third_stage_current_ratio: 5.0`.

| Spec | `--tech` | VDD | gain_min |
|------|----------|-----|----------|
| `spec_generic.yaml` | `generic` | 5.0 V | 100 dB |
| `spec_ptm45.yaml` | `ptm45` | 1.0 V | 70 dB |

The PTM specs use a slightly larger output-swing headroom (0.15 V) than the
two-stage specs: at low VDD the three-stage output stage's VDS_sat budget is
tighter (higher λ → larger required devices). The three-stage circuits aren't
pre-shipped; generate via `circuitgenome synthesize --topology
three_stage_opamp_nmc_single_ended --output-dir circuits/`.

```bash
circuitgenome size circuits/three_stage_opamp_nmc_single_ended/circuit_0001_flat.ckt \
  --topology three_stage_opamp_nmc_single_ended \
  --spec examples/three_stage_se_specs/spec_ptm45.yaml --tech ptm45
```
