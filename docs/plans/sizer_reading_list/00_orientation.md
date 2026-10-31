# Stop 0 — Orientation: the big picture, no code

**Files**: [`docs/modules/sizer.rst`](../../modules/sizer.rst) (325 lines) ·
[`docs/_extra/walkthrough/sizer_explained.html`](../../_extra/walkthrough/sizer_explained.html) (512 lines) ·
the `size` command in [`circuitgenome/cli.py`](../../../circuitgenome/cli.py#L214) (lines 214–444, read only as a map) ·
the four `config/tech_*.yaml` files (36–66 lines each)
**Time**: 30 min
**After this stop you can**:
- name the four jobs inside `circuitgenome/sizer` and say which folder does each
- say which sizer each of the four technologies goes to, and why
- describe what `circuitgenome size` does before and after it calls the sizer

## Background for this stop

- The sizer is Layer 3 of the pipeline. It gets a netlist whose devices are
  already sorted into named slots, and chooses the numbers: W/L, R, C
  ([pipeline layers](00_background.md#pipeline-layers),
  [what sizing means](00_background.md#sizing-problem)).
- There are two ways to choose those numbers: a solver search on the
  [square law](00_background.md#square-law), or a forward pass through a
  measured [gm/Id table](00_background.md#gmid)
  ([two strategies](00_background.md#two-strategies)).
- Whatever the sizer predicts, ngspice is the referee
  ([analytical vs SPICE](00_background.md#analytical-vs-spice)).
- A *technology* is a factory recipe. Its YAML file says which models exist
  for it ([technology, PDK, corners](00_background.md#technology)).

One idea is new here.

**Dispatch on data.** The code does not ask "is this gf180?". It asks "what does
this tech's config file *carry*?". A tech with a lookup table goes one way. A
tech with only textbook numbers goes another. Analogy: a parcel sorter that reads
the label, not the box. Add a new tech with a LUT and it is routed correctly
without touching any code.

---

## 0.1 `docs/modules/sizer.rst` (325 lines)

**Job in one sentence.** The user-facing manual page for the sizer: what goes in,
which path runs, how results are checked in SPICE.

**Design knowledge it encodes.**
- The spec table ([`sizer.rst:44`](../../modules/sizer.rst?plain=1#L44)) is
  the [spec sheet](00_background.md#spec-metrics).
- "Analytical Sizer" ([`:128`](../../modules/sizer.rst?plain=1#L128)) is the
  [square law](00_background.md#square-law) plus a solver.
- "gm/Id Sizer" ([`:144`](../../modules/sizer.rst?plain=1#L144)) is the
  [gm/Id forward pass](00_background.md#gmid).
- "SPICE verification" ([`:209`](../../modules/sizer.rst?plain=1#L209)) is the
  [referee](00_background.md#analytical-vs-spice), and why its numbers win.

**Walkthrough.** Read it in this order:

1. **Overview** ([`:4`](../../modules/sizer.rst?plain=1#L4)). Two paths, chosen
   by technology. Keep this sentence in mind: *the tech file picks the path*.
2. **Performance specification** ([`:37`](../../modules/sizer.rst?plain=1#L37)).
   The spec is "operating point + targets". The operating point (supply,
   `ibias`, load cap, current ratios) is fixed. The targets are minimums or
   maximums. Stop 1 maps every field.
3. **Path selection** ([`:197`](../../modules/sizer.rst?plain=1#L197)). Three
   rules: LUT → gm/Id; `generic` → Level-1; SPICE model without LUT → error.
   Stop 2 reads the code that does this.
4. **SPICE verification** ([`:209`](../../modules/sizer.rst?plain=1#L209)).
   ngspice runs in two roles: a DC "does it bias?" check, and a per-metric
   testbench. For real technologies the CLI prints the **SPICE** numbers, not
   the sizer's. For `generic` it prints the sizer's numbers, and `--simulate`
   adds a SPICE column.
5. **Example output** ([`:260`](../../modules/sizer.rst?plain=1#L260)). A
   `generic` run of `circuit_0001`. Note the "Solver: OPTIMAL" line: only the
   Level-1 path has a solver.

Skim the single-stage phase-margin section
([`:102`](../../modules/sizer.rst?plain=1#L102)). It matters only for one-stage
amplifiers.

**Where the page is behind the code** (main @ `8571bf9`):

| Line | Page says | Code says |
|---|---|---|
| [`:8`](../../modules/sizer.rst?plain=1#L8) | returns "minimum" W/L | only the CP-SAT path minimises anything (gate area). The gm/Id path computes W in one pass; nothing is minimised |
| [`:10`](../../modules/sizer.rst?plain=1#L10) | "targets DC specs — gain, GBW, PM, slew, CMRR, power, swing" | eight metrics; PSRR is missing, and GBW/PM/slew are not DC |
| [`:44`](../../modules/sizer.rst?plain=1#L44) spec table | no `psrr_min_db` row | [`models.py:197`](../../../circuitgenome/sizer/models.py#L197) has it |
| [`:166`](../../modules/sizer.rst?plain=1#L166) tech table | generic, ptm45, gf180mcu | `tech_sky130.yaml` is also bundled and tested ([`test_loader.py:26`](../../../tests/test_loader.py#L26)) |
| [`:203`](../../modules/sizer.rst?plain=1#L203) | LUT present "(ptm45, GF180MCU)" | sky130 too |
| [`:221`](../../modules/sizer.rst?plain=1#L221) | the SPICE bias check is described under "PTM and foundry PDKs" | the CLI runs it for *every* tech when ngspice is installed, `generic` included ([`cli.py:243`](../../../circuitgenome/cli.py#L243)) |

**Running example.** None of the page's examples use `circuit_0010` on gf180.
Section 0.3 below shows that run.

**Test to read.** None. This is prose.

**Check yourself.**
1. Which of the two paths uses a solver, and what does the solver minimise?
2. For gf180mcu, which numbers does `circuitgenome size` print: the sizer's
   predictions or ngspice's measurements?

<details><summary>Answers</summary>

1. The Level-1 (analytical) path. It uses OR-Tools CP-SAT to find the smallest
   total gate area on the W/L grid that meets the gm requirements
   ([`sizer.rst:135`](../../modules/sizer.rst?plain=1#L135)).
2. ngspice's. A tech with a real device model (`spice_model` or `spice_lib`)
   prints SPICE-measured metrics only
   ([`cli.py:311`](../../../circuitgenome/cli.py#L311)).
</details>

---

## 0.2 `docs/_extra/walkthrough/sizer_explained.html` (512 lines)

**Job in one sentence.** A figure-rich tour of `sizer.py` (the dispatcher) that
treats it as a hospital triage desk.

**Design knowledge it encodes.**
- Why square-law numbers on a real short-channel device are "plausible but
  wrong" ([square law](00_background.md#square-law)).
- The dispatcher is the *Strategy pattern*: one door, several interchangeable
  sizers behind it. The same idea appears one level down as `DeviceModel`
  (`Level1Model` vs `GmIdModel`, [two strategies](00_background.md#two-strategies)).

**Walkthrough.** Read Parts 1, 3 and 4. Part 1 is the triage analogy. Part 3 is
the three routes, with a trace table. Part 4 is the refusal. Part 5 is the
Strategy-pattern discussion, worth one skim.

**Where the page is behind the code.** The page was last changed on 2026-07-22
(`eca98a6`). The code moved on:

| HTML | Page says | Code says |
|---|---|---|
| [`:284`](../../_extra/walkthrough/sizer_explained.html#L284) | `size_gmid(parsed, sr_result, fbr_result, topology, tech, spec)` | the call also passes `intent=intent_for_tech(tech.name)` ([`sizer.py:56`](../../../circuitgenome/sizer/sizer.py#L56)) |
| [`:315`](../../_extra/walkthrough/sizer_explained.html#L315), [`:353`](../../_extra/walkthrough/sizer_explained.html#L353) | traces `ptm130_lut` and `ptm130` | no such techs exist. The bundled ones are `generic`, `ptm45`, `gf180mcu`, `sky130` |
| [`:368`](../../_extra/walkthrough/sizer_explained.html#L368) | "a real 130 nm PTM node" | the only PTM node left is 45 nm. 32/22/16 nm were dropped (`a9be8fa`) |
| [`:270`](../../_extra/walkthrough/sizer_explained.html#L270) | `SizingResult` holds "the compensation cap" | it holds a `capacitors` dict, plus `resistors` and two advisory flags (PR #251) |
| [`:460`](../../_extra/walkthrough/sizer_explained.html#L460) | "reads exactly two fields of `tech`" | three: `gmid_lut`, `spice_model`, and `name` (for the intent lookup) |
| whole page | no mention of foundry PDKs | gf180mcu and sky130 have `spice_lib`, not `spice_model`. They take Route 1 because they have a LUT. A `spice_lib` tech *without* a LUT is not caught by Route 2 (see [Stop 2](02_dispatcher.md#exit-check)) |

**Running example.** With `circuit_0010` on gf180, the page's "first yes wins"
rule fires at question 1 (gf180 has a LUT). The `spice_model` question is never
asked.

**Test to read.** None.

**Check yourself.**
1. The page says the order of the two questions *is* the logic. What would go
   wrong if the `spice_model` check came first?

<details><summary>Answers</summary>

1. ptm45 has both a `spice_model` and a `gmid_lut`
   ([`tech_ptm45.yaml:11`](../../../circuitgenome/sizer/config/tech_ptm45.yaml#L11)).
   With the refusal first, ptm45 would raise `UnsupportedTechError` even though
   it has a perfectly good LUT.
</details>

---

## 0.3 What the CLI does around the sizer (`cli.py::_cmd_size`)

**Job in one sentence.** `circuitgenome size` runs Layers 0–2 on a netlist,
calls the sizer once, then asks ngspice to check and measure the result.

**Design knowledge it encodes.**
- Fix the [bias point](00_background.md#small-signal) first, then measure the
  small-signal metrics. If the DC point is broken, the metrics are not shown.
- [Corners](00_background.md#technology): size once at the nominal corner, then
  re-measure at every corner to see the worst case.

**Walkthrough.**

1. [`cli.py:218`](../../../circuitgenome/cli.py#L218) to `:227`: parse the
   netlist (Layer 0), recognise sub-circuits (Layer 1), assign slots for the
   named `--topology` (Layer 2). The sizer never re-does this.
2. [`cli.py:229`](../../../circuitgenome/cli.py#L229): `load_tech(args.tech_file)`.
   `--tech` can be a file path *or* a short name such as `gf180mcu`. No
   `--tech` means `generic`. Then [`:230`](../../../circuitgenome/cli.py#L230):
   `load_spec(args.spec_file)`. Both are Stop 1.
3. [`cli.py:233`](../../../circuitgenome/cli.py#L233): **the one call into the
   sizer**, `size_circuit(...)`. If the tech is refused, the
   `UnsupportedTechError` is caught at
   [`:235`](../../../circuitgenome/cli.py#L235) and the command exits with the
   message. This is Stop 2.
4. [`cli.py:243`](../../../circuitgenome/cli.py#L243) to `:249`: **SPICE DC
   check**. If the sizer produced transistors and thinks the bias is fine,
   `check_bias_soundness` runs a DC operating-point (`.op`) simulation. If the
   circuit rails or a device is starved or in triode, the CLI sets
   `result.bias_feasible = False` and appends the reason to `warnings`. The CLI
   *edits the sizer's result* here. It can only downgrade a verdict, never
   upgrade one. (No ngspice means the check is skipped.)
5. [`cli.py:251`](../../../circuitgenome/cli.py#L251) to `:270`: print the
   solver status, warnings, every transistor, capacitor and resistor.
6. [`cli.py:303`](../../../circuitgenome/cli.py#L303): choose how to show
   performance. There are three cases:
   - **bias infeasible** ([`:304`](../../../circuitgenome/cli.py#L304)): print
     INFEASIBLE and no metrics. They would be meaningless.
   - **real device model** (`spice_model` or `spice_lib`,
     [`:311`](../../../circuitgenome/cli.py#L311)): ngspice is required.
     `simulate_metrics` ([`:322`](../../../circuitgenome/cli.py#L322)) measures
     all nine metrics, and the CLI computes the margins itself. The sizer's own
     `result.metrics` are **not printed**.
   - **`generic`** ([`:382`](../../../circuitgenome/cli.py#L382)): print the
     sizer's predicted `result.metrics` and `result.margins`.
7. [`cli.py:360`](../../../circuitgenome/cli.py#L360) to `:362`: **corner
   sweep**, for `spice_lib` techs only. `simulate_metrics(..., corner=c)` is
   called again for every corner in `spice_lib.corners`, and printed as one
   table. ptm45 (a `spice_model` tech) has no corners and gets no sweep.
8. [`cli.py:406`](../../../circuitgenome/cli.py#L406) to `:444`: `--simulate`.
   On a real tech it only prints "redundant". On `generic` it prints
   analytical vs SPICE side by side, with the percentage gap.

The other caller of `size_circuit` is the end-to-end designer
([`designer.py:134`](../../../circuitgenome/designer/designer.py#L134)), used
by `circuitgenome design`.

**Running example.** `circuit_0010` on gf180 (run on 2026-10-31, `8571bf9`,
about 4 s in total):

```
$ PYTHONPATH=$PWD python -m circuitgenome.cli size \
    circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec examples/two_stage_se_specs/spec_gf180.yaml
Tech: gf180mcu_3v3
Solver: GMID                                   <- gm/Id path, no solver
  mp1_second_stage   W=105.210µm  L=1.120µm ...
  c1_compensation    C=1.25pF
Feasibility: FEASIBLE                          <- SPICE .op check passed
Performance metrics (ngspice / BSIM4):
  Open-loop gain   81.17 dB   [spec ≥ 60.00 dB]   margin +21.17 dB  ✓
  Phase margin     63.49 °    [spec ≥ 60.00 °]    margin +3.49 °  ✓
  ...
Corner verification (re-measured; sized at typical):
  metric      typical   ss     ff     sf     fs
  PM (deg)    63.49   63.78  63.16  62.17  64.75
```

Two things to notice. First, the CLI prints only the ngspice column; the
analytical 63.2° from the README table is never shown. Second, the CLI does
not show that `mp1_second_stage` (105.21 µm, wider than gf180's 100 µm single
device) is built from 2 fingers. `result.transistors` knows this; the print
line ([`cli.py:263`](../../../circuitgenome/cli.py#L263)) does not show
`fingers`.

**Test to read.** [`tests/test_cli.py::test_size_ptm45_metrics_from_spice`](../../../tests/test_cli.py#L137)
checks that a real-model tech prints the ngspice metrics table.

**Check yourself.**
1. The sizer says `bias_feasible=True`, but the CLI prints INFEASIBLE. How?
2. Why does gf180 get a corner table but ptm45 does not?

<details><summary>Answers</summary>

1. The SPICE `.op` check at [`cli.py:246`](../../../circuitgenome/cli.py#L246)
   found a railed or starved operating point and flipped `bias_feasible` to
   `False`. The sizer's own DC check is analytical and can miss things.
2. The sweep is gated on `tech.spice_lib`
   ([`cli.py:360`](../../../circuitgenome/cli.py#L360)). Only a foundry corner
   library has named corners. ptm45 is a single flat `.model` file.
</details>

---

## 0.4 The four jobs, and where each lives

The [README](README.md#the-big-picture-in-one-diagram) table, restated with
the folders you will read:

| Job | Folder / file | Analogy | Stop |
|---|---|---|---|
| **Contracts**: what goes in, what comes out | `models.py`, `loader.py`, `config/` | the order form and the delivery note | 1 |
| **Dispatcher**: pick a sizer | `sizer.py`, `__init__.py` | the parcel sorter | 2 |
| **Circuit math**: formulas, LUT, stage chain | `physics/` | the engineering handbook | 3 |
| **Decisions**: choose W/L | `gmid/` (real techs), `analytical/` (`generic`) | the two designers who use the handbook | 4, 5 |
| **Referee**: re-measure in ngspice | `verify/` | the test lab | 6 |

(The README counts the dispatcher with the contracts, which gives four jobs.)

Two rules keep the layers clean:

- `physics/` never decides a width. It answers "given these sizes, what is the
  gain?" and "given this gm, what width does the LUT say?".
- Neither sizer imports `verify/`. The CLI and the designer call it *after*
  sizing, through the package's public API
  ([`verify/__init__.py:15`](../../../circuitgenome/sizer/verify/__init__.py#L15)).

---

## Exit check

**Which path does each tech (generic, gf180mcu, sky130, ptm45) take, and why?**

<details><summary>Answer</summary>

The dispatcher looks at two keys in the tech YAML, in this order: `gmid_lut`,
then `spice_model` ([`sizer.py:54`](../../../circuitgenome/sizer/sizer.py#L54),
[`:62`](../../../circuitgenome/sizer/sizer.py#L62)). `spice_lib` does not affect
sizing; it only changes how the CLI verifies.

| Tech (`name:`) | `gmid_lut` | `spice_model` | `spice_lib` | `mu_cox`/`vth`/`lam` | Sizer | Verification in the CLI |
|---|---|---|---|---|---|---|
| `generic` (`generic_parameterized`) | — | — | — | yes ([`tech_generic.yaml:12`](../../../circuitgenome/sizer/config/tech_generic.yaml#L12)) | **Level-1 + CP-SAT** (`size_level1`) | sizer's metrics printed. ngspice uses a Level-1 model built from `mu_cox`/`vth`/`lam`: the DC check always runs (if ngspice is installed), the metric benches only under `--simulate` |
| `gf180mcu` (`gf180mcu_3v3`) | [`models/gf180mcu_gmid.npz`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L23) | — | [`sm141064.ngspice`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L12), corners typical/ss/ff/sf/fs | none | **gm/Id** (`size_gmid`) | ngspice metrics + 5-corner table |
| `sky130` (`sky130_1v8`) | [`models/sky130_gmid.npz`](../../../circuitgenome/sizer/config/tech_sky130.yaml#L30) | — | [`sky130.lib.spice`](../../../circuitgenome/sizer/config/tech_sky130.yaml#L14), corners tt/ss/ff/sf/fs | none | **gm/Id** | ngspice metrics + 5-corner table |
| `ptm45` (`ptm45_hp`) | [`models/ptm45_gmid.npz`](../../../circuitgenome/sizer/config/tech_ptm45.yaml#L12) | [`models/ptm_45nm_HP.pm`](../../../circuitgenome/sizer/config/tech_ptm45.yaml#L11) | — | yes (extracted fits, [`:21`](../../../circuitgenome/sizer/config/tech_ptm45.yaml#L21)) | **gm/Id** (the LUT check comes first) | ngspice metrics, nominal only (no corners) |

**Why**:

- **generic** is a made-up textbook process. It has no real device model, so
  the square law is exactly right *for it by definition*. Its Level-1 numbers
  are only a qualitative check.
- **gf180mcu and sky130** are real foundry kits. The square law is badly wrong
  for real short-channel devices, so their YAMLs carry a LUT measured from the
  foundry BSIM4 model and *no* square-law parameters at all (issue #158). The
  loader enforces this split: a tech without a LUT must have
  `mu_cox`/`vth`/`lam`
  ([`loader.py:136`](../../../circuitgenome/sizer/loader.py#L136)).
- **ptm45** carries everything: a LUT, a BSIM4 card, and old square-law fits.
  The LUT wins because it is checked first. Its `mu_cox`/`vth`/`lam` are no
  longer read during sizing (only `Level1Model` reads them, and ptm45 never
  gets one).

Verified on `8571bf9` by loading each tech and calling `size_circuit` on
`circuit_0010`: `generic` → `solver_status="OPTIMAL"`; gf180mcu → `"GMID"`.
</details>

## Simplification notes

| Location | Observation |
|---|---|
| `docs/modules/sizer.rst:8, :10, :44, :166, :203, :221` | Stale against the code: "minimum" W/L, seven metrics (no PSRR), no `psrr_min_db` row, no sky130 in the tech table or path-selection list, SPICE bias check described as PTM/PDK-only (table in 0.1) |
| `docs/_extra/walkthrough/sizer_explained.html` | Not updated since `eca98a6` (2026-07-22): fictional `ptm130*` techs, no `intent=` argument, no `spice_lib` PDKs, "the compensation cap" (table in 0.2) |
| `cli.py:263` | The transistor print line drops `TransistorSizing.fingers`, so a 105 µm device on a 100 µm-max PDK looks like one illegal device |
| `cli.py:309` | The INFEASIBLE branch says "run --simulate to measure the actual operating point", but `--simulate` is "redundant" on `spice_model`/`spice_lib` techs ([`:406`](../../../circuitgenome/cli.py#L406)) |
| `cli.py:272` comment | "For PTM the numbers come from ngspice": now true for every `spice_model` *or* `spice_lib` tech |
| `cli.py:339` vs `physics/metrics.py:216` | The CLI recomputes margins for SPICE metrics with its own min/max table (`_SPEC_KEYS` + `is_min`), duplicating the sign rule in `evaluate_metrics._record` |
| `config/tech_ptm45.yaml:21-33` | ptm45 still carries Level-1 `mu_cox`/`vth`/`lam` fits that the gm/Id path never reads (#158 removed them from gf180/sky130 only) |
| `verify/__init__.py:6-9` docstring | Describes only `generic` and "PTM nodes"; foundry `spice_lib` PDKs are not mentioned (Stop 6) |
