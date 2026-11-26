# Stop 7 — Run it end to end

**Files**: [`circuitgenome/cli.py`](../../../circuitgenome/cli.py) (533 lines, `_cmd_size` only),
[`examples/two_stage_se_specs/`](../../../examples/two_stage_se_specs/) (four spec YAMLs, 13–30 lines),
[`tests/test_sizer.py`](../../../tests/test_sizer.py) (1838 lines, run, not read),
[`tests/test_cli.py`](../../../tests/test_cli.py) (179 lines)
· **Time**: 1 h, about 5 min of it waiting for commands
· **After this stop you can**:
- run the whole flow yourself: generate netlists, size one, read every line the CLI prints;
- say why `circuit_0001` misses the gain spec and `circuit_0010` doesn't, with numbers;
- compare the sizer's own estimate with ngspice and say which gaps are real and which are "different ruler".

Everything below was run on 2026-10-31 on `main` @ `8571bf9`, from the repo root,
on a 32-core Linux machine with ngspice on `PATH`. Outputs are copied from the
terminal and trimmed only where marked `...`. Times are wall-clock (`real`).

---

## Background for this stop

You need almost every idea from [the background](00_background.md), but these
carry the stop:

- [gm](00_background.md#gm) and [Rout](00_background.md#rout): a stage's gain is
  `gm · Rout`. Two stages multiply ([stages](00_background.md#stages)).
- [Loads](00_background.md#loads) and the [current mirror](00_background.md#current-mirror):
  what sits on top of the [differential pair](00_background.md#diff-pair) sets `Rout1`.
- [Headroom](00_background.md#headroom) and [Vdsat](00_background.md#vdsat):
  the voltage budget between the [rails](00_background.md#rails).
- [Inversion levels](00_background.md#inversion) and the [gm/Id LUT](00_background.md#gmid):
  a transistor cannot give more than about 25–30 µS of gm per µA of current, however wide it is.
- [Technology, corners](00_background.md#technology), and
  [analytical vs SPICE](00_background.md#analytical-vs-spice).
- The [spec sheet](00_background.md#spec-metrics) and its eight metrics.

**One idea not in the background: the "variant".** The synthesizer reads a
*topology template* (here `two_stage_opamp_single_ended` in
[`opamp_topologies.yaml:82`](../../../circuitgenome/synthesizer/config/opamp_topologies.yaml#L82)).
The template is a list of *slots* (`input_pair`, `load`, `tail_current`,
`bias_gen`, `compensation`, `second_stage`) and the wires between them. Each
slot can be filled by several building blocks: a PMOS or NMOS input pair, a
resistor / current-mirror / cascode load, a plain Miller cap or a cap plus a
nulling resistor, and so on. One *variant* is one choice per slot, written out
as a flat netlist. **Analogy**: a sandwich menu. The template says "bread +
filling + sauce"; each variant is one sandwich you can actually order.

---

## 7.1 Generate the netlists

**Job in one sentence.** Turn the template into every valid combination, one
`.ckt` file each.

```console
$ time PYTHONPATH=$PWD circuitgenome synthesize --topology two_stage_opamp_single_ended --output-dir circuits/two_stage_se

Topology: two_stage_opamp_single_ended
  Generated 162 circuits

Total: 162 circuits written to circuits/two_stage_se/

real	0m3,989s
```

162 files, one per variant, about 4 s. `circuits/` is git-ignored. Re-running
overwrites the same files with the same content. Without `--output-dir` the
files land in the current directory (the default is `.`,
[`cli.py:27`](../../../circuitgenome/cli.py#L27)).

**What varies.** A quick count over the 162 files: 90 have a PMOS input pair and
72 an NMOS one; 36 have a resistor load. Neighbouring numbers often differ in
only one slot. For example `circuit_0010` and `circuit_0011` differ only in the
compensation slot (`0011` adds a 1 kΩ nulling resistor in series with the cap,
the [RHP-zero fix](00_background.md#rhp-zero)).

**The running pair.** `diff` of the two circuits this reading list follows:

```console
$ diff circuits/two_stage_se/circuit_0001_flat.ckt circuits/two_stage_se/circuit_0010_flat.ckt
1c1
< .subckt circuit_0001 ibias in1 in2 out vdd! gnd!
---
> .subckt circuit_0010 ibias in1 in2 out vdd! gnd!
4,5c4,5
< r1_load net_diff1 gnd! 1k
< r2_load net_mid gnd! 1k
---
> m1_load net_diff1 net_diff1 gnd! gnd! nmos
> m2_load net_mid net_diff1 gnd! gnd! nmos
```

Everything else is identical: the same PMOS pair, PMOS tail mirror, bias
generator, Miller cap `c1_compensation` and NMOS common-source second stage.
Only the **load** slot differs:

- `circuit_0010`: an NMOS [current mirror](00_background.md#current-mirror).
  `m1_load` is the diode (its gate is tied to its drain); `m2_load` copies its
  current.
- `circuit_0001`: two resistors to ground. The `1k` in the file is a placeholder;
  the sizer replaces it.

The `1p` cap and the missing `W`/`L` on the transistors are placeholders too.
Choosing those numbers is the sizer's whole job
([what sizing means](00_background.md#sizing-problem)).

---

## 7.2 Size the running example from the CLI

**Job in one sentence.** `circuitgenome size` parses the netlist, recognises its
blocks, sizes it, re-measures it in ngspice, and prints the result.

```console
$ time PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec examples/two_stage_se_specs/spec_gf180.yaml
Netlist: circuit_0010_flat.ckt  |  Topology: two_stage_opamp_single_ended
Tech: gf180mcu_3v3

Solver: GMID

Transistor sizing:
  m1_input_pair                   W=3.070µm  L=0.560µm  IDS=10.00µA  VGS=-1.083V  VDS_sat=0.274V
  m2_input_pair                   W=3.070µm  L=0.560µm  IDS=10.00µA  VGS=-1.083V  VDS_sat=0.274V
  m1_load                         W=2.495µm  L=0.560µm  IDS=10.00µA  VGS=0.841V  VDS_sat=0.168V
  m2_load                         W=2.495µm  L=0.560µm  IDS=10.00µA  VGS=0.841V  VDS_sat=0.168V
  m1_tail_current                 W=42.085µm  L=1.120µm  IDS=20.00µA  VGS=-0.942V  VDS_sat=0.154V
  m2_tail_current                 W=42.085µm  L=1.120µm  IDS=20.00µA  VGS=-0.942V  VDS_sat=0.154V
  mn1_second_stage                W=17.855µm  L=0.560µm  IDS=50.00µA  VGS=0.807V  VDS_sat=0.145V
  mp1_second_stage                W=105.210µm  L=1.120µm  IDS=50.00µA  VGS=-0.942V  VDS_sat=0.154V
  mnref_bias_gen                  W=9.760µm  L=1.120µm  IDS=20.00µA  VGS=0.832V  VDS_sat=0.171V
  mn5_bias_gen                    W=9.760µm  L=1.120µm  IDS=20.00µA  VGS=0.832V  VDS_sat=0.171V
  mp5_bias_gen                    W=42.085µm  L=1.120µm  IDS=20.00µA  VGS=-0.942V  VDS_sat=0.154V
  mn7_bias_gen                    W=9.760µm  L=1.120µm  IDS=20.00µA  VGS=0.832V  VDS_sat=0.171V
  c1_compensation                 C=1.25pF

Feasibility: FEASIBLE

Performance metrics (ngspice / BSIM4):
  Open-loop gain         81.17 dB          [spec ≥ 60.00 dB]               margin +21.17 dB  ✓
  GBW                    6.93 MHz          [spec ≥ 2.00 MHz]               margin +4.93 MHz  ✓
  Phase margin           63.49 °           [spec ≥ 60.00 °]                margin +3.49 °  ✓
  Slew rate              8.87 V/µs         [spec ≥ 0.30 V/µs]              margin +8.57 V/µs  ✓
  Quiescent power        0.38 mW           [spec ≤ 2.00 mW]                margin +1.62 mW  ✓
  CMRR                   78.19 dB
  PSRR+                  86.75 dB
  Output swing max       3.29 V            [spec ≥ 3.00 V]                 margin +0.29 V  ✓
  Output swing min       0.04 V            [spec ≤ 0.30 V]                 margin +0.26 V  ✓

Corner verification (re-measured; sized at typical):
  metric             typical        ss        ff        sf        fs
  Gain (dB)            81.17     81.29     80.91     81.65     80.56
  GBW (MHz)             6.93      6.52      7.37      7.13      6.73
  PM (deg)             63.49     63.78     63.16     62.17     64.75
  Slew (V/µs)           8.87      8.85      8.97      8.89      8.90
  Power (mW)            0.38      0.37      0.38      0.38      0.38
  CMRR (dB)            78.19     76.42     79.03     77.83     77.74
  PSRR+ (dB)           86.75     83.18     88.52     88.46     84.15
  Swing hi (V)          3.29      3.29      3.29      3.29      3.29
  Swing lo (V)          0.04      0.04      0.04      0.04      0.04

real	0m3,897s
```

About 4 s in total, corner sweep included (the 2026-09-30 guide said ~20 s).

### What each line means

All of it is printed by [`_cmd_size`](../../../circuitgenome/cli.py#L214).

1. **`Netlist … | Topology …`** — an echo of your arguments.
2. **`Tech: gf180mcu_3v3`** — the `name:` field inside
   [`tech_gf180mcu.yaml`](../../../circuitgenome/sizer/config/tech_gf180mcu.yaml#L9),
   not the word you typed. `--tech gf180mcu` is not a file; `load_tech` falls
   back to the built-in `tech_<name>.yaml`
   ([`loader.py:74`](../../../circuitgenome/sizer/loader.py#L74)).
3. **`Solver: GMID`** — which sizer ran. The dispatcher sends any tech with a
   `gmid_lut` to the gm/Id pipeline
   ([`sizer.py:54`](../../../circuitgenome/sizer/sizer.py#L54)), and that
   pipeline stamps `"GMID"` ([`gmid_sizer.py:183`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L183)).
   The `generic` path prints the CP-SAT solver's status instead (`OPTIMAL`, see 7.5).
   See [two strategies](00_background.md#two-strategies).
4. **`⚠` lines** — `result.warnings`. None here. Before printing, the CLI also
   runs ngspice's DC operating point check (`check_bias_soundness`,
   [`cli.py:243`](../../../circuitgenome/cli.py#L243)). If that check fails,
   the CLI flips `bias_feasible` to `False` and adds the reason as a warning.
5. **Transistor sizing** — one row per transistor:
   - `W`, `L`: the width and length the sizer chose, snapped to the
     [manufacturing grid](00_background.md#technology).
   - `IDS`: its planned DC current. The tail carries the full `ibias` = 20 µA,
     each pair device half of it (10 µA), and the second stage
     `second_stage_current_ratio × ibias` = 2.5 × 20 = 50 µA
     ([KCL](00_background.md#kcl)).
   - `VGS`: gate-to-source voltage at that current (negative for PMOS).
   - `VDS_sat`: the device's [Vdsat](00_background.md#vdsat), the smallest
     drain-source voltage that keeps it in saturation. Its headroom cost.
   - The table does not show fingers. `mp1_second_stage` (105 µm, wider than
     gf180's 100 µm maximum) is actually drawn as 2 fingers. In Python,
     `result.transistors["mp1_second_stage"]` prints `…, fingers=2)`.
6. **`c1_compensation C=1.25pF`** — the Miller cap Cc
   ([Miller compensation](00_background.md#miller)). 1.25 pF is exactly
   0.25 × CL (CL = 5 pF): the stability floor `_CC_STABILITY_RATIO · CL`
   ([`preprocess.py:39`](../../../circuitgenome/sizer/physics/preprocess.py#L39),
   used at [`preprocess.py:422`](../../../circuitgenome/sizer/physics/preprocess.py#L422)).
   The slew spec would allow up to `ibias/SR` = 20 µA / 0.3 V/µs ≈ 67 pF, but
   SR only sets an *upper* bound.
7. **`Feasibility: FEASIBLE`** — the verdict, three levels
   ([`cli.py:303`](../../../circuitgenome/cli.py#L303)):
   - `INFEASIBLE`: the bias point cannot be set up (analytical check or the
     SPICE `.op` check above). Metrics are not printed.
   - `MARGINAL`: it biases, but at least one metric misses its spec.
   - `FEASIBLE`: it biases and every constrained metric meets spec.

   For a tech with a real SPICE model (gf180, sky130, ptm45) the misses are
   counted on the **ngspice** numbers
   ([`cli.py:340`](../../../circuitgenome/cli.py#L340)). For `generic` they are
   counted on the analytical margins.
8. **`Performance metrics (ngspice / BSIM4)`** — the referee's numbers, from
   `simulate_metrics` ([`simulate.py:20`](../../../circuitgenome/sizer/verify/simulate.py#L20)).
   For LUT techs the CLI **never prints the sizer's own estimate** (see 7.4 for
   how to get it). Each row is value, spec, signed margin (positive = good), ✓/✗:
   - *Open-loop gain* 81 dB: ∏ gm·Rout ([opamp](00_background.md#opamp)).
   - *GBW* 6.93 MHz: where gain falls to 1 ([GBW](00_background.md#gbw)).
   - *Phase margin* 63.5°: how far from oscillating in feedback ([PM](00_background.md#phase-margin)).
   - *Slew rate* 8.87 V/µs: the large-step speed limit ([slew](00_background.md#slew)).
   - *Quiescent power* 0.38 mW: `Vdd × total supply current` ([power](00_background.md#power)).
   - *CMRR*, *PSRR+*: no margin, because `spec_gf180.yaml` sets no
     `cmrr_min_db` / `psrr_min_db` ([CMRR](00_background.md#cmrr), [PSRR](00_background.md#psrr)).
   - *Output swing max / min*: the highest and lowest output level that still
     tracks ([swing](00_background.md#swing)). For *min*, smaller is better, so
     the spec reads `≤`.
9. **Corner verification** — the same W/L re-simulated at the five process
   corners in the tech's `spice_lib.corners`
   ([`cli.py:359`](../../../circuitgenome/cli.py#L359)). The sizer itself only
   ever sized at `typical`. Only `spice_lib` techs (gf180, sky130) get this table.
   ptm45 has a single BSIM4 card and no corners.

### Reading the corner table

**Corners in plain words.** Every wafer comes out a little different. The
foundry gives five model sets: `typical`, and four extremes named by
*NMOS letter, then PMOS letter*: `ss` (both slow), `ff` (both fast), `sf`
(slow NMOS, fast PMOS), `fs` (fast NMOS, slow PMOS). "Slow" means a higher
threshold and lower mobility, so less current and less gm for the same
voltages. In gf180's library, for example, the `fs` NMOS card has
`vth0 = 0.646 V` against `0.708 V` at typical, and the `fs` PMOS card
`vth0 = −0.815 V` against `−0.751 V`
([`sm141064.ngspice:11685`](../../../circuitgenome/sizer/pdk/gf180/ngspice/sm141064.ngspice#L11685),
first size bin).
**Analogy**: the same cake recipe (the W/L) baked in five different ovens. A
good recipe comes out edible in all five.

For `circuit_0010` the spread is small. Worst and best per metric:

| Metric | Worst corner | Best corner | Why, in plain words |
|---|---|---|---|
| GBW | `ss` 6.52 MHz | `ff` 7.37 MHz | GBW ≈ gm1 / (2π·Cc). The pair's current is pinned at 10 µA by the mirrors, so a slow PMOS gives a little less gm1 for that current. Cc is a fixed capacitor. |
| Phase margin | `sf` 62.2° | `fs` 64.8° | PM depends on how far the second pole sits above GBW, roughly gm2/gm1 (the PM rule `gm2 ≥ gm1·CL/(Cc·tan(90°−PM))`, [`preprocess.py:513`](../../../circuitgenome/sizer/physics/preprocess.py#L513)). gm2 comes from the **NMOS** `mn1_second_stage`, gm1 from the **PMOS** pair. `sf` = slow NMOS + fast PMOS shrinks gm2/gm1 the most, `fs` grows it. |
| Gain | `fs` 80.6 dB | `sf` 81.7 dB | ±0.6 dB: gain is `gm·Rout` products, and corners move gm and gds in the same direction, so they mostly cancel. |
| CMRR / PSRR | `ss` 76.4 / 83.2 dB | `ff` 79.0 / 88.5 dB | Both still ~80 dB. No spec is set, so they are information only. |
| Slew, power, swing | flat | flat | Set by currents (copied from the ideal 20 µA `ibias` source in the bench) and fixed caps, not by device speed. Swing is set by Vdsat, which moves only a few mV. |

Nothing comes near a spec limit at any corner. The tightest is PM at `sf`:
62.2° against 60°.

### Is `--simulate` still redundant?

Yes, for every tech with a SPICE model. Adding it changes only the last line:

```console
$ time (PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec examples/two_stage_se_specs/spec_gf180.yaml --simulate | tail -4)
  Swing hi (V)          3.29      3.29      3.29      3.29      3.29
  Swing lo (V)          0.04      0.04      0.04      0.04      0.04

(--simulate is redundant for this technology: the metrics above are already measured with ngspice.)

real	0m3,990s
```

The branch is [`cli.py:406`](../../../circuitgenome/cli.py#L406). `--simulate`
is useful only on `generic`, where it adds an analytical-vs-SPICE table (7.5).

**Test to read.** [`test_cli.py::test_size_ptm45_metrics_from_spice`](../../../tests/test_cli.py#L137)
asserts the `(ngspice / BSIM4)` header for a SPICE-model tech;
[`test_cli.py::test_size_marginal_verdict`](../../../tests/test_cli.py#L84)
asserts `MARGINAL` plus a ✗ for a spec the design misses. Both need netlists in
a different folder (see 7.6).

**Check yourself.**
1. You see `Solver: OPTIMAL`. Which sizer ran, and are the printed metrics from ngspice?
2. Why does the PM row get worse at `sf` and better at `fs`, and not the other way round?
3. Why is Cc 1.25 pF and not the ~67 pF the slew spec would allow?

<details><summary>Answers</summary>

1. The analytical Level-1 sizer (CP-SAT status), so the tech is `generic`
   (no LUT, no SPICE model). The table under `Performance metrics:` is the
   analytical estimate. ngspice numbers appear only with `--simulate`.
2. The second-stage device that sets gm2 is NMOS; the input pair that sets gm1
   is PMOS. `sf` slows the NMOS and speeds up the PMOS, so gm2/gm1 falls and the
   second pole moves toward the 0 dB crossing.
3. The slew rule only caps Cc from above (`Cc ≤ ibias/SR`). The sizer starts
   from the stability floor `0.25·CL` = 1.25 pF and raises it only if GBW needs
   it ([`preprocess.py:422`](../../../circuitgenome/sizer/physics/preprocess.py#L422),
   [`test_size_two_stage_cc_stability_floor`](../../../tests/test_sizer.py#L533)).

</details>

---

## 7.3 The contrast: `circuit_0001` (resistor load)

```console
$ time PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0001_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec examples/two_stage_se_specs/spec_gf180.yaml
Netlist: circuit_0001_flat.ckt  |  Topology: two_stage_opamp_single_ended
Tech: gf180mcu_3v3

Solver: GMID
⚠ second-stage gm requirement exceeds the weak-inversion ceiling — increase second_stage_current_ratio/ibias or relax gain.
⚠ first-stage load is a fixed rail-referenced resistor: its DC drop is sized from the LUT Vgs at the nominal corner and cannot track Vth across PVT corners, so the inter-stage bias is corner-fragile — verify gain across corners in SPICE.

Transistor sizing:
  m1_input_pair                   W=3.070µm  L=0.560µm  IDS=10.00µA  VGS=-1.083V  VDS_sat=0.274V
  ...
  mn1_second_stage                W=100.000µm  L=0.560µm  IDS=50.00µA  VGS=0.690V  VDS_sat=0.079V
  mp1_second_stage                W=105.210µm  L=1.120µm  IDS=50.00µA  VGS=-0.942V  VDS_sat=0.154V
  ...
  c1_compensation                 C=1.25pF
  r1_load                         R=76.04kΩ
  r2_load                         R=76.04kΩ

Feasibility: MARGINAL — biases, but does not meet spec

Performance metrics (ngspice / BSIM4):
  Open-loop gain         52.34 dB          [spec ≥ 60.00 dB]               margin -7.66 dB  ✗
  GBW                    3.41 MHz          [spec ≥ 2.00 MHz]               margin +1.41 MHz  ✓
  Phase margin           81.81 °           [spec ≥ 60.00 °]                margin +21.81 °  ✓
  Slew rate              6.37 V/µs         [spec ≥ 0.30 V/µs]              margin +6.07 V/µs  ✓
  Quiescent power        0.38 mW           [spec ≤ 2.00 mW]                margin +1.62 mW  ✓
  CMRR                   39.15 dB
  PSRR+                  34.99 dB
  Output swing max       3.20 V            [spec ≥ 3.00 V]                 margin +0.20 V  ✓
  Output swing min       0.07 V            [spec ≤ 0.30 V]                 margin +0.23 V  ✓

Corner verification (re-measured; sized at typical):
  metric             typical        ss        ff        sf        fs
  Gain (dB)            52.34     51.87     52.66     52.71     51.90
  GBW (MHz)             3.41      3.23      3.55      3.54      3.26
  PM (deg)             81.81     82.07     81.70     81.36     82.28
  Slew (V/µs)           6.37      7.05      5.48      6.97      5.70
  Power (mW)            0.38      0.37      0.38      0.38      0.38
  CMRR (dB)            39.15     35.31     40.49     40.53     37.54
  PSRR+ (dB)           34.99     31.98     36.47     35.34     34.32
  Swing hi (V)          3.20      3.21      3.11      3.22      3.13
  Swing lo (V)          0.07      0.07      0.07      0.07      0.07

real	0m3,780s
```

The rows hidden by `...` are identical to `circuit_0010`'s. Verdict:
**MARGINAL**, gain 52.3 dB against a 60 dB spec, short by 7.7 dB at every
corner.

### Why the gain falls short

Gain is the product of two stage gains, each `gm · Rout`. To see the two
factors, this snippet wraps the metric evaluator and prints the
[stage chain](03_physics.md) the sizer used (a reading aid, not repo code):

```python
# chain_peek.py — print each stage's gm, Rout and gain (run with PYTHONPATH=$PWD)
import math
from pathlib import Path
from circuitgenome.recognizer import parse, recognize, assign_slots
from circuitgenome.synthesizer.loader import load_topologies
from circuitgenome.sizer import load_spec, load_tech, size_circuit
import circuitgenome.sizer.gmid.evaluate as ev

captured = []
orig = ev.evaluate_metrics
ev.evaluate_metrics = lambda chain, spec: (captured.append(chain), orig(chain, spec))[1]

topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
tech, spec = load_tech("gf180mcu"), load_spec("examples/two_stage_se_specs/spec_gf180.yaml")
for cid in ("circuit_0010", "circuit_0001"):
    parsed = parse(Path(f"circuits/two_stage_se/{cid}_flat.ckt").read_text())
    sr = recognize(parsed)
    size_circuit(parsed, sr, assign_slots(sr, topology), topology, tech, spec)
    ch = captured[-1]
    print(cid, "k_fs", ch.k_fs)
    for i, s in enumerate(ch.stages):
        a = (ch.k_fs if i == 0 else 1) * s.gm * s.rout
        print(f"  stage{i+1}: gm={s.gm*1e6:.1f} uS  rout={s.rout/1e3:.1f} kOhm  gain={20*math.log10(a):.1f} dB")
```

Output:

```
circuit_0010 k_fs 1.0
  stage1: gm=60.0 uS  rout=1162.1 kOhm  gain=36.9 dB
  stage2: gm=575.0 uS  rout=297.5 kOhm  gain=44.7 dB
circuit_0001 k_fs 0.5
  stage1: gm=60.0 uS  rout=74.7 kOhm  gain=7.0 dB
  stage2: gm=912.8 uS  rout=197.9 kOhm  gain=45.1 dB
```

The second stages are about equal (~45 dB). The whole 30 dB difference is in
**stage 1**: 36.9 dB vs 7.0 dB. Three reasons, from Stop 3 and Stop 4 code:

1. **The resistor's value is set by headroom, not chosen for gain.**
   `size_load_resistors` ([`preprocess.py:51`](../../../circuitgenome/sizer/physics/preprocess.py#L51))
   computes `R = V_drop / I` ([line 101](../../../circuitgenome/sizer/physics/preprocess.py#L101)).
   `I` is the branch current, `ibias/2` = 10 µA. `V_drop` is not free either:
   the resistor's top node `net_mid` is also the gate of `mn1_second_stage`, so
   it must sit at exactly the second stage's turn-on `Vgs` above ground, about
   0.76 V (read from the LUT, [`plan.py:69`](../../../circuitgenome/sizer/gmid/plan.py#L69),
   issue #158). So `R = 0.76 V / 10 µA = 76 kΩ`, the value printed. A bigger R
   would push `net_mid` higher and turn the second stage hard on. This is the
   [stage interface](00_background.md#stage-interface) rule seen from the
   resistor's side.
   **Analogy**: a water tower whose height is fixed by the building next door.
   You cannot make it taller to get more pressure.
2. **Half the signal is thrown away.** A current mirror copies the left branch's
   signal current to the right, so both halves of the pair reach the output. A
   resistor load cannot do that: only one side's current reaches `net_mid`.
   The code calls this `k_fs` (first-stage factor), 1.0 for a mirror and 0.5
   otherwise ([`_first_stage_gain_factor`, `preprocess.py:281`](../../../circuitgenome/sizer/physics/preprocess.py#L281)).
   Stage-1 gain = `0.5 × 60 µS × 74.7 kΩ` = 2.24 = 7.0 dB. (74.7 kΩ is the
   76 kΩ resistor in parallel with the pair device's own `ro`.)
3. **A mirror's Rout is not limited by a voltage drop.** The mirror's
   output resistance is the two transistors' [ro](00_background.md#rout) in
   parallel, about 1.16 MΩ here. It comes from channel-length modulation, not
   from `V/I`, so it can be 15× bigger than the resistor while keeping the same
   DC level. Stage-1 gain = `1.0 × 60 µS × 1162 kΩ` = 70 = 36.9 dB.

Put as one formula: with a resistor load, stage 1 gives at most
`k_fs · (gm/Id) · V_drop` ≈ 0.5 × 6 /V × 0.76 V ≈ 2.3. Even at the
weak-inversion ceiling of gm/Id (~25 /V) it only reaches about 10 (20 dB).

**What the two warnings mean.**

- *"second-stage gm requirement exceeds the weak-inversion ceiling"*
  ([`preprocess.py:547`](../../../circuitgenome/sizer/physics/preprocess.py#L547)).
  The sizer tried to make up the missing gain in stage 2: it solved
  `gm2 = A0 / (k_fs · gm1 · Rout1 · Rout2)`
  ([`preprocess.py:494`](../../../circuitgenome/sizer/physics/preprocess.py#L494))
  and got a gm2 that 50 µA cannot deliver. A transistor's gm per µA tops out
  in [weak inversion](00_background.md#inversion), however wide it is. The
  request was clamped to the ceiling, and `mn1_second_stage` ended at the
  widest allowed device, `W=100.000µm`, with a tiny `VDS_sat=0.079V` (deep
  weak inversion). That bought only +0.4 dB (45.1 vs 44.7 dB): gm2 rose from
  575 to 913 µS, but `Rout2` came out lower (198 vs 298 kΩ).
- *"first-stage load is a fixed rail-referenced resistor … corner-fragile"*
  ([`evaluate.py:69`](../../../circuitgenome/sizer/gmid/evaluate.py#L69)).
  The resistor's drop was matched to the second stage's `Vgs` at `typical`.
  At another corner the transistor's `Vgs` moves but the resistor's
  `I·R` does not, so the two no longer agree. A mirror load tracks automatically
  because its diode is a transistor too. Here the risk is only advisory: the
  corner table shows gain stays at 51.9–52.7 dB. Note also that the final
  `mn1_second_stage` sits at `VGS=0.690V`, not the 0.76 V the resistor was sized
  for. The docstring of `_resistor_load_bias` says the DC feedback absorbs this
  as a small offset ([`evaluate.py:35`](../../../circuitgenome/sizer/gmid/evaluate.py#L35)).

**Why the other metrics look *better*.** PM is 81.8° (vs 63.5°) because the
first-stage gm reaching the loop is halved (`k_fs` = 0.5), so GBW halves
(3.41 MHz) and sits further below the second pole. CMRR and PSRR (~35–39 dB)
are much worse. The README table has the analytical side of these numbers.

**Test to read.**
[`test_sizer.py::test_first_stage_gain_factor`](../../../tests/test_sizer.py#L1155)
pins `k_fs` = 1.0 for a mirror and 0.5 for a resistor or plain current-source
load.
[`test_sizer.py::test_resistor_load_lut_seed_biases_interstage`](../../../tests/test_sizer.py#L1526)
sizes exactly this PMOS-pair / resistor-to-gnd / NMOS-CS combination on gf180
and asserts the gain is reported and only the corner-fragility advisory is
raised.

**Check yourself.**
1. Why can't the sizer simply pick R = 1 MΩ to get the mirror's gain?
2. Doubling the second-stage current (`second_stage_current_ratio: 5.0`) — will it fix the gain? (Try it in 7.7.)

<details><summary>Answers</summary>

1. The resistor's DC drop `I·R` must equal the second stage's `Vgs` (~0.76 V).
   With 10 µA, 1 MΩ would drop 10 V, far more than the 3.3 V supply. To keep the
   same drop you would have to cut the current 13×, and gm1 would fall with it.
2. No. gm2 roughly doubles but `Rout2 ∝ 1/I` halves, so `gm2·Rout2` stays about
   the same. The run in 7.7 measures 52.37 dB.

</details>

---

## 7.4 Analytical vs SPICE, side by side

For LUT techs the CLI prints only ngspice's numbers. To see the sizer's own
estimate next to them, call the Python API directly. This is the README's
running-example script, cleaned up (saved as `compare.py` anywhere, run from
the repo root):

```python
# compare.py <circuit> <tech> <spec.yaml>
import sys
from pathlib import Path
from circuitgenome.recognizer import parse, recognize, assign_slots
from circuitgenome.synthesizer.loader import load_topologies
from circuitgenome.sizer import load_spec, load_tech, size_circuit, simulate_metrics

circuit, tech_name, spec_file = sys.argv[1:4]
netlist = Path(f"circuits/two_stage_se/{circuit}_flat.ckt").read_text()
topology = next(t for t in load_topologies() if t.name == "two_stage_opamp_single_ended")
parsed = parse(netlist)
sr = recognize(parsed)
fbr = assign_slots(sr, topology)
tech = load_tech(tech_name)
spec = load_spec(spec_file)

result = size_circuit(parsed, sr, fbr, topology, tech, spec)   # analytical estimate
spice = simulate_metrics(netlist, result, tech, spec)           # ngspice referee
for key, value in result.metrics.items():
    print(f"{key:20s} analytical={value:12.4g}  spice={spice.get(key, float('nan')):12.4g}")
```

```console
$ time PYTHONPATH=$PWD python compare.py circuit_0010 gf180mcu examples/two_stage_se_specs/spec_gf180.yaml
gain_db              analytical=       81.53  spice=       81.17
gbw_hz               analytical=   7.057e+06  spice=   6.931e+06
phase_margin_deg     analytical=       63.22  spice=       63.49
slew_rate_vps        analytical=       8e+06  spice=    8.87e+06
power_w              analytical=    0.000363  spice=   0.0003783
output_swing_max_v   analytical=       3.146  spice=       3.293
output_swing_min_v   analytical=      0.1446  spice=     0.03799
cmrr_db              analytical=       80.06  spice=       78.19
psrr_db              analytical=       90.45  spice=       86.75
real	0m1,013s
```

Sizing takes about 0.06 s; the rest is one ngspice run of every bench. The same
script on `circuit_0001`, collected into one table:

| Metric | Spec | `0010` analytical | `0010` ngspice | gap | `0001` analytical | `0001` ngspice | gap |
|---|---|---|---|---|---|---|---|
| Gain | ≥ 60 dB | 81.53 dB | 81.17 dB | −0.4 dB | 52.15 dB | 52.34 dB | +0.2 dB |
| GBW | ≥ 2 MHz | 7.06 MHz | 6.93 MHz | −2 % | 3.52 MHz | 3.41 MHz | −3 % |
| PM | ≥ 60° | 63.2° | 63.5° | +0.3° | 81.4° | 81.8° | +0.4° |
| Slew | ≥ 0.3 V/µs | 8.00 V/µs | 8.87 V/µs | **+11 %** | 8.00 V/µs | 6.37 V/µs | **−20 %** |
| Power | ≤ 2 mW | 0.363 mW | 0.378 mW | +4 % | 0.363 mW | 0.378 mW | +4 % |
| Swing max | ≥ 3.0 V | 3.146 V | 3.293 V | **+0.15 V** | 3.146 V | 3.200 V | +0.05 V |
| Swing min | ≤ 0.3 V | 0.145 V | 0.038 V | **−0.11 V** | 0.079 V | 0.070 V | −0.01 V |
| CMRR | — | 80.1 dB | 78.2 dB | −1.9 dB | 38.9 dB | 39.2 dB | +0.3 dB |
| PSRR | — | 90.5 dB | 86.8 dB | −3.7 dB | 35.0 dB | 35.0 dB | 0.0 dB |

Gain, GBW, PM, power, CMRR and PSRR agree within a few percent. Two metrics
stand out.

### Slew: same idea, different ruler

- **Analytical** ([`metrics.py:154` `_slew_rate`](../../../circuitgenome/sizer/physics/metrics.py#L154)):
  the slowest of two `I / C` limits
  ([line 180](../../../circuitgenome/sizer/physics/metrics.py#L180)):
  the first stage pushing `k_slew × I_tail` into Cc, and the second stage
  pushing its quiescent current into `Cc + CL`.
  - `circuit_0010`: `min(1.0 × 20 µA / 1.25 pF, 50 µA / 6.25 pF)` = `min(16, 8)` = **8 V/µs** (the output node limits).
  - `circuit_0001`: `k_slew` = `k_fs` = 0.5 for a resistor load
    ([`stage_chain.py:1316`](../../../circuitgenome/sizer/physics/stage_chain.py#L1316)),
    so `min(0.5 × 20 / 1.25, 8)` = `min(8, 8)` = **8 V/µs**. Both limits tie.
- **ngspice** ([`measure.py:426` `_measure_sr`](../../../circuitgenome/sizer/verify/measure.py#L426)):
  wires the amplifier as a unity buffer, applies a 0.3·Vdd step, and on each
  edge keeps the **steepest** secant across 20 % of the swing
  ([`_edge_slew`, line 331](../../../circuitgenome/sizer/verify/measure.py#L331)).
  It reports the slower of the rising and falling edges
  ([line 501](../../../circuitgenome/sizer/verify/measure.py#L501)).

So the formula assumes each node is fed by its fixed quiescent current, while
the bench measures the real large-signal edge, where currents are no longer at
their quiescent values. That definition gap is verified in the code. *Why*
`0010` comes out 11 % faster and `0001` 20 % slower (and why `0001`'s slew
spreads 5.5–7.1 V/µs over corners while `0010` stays flat) is an **open
question**: answering it needs a look at the transient waveforms, which this
stop did not do. The [slew](00_background.md#slew) entry notes that an older,
much larger slew gap was a bench artifact and is fixed.

### Swing: "device still saturated" vs "output still tracks"

- **Analytical** ([`metrics.py:305`](../../../circuitgenome/sizer/physics/metrics.py#L305),
  [`stage_chain.py:876` `_stage_swing_headroom`](../../../circuitgenome/sizer/physics/stage_chain.py#L876)):
  each rail minus the output device's Vdsat. `0010`: max = 3.3 − 0.154
  (`mp1_second_stage`) = 3.146 V; min = 0 + 0.145 (`mn1_second_stage`) = 0.145 V.
  These match the printed `VDS_sat` values exactly.
- **ngspice** ([`measure.py:505` `_measure_swing`](../../../circuitgenome/sizer/verify/measure.py#L505)):
  sweeps the output through an inverting gain −1 loop and keeps the range where
  the output still follows, slope ≤ −0.7
  ([line 536](../../../circuitgenome/sizer/verify/measure.py#L536)).

The analytical number is "the last point where every output device is
saturated". The bench number is "the last point where the closed loop still
tracks". Past Vdsat the device enters triode and its gain drops, but with
81 dB of open-loop gain to spare the loop still tracks. So ngspice reads a
wider swing. `circuit_0001` has only 52 dB to spare, and its SPICE swing sits
much closer to the analytical edge (+0.05 / −0.01 V). That fits the
explanation, but the exact amount was not derived here. The analytical figure
is the conservative one, which is the safe side for a spec check.

**Check yourself.**
1. Which of the two slew limits sets `circuit_0010`'s 8 V/µs, and what would you change to raise it?
2. Why is the analytical swing always inside the SPICE swing on these two circuits?

<details><summary>Answers</summary>

1. The output node: `50 µA / (Cc + CL)` = 8 V/µs. Raise the second-stage
   current (`second_stage_current_ratio`) or lower CL. Raising `ibias` lifts
   both limits.
2. Analytical swing stops at the saturation edge (rail ∓ Vdsat). The bench
   keeps going as long as the feedback loop still forces the output to follow,
   which continues a little way into triode while open-loop gain remains.

</details>

---

## 7.5 A second technology

Same netlist, each tech with its own spec file from
[`examples/two_stage_se_specs/`](../../../examples/two_stage_se_specs/)
(`spec_sky130.yaml`, `spec_ptm45.yaml`, `spec_generic.yaml`). The specs differ
(the supplies are 1.8 V, 1.0 V and 5 V), so compare verdicts, not raw numbers.

```console
$ time PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech sky130 \
    --spec examples/two_stage_se_specs/spec_sky130.yaml
...
Tech: sky130_1v8

Solver: GMID
...
  c1_compensation                 C=1.25pF

Feasibility: MARGINAL — biases, but does not meet spec

Performance metrics (ngspice / BSIM4):
  Open-loop gain         64.93 dB          [spec ≥ 40.00 dB]               margin +24.93 dB  ✓
  GBW                    10.99 MHz         [spec ≥ 0.80 MHz]               margin +10.19 MHz  ✓
  Phase margin           48.10 °           [spec ≥ 60.00 °]                margin -11.90 °  ✗
  Slew rate              6.49 V/µs         [spec ≥ 0.30 V/µs]              margin +6.19 V/µs  ✓
  Quiescent power        0.14 mW           [spec ≤ 2.00 mW]                margin +1.86 mW  ✓
  CMRR                   46.47 dB
  PSRR+                  47.98 dB
  Output swing max       1.79 V            [spec ≥ 1.50 V]                 margin +0.29 V  ✓
  Output swing min       0.03 V            [spec ≤ 0.30 V]                 margin +0.27 V  ✓

Corner verification (re-measured; sized at tt):
  metric                  tt        ss        ff        sf        fs
  Gain (dB)            64.93     67.17     62.20     64.65     65.42
  GBW (MHz)            10.99     10.37     10.95     10.61     10.49
  PM (deg)             48.10     47.81     50.28     50.77     47.62
  ...
real	0m23,675s

$ time PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech ptm45 \
    --spec examples/two_stage_se_specs/spec_ptm45.yaml
...
Tech: ptm45_hp

Solver: GMID
⚠ second-stage gm requirement exceeds the weak-inversion ceiling — increase second_stage_current_ratio/ibias or relax gain.
...
  c1_compensation                 C=0.50pF

Feasibility: FEASIBLE

Performance metrics (ngspice / BSIM4):
  Open-loop gain         66.43 dB          [spec ≥ 60.00 dB]               margin +6.43 dB  ✓
  GBW                    26.03 MHz         [spec ≥ 2.50 MHz]               margin +23.53 MHz  ✓
  Phase margin           61.70 °           [spec ≥ 60.00 °]                margin +1.70 °  ✓
  ...
real	0m0,759s

$ time PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech generic \
    --spec examples/two_stage_se_specs/spec_generic.yaml --simulate
...
Tech: generic_parameterized

Solver: OPTIMAL
⚠ second-stage gm requirement exceeds the weak-inversion ceiling — increase second_stage_current_ratio/ibias or relax gain.
...
  c1_compensation                 C=2.86pF

Feasibility: MARGINAL — biases, but does not meet spec (see ⚠ above)

Performance metrics:
  Open-loop gain         90.12 dB          [spec ≥ 80.00 dB]               margin +10.12 dB  ✓
  GBW                    2.54 MHz          [spec ≥ 2.50 MHz]               margin +0.04 MHz  ✓
  Phase margin           57.93 °           [spec ≥ 60.00 °]                margin -2.07 °  ✗
  Slew rate              1.09 V/µs         [spec ≥ 3.50 V/µs]              margin -2.41 V/µs  ✗
  ...

SPICE verification (ngspice):
  metric                   analytical          SPICE         Δ
  Open-loop gain             90.12 dB       91.12 dB       +1%
  GBW                       2.541 MHz      2.931 MHz      +15%
  Phase margin                 57.9 °         59.1 °       +2%
  Slew rate                1.094 V/µs     1.371 V/µs      +25%
  ...
real	0m0,990s
```

Together with 7.2:

| `--tech` | Path ([Stop 0](00_orientation.md#exit-check)) | `Solver:` | Metrics shown | Corners | Verdict | Misses | Time |
|---|---|---|---|---|---|---|---|
| `gf180mcu` | gm/Id (`gmid_lut` set) | `GMID` | ngspice | 5 (`spice_lib`) | FEASIBLE | — | 3.9 s |
| `sky130` | gm/Id (`gmid_lut` set) | `GMID` | ngspice | 5 (`spice_lib`) | MARGINAL | PM 48.1° < 60° | 23.7 s |
| `ptm45` | gm/Id (`gmid_lut` set) | `GMID` | ngspice | none (one `spice_model` card) | FEASIBLE | — | 0.8 s |
| `generic` | Level-1 + CP-SAT (no LUT, no SPICE model) | `OPTIMAL` | analytical (+ SPICE with `--simulate`) | none | MARGINAL | PM 57.9°, slew 1.09 < 3.5 V/µs | 1.0 s |

**Tie to Stop 0's exit check ("which path does each tech take, and why?").**
You can now *see* the answer in the output. The dispatcher looks at tech
*fields*, not names ([`sizer.py:54`](../../../circuitgenome/sizer/sizer.py#L54),
[`:62`](../../../circuitgenome/sizer/sizer.py#L62)). Three techs ship a
`gmid_lut` and print `Solver: GMID` with ngspice metrics. `generic` has neither
a LUT nor a SPICE model, so it goes to Level-1, prints a CP-SAT status, and
shows analytical metrics unless you add `--simulate`. The corner table is a
third, separate switch: it appears only when the tech has a corner library
(`spice_lib` with more than one corner), so ptm45 has none.

**Notes on the extra runs.**

- **sky130 misses PM, and the sizer knew.** Running `compare.py circuit_0010 sky130 …`
  gives an analytical PM of 48.6° (ngspice 48.1°). The analytical margin is
  −11.4°, yet no `⚠` warning is printed and `bias_feasible` is `True`, so only
  the ✗ in the ngspice table tells you. GBW is 11 MHz against a 0.8 MHz spec:
  far more bandwidth than asked for, paid for in PM. Why the sizer does not
  trade some of it back (a larger Cc or a stronger second stage) is an **open
  question**. 7.7 shows the same pattern on gf180. Also note the spec file's
  header still says GBW is "current-limited (~0.9 MHz)".
- **ptm45 warns but passes.** The gm-ceiling warning text says "relax gain",
  but gain passes (66.4 dB). The clamp acts on `gm2_req`, which is the largest
  of the gain, PM and other requirements
  ([`preprocess.py:494`](../../../circuitgenome/sizer/physics/preprocess.py#L494),
  [`:513`](../../../circuitgenome/sizer/physics/preprocess.py#L513)), so the
  binding one need not be gain.
- **sky130 is slow** (24 s vs 4 s on gf180). Timed separately in Python,
  `size_circuit` takes 0.04 s and one `simulate_metrics` run 4.0 s; the CLI
  does six of those (nominal plus five corners). The time is all ngspice on
  the sky130 library.
- **`generic` with `--simulate`** is the only place the CLI shows an
  analytical-vs-SPICE table. The "SPICE" there uses an ngspice `level=1`
  model built from the same square-law parameters
  ([`deck.py:69`](../../../circuitgenome/sizer/verify/deck.py#L69)), so it
  checks the circuit math, not the device model.

---

## 7.6 Run the tests

```console
$ PYTHONPATH=$PWD pytest -q tests/test_sizer.py --collect-only -q | tail -1
tests/test_sizer.py: 97
$ PYTHONPATH=$PWD pytest -q tests/test_sizer.py --collect-only -q -m "not slow" | tail -1
tests/test_sizer.py: 93

$ time PYTHONPATH=$PWD pytest -q tests/test_sizer.py --durations=8
...
28.73s setup    tests/test_sizer.py::test_size_three_stage_fd_basic
7.07s setup    tests/test_sizer.py::test_size_three_stage_rnmc_fd_basic
0.91s call     tests/test_sizer.py::test_fd_stage_interface_exempts_output_sensing_cmfb
...
96 passed, 1 xfailed in 52.14s
real	0m52,514s

$ time PYTHONPATH=$PWD pytest -q tests/test_sizer.py -m "not slow" -rx
...
XFAIL tests/test_sizer.py::test_sizer_meets_slew_through_every_stage - the sizer only bounds Cc <= ibias/SR; the later stages' and the follower's currents are not sized to slew their own nodes, so the design slews ~1.4 V/us (SPICE) against a 3.5 V/us spec
92 passed, 4 deselected, 1 xfailed in 22.90s
real	0m23,425s
```

| Run | Tests | Result | Time |
|---|---|---|---|
| `tests/test_sizer.py` | 97 | 96 passed, 1 xfailed | 52 s |
| `tests/test_sizer.py -m "not slow"` | 93 | 92 passed, 1 xfailed, 4 deselected | 23 s |
| `tests/test_cli.py` (as checked out) | 10 | 3 passed, 7 skipped | 1 s |
| whole suite, `pytest -q` | 738 | 724 passed, 13 skipped, 1 xfailed | 4 min 52 s |

What changed since the old guide ("71 tests, ~15 min, no `slow` marks"):

- **Speed.** #253 and #254 (recognizer speed-ups) made the file about 17×
  faster. Almost all of the 52 s is one module fixture: the three-stage
  fully differential circuit (`test_size_three_stage_fd_basic` setup, 28.7 s).
- **`slow` marks.** #250 counted 13 `slow` marks in this file. Commit `cd4a29c`
  re-labelled them by measured time after the speed-up. Now 4 remain
  ([`test_sizer.py:993`](../../../tests/test_sizer.py#L993) and the next three),
  the tests sharing that slow fixture. The marker's rule is in
  [`pyproject.toml`](../../../pyproject.toml): "takes over ~15 s, or shares a
  slow module fixture with a test that does". `-m "not slow"` now halves this file.
- **Whole suite.** 4 min 52 s on this machine (`pytest -q`, no other jobs
  running). The project notes say ~3 min; this run was slower, and the
  difference was not investigated.
- **The one xfail** is strict
  ([`test_sizer.py:494`](../../../tests/test_sizer.py#L494)): a known slew gap
  on three-stage designs, kept as a red flag rather than deleted.

**`test_cli.py` skips silently.** Seven of its ten tests need netlists under
`circuits/two_stage_opamp_single_ended/`
([`test_cli.py:52`](../../../tests/test_cli.py#L52)), not the
`circuits/two_stage_se/` folder used above. To run them, generate a second copy:

```console
$ PYTHONPATH=$PWD circuitgenome synthesize --topology two_stage_opamp_single_ended --output-dir circuits/two_stage_opamp_single_ended
...
Total: 162 circuits written to circuits/two_stage_opamp_single_ended/
$ time PYTHONPATH=$PWD pytest -q tests/test_cli.py
...
FAILED tests/test_cli.py::test_size_ptm45_infeasible_verdict - AssertionError...
FAILED tests/test_cli.py::test_size_spice_bias_infeasible - AssertionError: a...
2 failed, 8 passed in 2.75s
```

**Two of them fail once they can run.** Both still give `INFEASIBLE`, but for a
different reason than the test expects. `test_size_ptm45_infeasible_verdict`
looks for "cascode tail", but today's `circuit_0110` has a plain two-device
PMOS tail, and the reason printed is a stage-interface warning ("the
second-stage input pins the first-stage output at 0.30 V but the load needs it
≥ 0.66 V …"). `test_size_spice_bias_infeasible` looks for "SPICE bias check",
but `circuit_0019` is now caught earlier by the same stage-interface check.
So the circuit numbers in these tests point at different variants than when
the tests were written (last changed in `ee878aa`, 2026-09-27), and nobody
noticed because the tests normally skip. Delete the folder afterwards if you
don't want the two failures in your normal runs.

**While reading**, run single tests with `-k`, for example
`pytest -q tests/test_sizer.py -k resistor_load`.

---

## 7.7 Next experiments

All of these are fast (≈ 4 s each). The spec copies go under the git-ignored
`circuits/` folder.

**1. Ask for more bandwidth.** Raise `gbw_min_hz` from 2 MHz to 10 MHz and
watch what the sizer moves.

```console
$ sed 's/^gbw_min_hz: .*/gbw_min_hz: 10.0e+6/' examples/two_stage_se_specs/spec_gf180.yaml > circuits/spec_gf180_gbw10.yaml
$ PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec circuits/spec_gf180_gbw10.yaml | grep -E 'input_pair|second_stage|compensation|Feasib|gain|GBW |Phase'
  m1_input_pair                   W=7.070µm  L=0.560µm  IDS=10.00µA  VGS=-0.969V  VDS_sat=0.187V
  m2_input_pair                   W=7.070µm  L=0.560µm  IDS=10.00µA  VGS=-0.969V  VDS_sat=0.187V
  mn1_second_stage                W=17.855µm  L=0.560µm  IDS=50.00µA  VGS=0.807V  VDS_sat=0.145V
  mp1_second_stage                W=105.210µm  L=1.120µm  IDS=50.00µA  VGS=-0.942V  VDS_sat=0.154V
  c1_compensation                 C=1.25pF
Feasibility: MARGINAL — biases, but does not meet spec
  Open-loop gain         84.53 dB          [spec ≥ 60.00 dB]               margin +24.53 dB  ✓
  GBW                    9.97 MHz          [spec ≥ 10.00 MHz]              margin -0.03 MHz  ✗
  Phase margin           53.05 °           [spec ≥ 60.00 °]                margin -6.95 °  ✗
```

The sizer raised gm1 by **widening** the input pair (3.07 → 7.07 µm) at the same
10 µA. Its `VDS_sat` dropped from 0.274 to 0.187 V: it moved toward weak
inversion, a higher gm/Id. Cc stayed at its 1.25 pF floor, and the second stage
did not change at all, so PM fell to 53°. The analytical model predicts the same
(`compare.py` gives PM 53.2°). The two-stage re-plan
([`two_stage_refine.py`](../../../circuitgenome/sizer/gmid/two_stage_refine.py),
called from [`gmid_sizer.py:152`](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L152))
reported "no change needed" on its second pass while its own chain read PM 53.2°.
Why it stops there is the same **open question** as sky130's PM in 7.5.

**2. Give the second stage more current.** Same 10 MHz spec, plus
`second_stage_current_ratio: 5.0`:

```console
$ sed 's/^second_stage_current_ratio: .*/second_stage_current_ratio: 5.0/' circuits/spec_gf180_gbw10.yaml > circuits/spec_gf180_gbw10_r5.yaml
$ PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0010_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec circuits/spec_gf180_gbw10_r5.yaml | grep -E 'second_stage|Feasib|GBW |Phase|Quiescent'
  mn1_second_stage                W=35.715µm  L=0.560µm  IDS=100.00µA  VGS=0.807V  VDS_sat=0.145V
  mp1_second_stage                W=210.425µm  L=1.120µm  IDS=100.00µA  VGS=-0.942V  VDS_sat=0.154V
Feasibility: MARGINAL — biases, but does not meet spec
  GBW                    9.88 MHz          [spec ≥ 10.00 MHz]              margin -0.12 MHz  ✗
  Phase margin           69.61 °           [spec ≥ 60.00 °]                margin +9.61 °  ✓
  Quiescent power        0.55 mW           [spec ≤ 2.00 mW]                margin +1.45 mW  ✓
```

Twice the current gives twice the width at the same `VGS` (same gm/Id), so
about twice the gm2, and PM recovers to 69.6° for +0.17 mW of power. That is the
[phase-margin](00_background.md#phase-margin) trade in one run. GBW still
misses, by about 1 % (9.88 MHz).

**3. The same current on `circuit_0001` does not fix its gain.**

```console
$ sed 's/^second_stage_current_ratio: .*/second_stage_current_ratio: 5.0/' examples/two_stage_se_specs/spec_gf180.yaml > circuits/spec_gf180_r5.yaml
$ PYTHONPATH=$PWD circuitgenome size circuits/two_stage_se/circuit_0001_flat.ckt \
    --topology two_stage_opamp_single_ended --tech gf180mcu \
    --spec circuits/spec_gf180_r5.yaml | grep -E 'mn1_second|r1_load|Feasib|gain'
  mn1_second_stage                W=100.000µm  L=0.560µm  IDS=100.00µA  VGS=0.731V  VDS_sat=0.098V
  r1_load                         R=76.04kΩ
Feasibility: MARGINAL — biases, but does not meet spec
  Open-loop gain         52.37 dB          [spec ≥ 60.00 dB]               margin -7.63 dB  ✗
```

52.34 → 52.37 dB. `R` is still 76 kΩ: it depends only on `ibias/2` and the
LUT seed `Vgs` ([`plan.py:69`](../../../circuitgenome/sizer/gmid/plan.py#L69)),
and neither changed. So stage 1 still gives only 7 dB. Stage 2's
`gm·Rout` hardly depends on its current. The gain gap is structural: it is in
the load slot, not in a knob.

**4. Read the options.**

```console
$ PYTHONPATH=$PWD circuitgenome size --help
usage: circuitgenome size [-h] --topology TOPOLOGY [--tech TECH_FILE]
                          --spec SPEC_FILE [--time-limit TIME_LIMIT]
                          [--simulate]
                          NETLIST
...
  --tech TECH_FILE      Technology YAML config (default: built-in generic)
  --spec SPEC_FILE      Performance specification YAML file
  --time-limit TIME_LIMIT
                        CP-SAT solver time limit in seconds (default: 30)
  --simulate            After sizing, verify metrics with an ngspice
                        simulation (same technology) and print analytical vs
                        SPICE
```

What the help does not say: `--tech` also takes the short names `gf180mcu`,
`sky130`, `ptm45` and `generic`; `--time-limit` matters only for `generic`
(the only CP-SAT path); and `--simulate` is a no-op note on SPICE-model techs.

More to try on your own: swap in `circuit_0011` (Cc plus nulling resistor)
and compare PM; or size a cascode-load variant and compare its stage-1 `Rout`
with the 1.16 MΩ above.

---

## Exit check

**Explain, without looking, why `circuit_0001` misses the gain spec and
`circuit_0010` doesn't, using the words gm, Rout, and headroom.**

<details><summary>Answer</summary>

Open-loop gain is the product of the two stage gains, each `gm · Rout`. Both
circuits have the same input pair (gm1 = 60 µS at 10 µA) and almost the same
second stage (~45 dB). The difference is all in the first stage's **Rout**,
which the load sets.

- **`circuit_0010` (current-mirror load).** Rout1 is the parallel `ro` of the
  pair device and the mirror device, about 1.16 MΩ. `ro` comes from
  channel-length modulation, not from a voltage drop, so it is large without
  costing **headroom**. The mirror also folds both halves of the pair's signal
  into the output (`k_fs` = 1.0,
  [`preprocess.py:281`](../../../circuitgenome/sizer/physics/preprocess.py#L281)).
  Stage 1 = 60 µS × 1.16 MΩ ≈ 70 (36.9 dB). Total 81 dB > 60 dB.
- **`circuit_0001` (resistor load).** A resistor's R is fixed by **headroom**:
  `R = V_drop / I` ([`preprocess.py:101`](../../../circuitgenome/sizer/physics/preprocess.py#L101)).
  The drop must equal the next stage's `Vgs` (~0.76 V), because the resistor's
  top node is the second stage's gate, and `I` = 10 µA. So R = 76 kΩ, and Rout1
  ≈ 75 kΩ, 15× smaller than the mirror's. Only half the signal reaches the
  output (`k_fs` = 0.5). Stage 1 = 0.5 × 60 µS × 75 kΩ ≈ 2.2 (7 dB).
- The sizer tried to make up the gap with **gm** in stage 2
  (`gm2 = A0 / (k_fs·gm1·Rout1·Rout2)`,
  [`preprocess.py:494`](../../../circuitgenome/sizer/physics/preprocess.py#L494)),
  but the gm needed is beyond what 50 µA can give in weak inversion. Hence the
  "weak-inversion ceiling" warning and a 100 µm device that adds only 0.4 dB.
  Total 52 dB < 60 dB at every corner. Giving stage 2 more current does not help
  either (7.7, run 3), because its Rout falls as its gm rises.

In one line: **gain = gm·Rout; the mirror's Rout is free, the resistor's Rout
is capped by headroom (R = V/I), and gm cannot grow past the weak-inversion
ceiling to make up the 30 dB.**

</details>

---

## Simplification notes

| Location | Observation |
|---|---|
| [`cli.py:309`](../../../circuitgenome/cli.py#L309) | The `INFEASIBLE` message says "run --simulate to measure the actual operating point", but on any SPICE-model tech `--simulate` only prints the "redundant" note ([`cli.py:406`](../../../circuitgenome/cli.py#L406)), so the hint leads nowhere. |
| [`cli.py:54`](../../../circuitgenome/cli.py#L54) `--tech` help | Says "Technology YAML config"; the built-in short names (`gf180mcu`, `sky130`, `ptm45`, `generic`) work only through `load_tech`'s fallback ([`loader.py:74`](../../../circuitgenome/sizer/loader.py#L74)) and are not mentioned. A local file or folder named e.g. `sky130` would shadow the built-in. |
| [`cli.py:58`](../../../circuitgenome/cli.py#L58), [`:60`](../../../circuitgenome/cli.py#L60) | `--time-limit` applies only to `generic`, and `--simulate` is a no-op on SPICE-model techs; neither help string says so. |
| [`cli.py:253`](../../../circuitgenome/cli.py#L253) | `Solver:` mixes a pipeline label (`GMID`) with a CP-SAT status (`OPTIMAL`/`FEASIBLE`) in one field. |
| [`cli.py:262`](../../../circuitgenome/cli.py#L262) | The sizing table omits `fingers`, so `mp1_second_stage` shows `W=105.210µm`, above gf180's 100 µm `width.max`, with no hint that it is drawn as 2 fingers. |
| [`cli.py:272`](../../../circuitgenome/cli.py#L272) comment | "For PTM the numbers come from ngspice": true for every SPICE-model tech (gf180, sky130 too). Same drift as README candidate #6. |
| [`sizer.py:62`](../../../circuitgenome/sizer/sizer.py#L62) | The "SPICE model without LUT → error" guard checks only `spice_model`. A `spice_lib` PDK tech without a `gmid_lut` would fall through to Level-1 silently, against the policy in the module docstring. |
| [`preprocess.py:551`](../../../circuitgenome/sizer/physics/preprocess.py#L551) | The second-stage gm-ceiling warning says "relax gain", but `gm2_req` is the max of the gain and PM requirements; on ptm45 it fires while gain passes. |
| `gmid_sizer.py` / `two_stage_refine.py` | An analytical PM miss (sky130: margin −11.4°; gf180 at 10 MHz GBW: −6.8°) produces no `⚠` warning. The re-plan returns "unchanged" while its own chain reads PM below spec. Open question, not confirmed as a bug. |
| [`spec_sky130.yaml`](../../../examples/two_stage_se_specs/spec_sky130.yaml) header | Says GBW is "current-limited (~0.9 MHz)"; `circuit_0010` sizes to 12.9 MHz analytical / 11.0 MHz ngspice and misses PM (48°). The "9/24 meet every margin" claim may be stale too (not re-checked). |
| `examples/two_stage_se_specs/*.yaml` usage comments, [`test_cli.py:52`](../../../tests/test_cli.py#L52) | Comments and tests expect netlists in `circuits/two_stage_opamp_single_ended/`; this guide and the old one use `circuits/two_stage_se/`. With only the latter present, 7 of 10 CLI tests skip silently. |
| [`cli.py:307`](../../../circuitgenome/cli.py#L307) | The `↳` reason under `INFEASIBLE` is picked by keyword ("cascode", "headroom", "collapse", "SPICE bias"). A stage-interface failure ("stage interface cannot bias …") matches none of them, so the verdict prints with no reason under it (the `⚠` line above still has it). |
| [`test_cli.py:62`](../../../tests/test_cli.py#L62), [`:118`](../../../tests/test_cli.py#L118) | Both tests fail when their fixtures exist: `circuit_0110` no longer has a cascode tail and `circuit_0019` is now rejected by the stage-interface check, not the SPICE bias check. Pinning variants by number makes these tests drift when the synthesizer's order changes; they normally skip, so the drift goes unnoticed. |
