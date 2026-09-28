# One-stage op-amp sizing specs

Feasible per-node specs for `one_stage_opamp` (topology `one_stage_opamp`). A
single stage has **no Miller capacitor**: it is *load* compensated, so `CL`
takes the place of `Cc` and GBW / phase-margin / slew-rate are modelled from it
(issue #221). Gain targets are modest (one-stage gain ≈ gm1·Rout1). The specs
below predate that and still constrain DC gain, output swing and power only —
they remain valid, just no longer the full set the sizer can check.

| Spec | `--tech` | VDD | gain_min |
|------|----------|-----|----------|
| `spec_generic.yaml` | `generic` | 5.0 V | 40 dB |
| `spec_ptm45.yaml` | `ptm45` | 1.0 V | 40 dB |

```bash
circuitgenome size circuits/one_stage_opamp/circuit_0001_flat.ckt \
  --topology one_stage_opamp \
  --spec examples/one_stage_specs/spec_ptm45.yaml --tech ptm45
```
