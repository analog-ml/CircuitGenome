# SKY130 designer survey — topologies meeting spec, per template

Results of running the `circuitgenome design` synthesizer across **all 13 opamp
templates** on the SKY130 1.8 V core tech, against the shared survey spec
[`examples/sky130_specs/spec_all_templates.yaml`](../sky130_specs/spec_all_templates.yaml).

**Command** (per-template; see [Reproducing](#reproducing) for why not `--all`):

```bash
circuitgenome design --topology <TEMPLATE> \
  --spec examples/sky130_specs/spec_all_templates.yaml \
  --tech sky130 --output-dir out/<TEMPLATE> --workers 1
```

## Target spec (1.8 V survey)

| Parameter | Value |
|-----------|-------|
| Supply (vdd / vss) | 1.8 V / 0.0 V |
| Input bias current | 20 µA |
| Load capacitance | 5 pF |
| **Min DC gain** | **45 dB** |
| **Min GBW** | **2 MHz** |
| **Min phase margin** | **60°** |
| **Max power** | **2 mW** |

A candidate is **accepted** ("meets spec") only when it sizes, establishes a
usable DC bias point (analytical **and** SPICE `.op`), passes the analytic-gain
gate, and every constrained metric that ngspice measured clears its target.
Acceptance is SPICE-verified with ngspice by definition.

## Results — designs meeting spec, per template

| Template | Meet spec | Evaluated | Yield | Status |
|----------|----------:|----------:|------:|--------|
| `one_stage_opamp`                                  | 0   | 54     | 0 %   | exhaustive |
| `two_stage_opamp_single_ended`                     | 15  | 162    | 9 %   | exhaustive |
| `two_stage_opamp_fully_differential`               | 297 | 648    | 46 %  | exhaustive |
| `two_stage_opamp_buffered_single_ended`            | 4   | 324    | 1 %   | exhaustive |
| `two_stage_opamp_buffered_fully_differential`      | 351 | 2,592  | 14 %  | exhaustive |
| `three_stage_opamp_nmc_single_ended`               | 90  | 972    | 9 %   | exhaustive |
| `three_stage_opamp_rnmc_single_ended`              | 16  | 972    | 2 %   | exhaustive |
| `three_stage_opamp_nmc_buffered_single_ended`      | 25  | 1,944  | 1 %   | exhaustive |
| `three_stage_opamp_rnmc_buffered_single_ended`     | 12  | 1,944  | 1 %   | exhaustive |
| `three_stage_opamp_rnmc_fully_differential`        | 824 | 23,328 | 4 %   | exhaustive |
| `three_stage_opamp_nmc_fully_differential`         | ≥ 3,240 | 23,328 | — | **partial (~72 %)** |
| `three_stage_opamp_nmc_buffered_fully_differential`  | ≥ 2,725 | 93,312 | — | **partial (~19 %)** |
| `three_stage_opamp_rnmc_buffered_fully_differential` | ≥ 1,044 | 93,312 | — | **partial (~19 %)** |

**10 of 13 templates completed exhaustively.** The three `partial` rows are
lower bounds: those runs were stopped before covering their full candidate space
(see [Partial templates](#partial-templates)). Per-template summary artifacts
(spec, stats, best design points) for the SPICE-verified runs are in
[`reports/`](reports/).

### Observations

- **`one_stage_opamp` = 0** — the single-stage family cannot clear the 45 dB
  floor at SKY130's short-channel intrinsic gain (its natural ceiling is
  ~40 dB). Expected; the survey spec deliberately sits where multi-stage
  families can still pass.
- **Fully-differential yields are the highest where measured** (two-stage FD
  46 %, buffered two-stage FD 14 %, and the FD three-stage rows accept
  thousands of designs). FD biases comfortably at the 1.8 V rail here.
- **FD candidate spaces are far larger** than single-ended: the non-buffered
  three-stage FD templates enumerate **23,328** variants each and the buffered
  ones **93,312** each, versus < 2,000 for the single-ended templates.

## Partial templates

The three FD templates marked *partial* were stopped deliberately: exhaustive
coverage was not practical under the run conditions (see the `--workers 1`
caveat below). At ~23 s/candidate that is ~1.5 days for a 23k-variant template
and ~2–3 weeks for a 93k-variant one. Their counts here are **lower bounds**
(accepted designs written to disk before the run was stopped), not final totals.
To complete them, either resolve the `--workers > 1` deadlock (below) for a
4–8× speedup, or run them with `--limit N` for a meet-spec *rate* rather than an
exhaustive absolute count.

## Reproducing

Two caveats that shaped how this survey was run:

1. **Run against the working tree, not the installed package.** A stale,
   non-editable `circuitgenome` may shadow the repo; prefix runs with
   `PYTHONPATH=$PWD` (or `pip install -e .`) so the current source is used.
2. **`design --workers > 1` deadlocks on the FD three-stage / buffered-FD
   templates.** The `ProcessPoolExecutor` path hangs (workers block in `poll()`,
   no progress) on exactly the fully-differential templates that add a gain
   stage; single-ended templates and the plain two-stage FD are unaffected.
   Every template above was therefore run in-process with `--workers 1`, which
   is the primary reason the large FD templates are slow. This is a real bug
   worth a fix — running these templates fast requires a working multi-process
   path.

Because a single `design --all` run lets one hung template block all later ones,
templates were run **one process per template** so a stall never cascades.
