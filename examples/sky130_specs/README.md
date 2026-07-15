# SKY130 specs

Specs for the SKY130 1.8 V core tech (`--tech sky130`).

| Spec | Scope | gain_min |
|------|-------|----------|
| `spec_all_templates.yaml` | every template (`design --all`) — survey spec | 45 dB |

The survey spec sits at the intersection all families can attempt (the
one-stage family caps the gain floor; the FD three-stage family sets the power
ceiling). For a single family, prefer a per-family spec — e.g.
`examples/two_stage_se_specs/spec_sky130.yaml` (60 dB) for the two-stage SE
benchmark.

```bash
circuitgenome design --all \
  --spec examples/sky130_specs/spec_all_templates.yaml \
  --output-dir out/sky130_all --tech sky130 --workers 4
```
