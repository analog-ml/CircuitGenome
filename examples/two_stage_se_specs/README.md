# Per-technology-node sizing specs

Ready-to-run performance specs, one per built-in technology config, each tuned to
be **feasible** for a two-stage single-ended op-amp. Pair each with its node via
`--tech` (the sizer resolves the built-in config name):

| Spec | `--tech` | VDD | Notes |
|------|----------|-----|-------|
| `spec_generic.yaml` | `generic` | 5.0 V | ~0.25 µm illustrative defaults |
| `spec_ptm45.yaml` | `ptm45` | 1.0 V | PTM 45 nm HP bulk |

The PTM specs use lower supplies and more modest gain targets than the generic
spec because the extracted effective Level-1 parameters have higher channel-length
modulation (λ) at these short channels. See *Initial Sizer → Technology
configurations* in the Sphinx docs for details.

Example:

```bash
circuitgenome size circuits/two_stage_opamp_single_ended/circuit_0001_flat.ckt \
  --topology two_stage_opamp_single_ended \
  --spec examples/two_stage_se_specs/spec_ptm45.yaml \
  --tech ptm45
```
