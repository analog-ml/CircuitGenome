# Stop 3 — physics/: the shared circuit math

**Files** (in reading order):

| File | Lines | Part |
|---|---|---|
| [`physics/taxonomy.py`](../../../circuitgenome/sizer/physics/taxonomy.py) | 54 | 3.1 |
| [`physics/equations.py`](../../../circuitgenome/sizer/physics/equations.py) | 974 | 3.2 |
| [`physics/gmid_lut.py`](../../../circuitgenome/sizer/physics/gmid_lut.py) | 161 | 3.3 |
| [`physics/device_model.py`](../../../circuitgenome/sizer/physics/device_model.py) | 403 | 3.4 |
| [`physics/circuit_view.py`](../../../circuitgenome/sizer/physics/circuit_view.py) | 216 | 3.5 |
| [`physics/rnmc.py`](../../../circuitgenome/sizer/physics/rnmc.py) | 277 | 3.6 |
| [`physics/preprocess.py`](../../../circuitgenome/sizer/physics/preprocess.py) | 706 | 3.7 |
| [`physics/node_levels.py`](../../../circuitgenome/sizer/physics/node_levels.py) | 141 | 3.8 |
| [`physics/stage_chain.py`](../../../circuitgenome/sizer/physics/stage_chain.py) | 1318 | 3.9 |
| [`physics/metrics.py`](../../../circuitgenome/sizer/physics/metrics.py) | 334 | 3.10 |

(4,584 lines, plus an 18-line [`__init__.py`](../../../circuitgenome/sizer/physics/__init__.py)
whose docstring is the map of this package.)

**Time**: 6–8 h (about 3 h for 3.1–3.4, the rest for 3.5–3.10).

**The stop's promise**, from [`__init__.py:17`](../../../circuitgenome/sizer/physics/__init__.py#L17):

> Nothing here decides a width. Nothing here runs a simulator.

Everything in `physics/` is *circuit math*. It takes numbers (currents, gm
values, capacitances) or an already-sized circuit and returns more numbers
(requirements, poles, predicted metrics). The decisions (which W, which L)
live in `gmid/` and `analytical/` (Stops 4 and 5). The referee (ngspice) lives
in `verify/` (Stop 6).

**After this stop you can**:

- point at the function that turns each spec metric into a formula, and say
  whether it is a closed-form rule of thumb or the full nodal model;
- run the gm/Id forward recipe (gm target → gm/Id → LUT → W) by hand for any
  device of `circuit_0010`, and say why the input pair ends up at gm/Id = 6;
- explain how the sized circuit becomes a small-signal chain, and how that
  chain becomes the eight predicted metrics (3.5–3.10).

---

## Background for this stop

### Recap: concepts from the primer you will need

- **Device physics**: [MOSFET](00_background.md#mosfet),
  [NMOS / PMOS](00_background.md#nmos-pmos), [regions](00_background.md#regions),
  [Vdsat](00_background.md#vdsat), [threshold and body effect](00_background.md#vth-body),
  [bias point vs small signal](00_background.md#small-signal), [gm](00_background.md#gm),
  [rout / gds](00_background.md#rout), [square law](00_background.md#square-law),
  [inversion levels](00_background.md#inversion), [gm/Id and the LUT](00_background.md#gmid),
  [ft](00_background.md#ft), [parasitic caps](00_background.md#parasitic-caps).
- **Building blocks**: [KCL](00_background.md#kcl), [rails](00_background.md#rails),
  [common-source stage](00_background.md#common-source),
  [current mirror](00_background.md#current-mirror),
  [differential pair and tail](00_background.md#diff-pair), [loads](00_background.md#loads),
  [cascode](00_background.md#cascode), [source follower](00_background.md#source-follower),
  [bias generator](00_background.md#bias-generator).
- **The op-amp**: [stages](00_background.md#stages),
  [fully differential](00_background.md#fully-differential), [CMFB](00_background.md#cmfb),
  [headroom](00_background.md#headroom), [swing](00_background.md#swing).
- **Speed and stability**: [poles](00_background.md#poles), [GBW](00_background.md#gbw),
  [phase margin](00_background.md#phase-margin), [RHP zero](00_background.md#rhp-zero),
  [Miller](00_background.md#miller), [nested Miller / RNMC](00_background.md#nested-miller),
  [slew](00_background.md#slew).
- **Metrics**: [the spec sheet](00_background.md#spec-metrics), [power](00_background.md#power),
  [CMRR](00_background.md#cmrr), [PSRR](00_background.md#psrr), [dB](00_background.md#db),
  and above all [nodal analysis](00_background.md#nodal-analysis).
- **The two sizers**: [search vs forward pass](00_background.md#two-strategies).

### Extra concepts this stop uses

The primer stops at circuit ideas. This stop's code also uses a few numerical
tricks. Each one is short.

<a id="bilinear"></a>
#### Bilinear interpolation (reading between the rows of a table)

**In plain words.** The gm/Id lookup table (LUT) is a 2-D grid. Rows are
channel lengths `L`, columns are gm/Id values. A query almost never lands
exactly on a grid point. Bilinear interpolation answers "what is the value at
(gm/Id = 10.25, L = 0.56)?" by blending the four surrounding grid values,
weighted by how close the query is to each.

**Analogy.** A road atlas distance chart only lists big cities. To guess a
town's distance, you take the two nearest listed cities and go a fraction of
the way between their numbers. Do that once along each axis.

**Formula the code uses.** It splits the blend into two 1-D steps:

```
1. along L:      row(L)  = (1 − t)·row[L0] + t·row[L1]      t = (L − L0)/(L1 − L0)
2. along gm/Id:  value   = np.interp(gm_id, gm_id_axis, row(L))
```

Two details matter for reading the code:

- **Clamping, not extrapolation.** A query outside the grid is clamped to the
  nearest edge. A gm/Id of 3 on a table that starts at 6 reads as 6. This is
  why the table's first gm/Id is a hard floor in the sizer (see
  [3.4](#34-device_modelpy-403-lines)).
- **Log-space blending** for one table (`gds_scale`). Its values span decades
  (1× to 20×), so the code blends `log(value)` and exponentiates back. Linear
  blending of numbers that differ by 10× would badly over-estimate the middle.

<a id="complex-gain"></a>
#### Complex gain: one number for "how much" and "how late"

**In plain words.** At frequency `f` the code writes `s = j·ω` with
`ω = 2π·f` (angular frequency, rad/s). The amplifier's gain at that frequency,
`A(jω)`, is a complex number. Its size `|A|` (in [dB](00_background.md#db)) is
how much the signal is amplified. Its angle `∠A` is how far the output lags
the input, in degrees. A [Bode plot](00_background.md#poles) is just these two
numbers drawn against log-frequency.

**Analogy.** A clock hand. Its length is the gain; the angle it has turned is
the delay.

<a id="stamping"></a>
#### Building a nodal matrix by "stamping"

**In plain words.** [Nodal analysis](00_background.md#nodal-analysis) writes
one KCL equation per node: `(G + s·C)·v = b`. Each circuit element touches a
fixed set of matrix cells, so you build the matrix by adding ("stamping") each
element's contribution in turn:

| Element | Cells it adds to |
|---|---|
| conductance `g` between nodes `i` and `j` | `+g` at `(i,i)` and `(j,j)`, `−g` at `(i,j)` and `(j,i)`. To ground: only `(i,i)` |
| capacitor `c` | the same pattern, in `C` instead of `G` |
| transistor `gm·v_ctrl` (a *voltage-controlled current source*, VCCS) | `+gm` at `(i, ctrl)` only |
| the input signal | an entry in `b` |

**Analogy.** A plumbing spreadsheet with one row per pipe junction. Each pipe
you add writes into the two rows of the junctions it joins.

<a id="eigen-poles"></a>
#### Poles are eigenvalues

**In plain words.** A [pole](00_background.md#poles) is a frequency `s` where
the circuit "rings by itself", that is, where `det(G + s·C) = 0`. Rearranged,
the poles are the eigenvalues of `−C⁻¹·G`, which `numpy` computes directly.

- A pole with a **negative real part** is a disturbance that dies out: stable.
- A pole with a **positive real part** (a right-half-plane, RHP, pole) grows:
  the amplifier oscillates.
- A **complex pair** rings while it dies out. Its damping is
  `ζ = −Re(p)/|p|` ([nested Miller](00_background.md#nested-miller)).

<a id="false-position"></a>
#### Finding a crossing: bracket, then false position

**In plain words.** To find the frequency where the gain is exactly 0 dB, the
code first sweeps 4,000 frequencies and finds two neighbours where the gain
changes sign (one above 0 dB, one below). Then it draws a straight line between
those two points, takes where the line crosses zero, evaluates the real gain
there, and keeps the half that still brackets the crossing. Three rounds of
this (*false position*, also called *regula falsi*) pin the crossing down far
more precisely than the sweep grid.

**Analogy.** Guessing a word's page in a dictionary: open at the page the
alphabet suggests, then narrow from there.

<a id="fixed-point"></a>
#### Fixed-point iteration

**In plain words.** Some quantities depend on themselves. A device's source
voltage sets its body effect, and the body effect shifts its `Vgs`, which moves
the source voltage. The code guesses, recomputes, and repeats a few times until
the answer stops moving. `node_levels.py` does four rounds.

---

## 3.1 `taxonomy.py` (54 lines)

**Job in one sentence.** It holds every naming assumption the sizer makes:
which slot names exist, which bias-current group each belongs to, and which
net names are rails or bias nets.

**Design knowledge it encodes.**

- [KCL](00_background.md#kcl): the two sides of a [differential pair](00_background.md#diff-pair)
  each carry half the tail current, so the pair and its load form a "half
  bias" group.
- [Stages](00_background.md#stages) and [fully differential](00_background.md#fully-differential)
  halves (`_p` / `_n` slot suffixes).
- [Source follower](00_background.md#source-follower): gain ≈ 1, so it is not
  a gain stage.
- [Rails](00_background.md#rails) are AC ground: a small wiggle cannot move a
  fixed supply.
- [Bias generator](00_background.md#bias-generator) nets: a transistor gated
  by one of them is a current source, not a signal device.

**Walkthrough.**

1. [`taxonomy.py:17`](../../../circuitgenome/sizer/physics/taxonomy.py#L17)
   `HALF_BIAS_SLOTS = {"input_pair", "load"}`. These slots split one current
   between their devices. The consumer is
   [`preprocess.assign_ids`](../../../circuitgenome/sizer/physics/preprocess.py#L235),
   which gives each device `ibias / n`, where `n` counts the same-polarity
   devices in the slot (2 for a plain pair, so `ibias/2`). This is
   [KCL](00_background.md#kcl) at the tail node.
2. [`taxonomy.py:19`](../../../circuitgenome/sizer/physics/taxonomy.py#L19)
   `FULL_BIAS_SLOTS = {"tail_current", "bias_gen"}`: each device carries the
   whole `ibias`.
3. [`taxonomy.py:21`](../../../circuitgenome/sizer/physics/taxonomy.py#L21) and
   [`:23`](../../../circuitgenome/sizer/physics/taxonomy.py#L23): the second-
   and third-stage slot names, single-ended and FD. `assign_ids` gives them
   `ibias × second_stage_current_ratio` (or `third_stage_current_ratio`).
4. [`taxonomy.py:28`](../../../circuitgenome/sizer/physics/taxonomy.py#L28)
   `OUTPUT_STAGE_SLOTS`, and the comment above it: a follower is
   *deliberately* left out of `STAGE_SLOTS`
   ([`:30`](../../../circuitgenome/sizer/physics/taxonomy.py#L30)), so it never
   enters the gain product `∏ gm·Rout`. That is the
   [source-follower](00_background.md#source-follower) idea in one line.
5. [`taxonomy.py:33`](../../../circuitgenome/sizer/physics/taxonomy.py#L33)
   `BIAS_NETS` (supplies and `ibias`) and
   [`:35`](../../../circuitgenome/sizer/physics/taxonomy.py#L35) `RAILS`
   (supplies and `"0"`). `RAILS` is where an output-resistance walk stops (AC
   ground) and which nets [`node_levels`](#38-node_levelspy-141-lines)
   pins first.
6. [`taxonomy.py:38`](../../../circuitgenome/sizer/physics/taxonomy.py#L38)
   `is_signal_device(device)`: True when the gate is **not** a bias net, not
   `net_bias*`, and not a bias generator's internal `*_pref` / `*_ncasc` gate.
   Code all over `physics/` and `gmid/` uses it to pick the transistor that
   gives a [common-source stage](00_background.md#common-source) its gm (the
   other device in the slot is its current-source load).

   ```python
   gate = device.terminals.get("g", "")
   return (bool(gate) and gate not in BIAS_NETS
           and not gate.startswith("net_bias") and not gate.endswith("_pref")
           and not gate.endswith("_ncasc"))
   ```

   It is a **name test**, not a connectivity test. It trusts the synthesizer's
   net-naming convention.

**Running example.** `circuit_0010`'s twelve devices, as the sizer sees them
(slots from the recognizer, groups and the signal test from this file):

| Device | Slot | Group | Gate net | `is_signal_device` | Planned current |
|---|---|---|---|---|---|
| `m1_input_pair`, `m2_input_pair` | `input_pair` | HALF | `in1` / `in2` | True | 10 µA each |
| `m1_load`, `m2_load` | `load` | HALF | `net_diff1` | **True** | 10 µA each |
| `m1_tail_current`, `m2_tail_current` | `tail_current` | FULL | `net_bias7` | False | 20 µA |
| `mn1_second_stage` | `second_stage` | SECOND | `net_mid` | True | 50 µA |
| `mp1_second_stage` | `second_stage` | SECOND | `net_bias5` | False | 50 µA |
| `mnref`, `mn5`, `mn7_bias_gen` | `bias_gen` | FULL | `ibias` | False | 20 µA |
| `mp5_bias_gen` | `bias_gen` | FULL | `net_bias5` | False | 20 µA |

(`ibias` = 20 µA and `second_stage_current_ratio` = 2.5 in the gf180 spec.)

Note the mirror load: its gate is the diode node `net_diff1`, which is not a
bias-net name, so it reads as "signal". That is harmless in a `load` slot
(nothing there looks for *the* signal device), but it means "signal" in this
function really means "gate not on a bias net".

**Test to read.**
[`test_gmid_blocks.py::test_is_signal_device`](../../../tests/test_gmid_blocks.py#L31):
a gate on `in1` is signal; a gate on `net_bias1` or `vdd!` is not. There is no
`test_taxonomy.py`; the groups are exercised through `assign_ids` and the
topology-mismatch guard in [`circuit_view.py:137`](../../../circuitgenome/sizer/physics/circuit_view.py#L137)
(3.5).

**Check yourself.**

1. Why is `output_stage` kept out of `STAGE_SLOTS`?
2. A new template names its second stage `gain2`. What current does
   `assign_ids` give its devices, and what else would silently go wrong?
3. In `circuit_0010`, which device of the `second_stage` slot supplies `gm2`,
   and how does the code know?

<details><summary>Answers</summary>

1. A source follower has gain ≈ 1. Counting it as a stage would put it into
   the gain product and into the stage chain the PM model is built from, as if
   it were a common-source gain stage. It is handled separately instead (the
   `buffer` argument of the loop models in 3.2).
2. `gain2` is in no group, so it falls through to the "conservative default"
   branch and gets plain `ibias`
   ([`preprocess.py:246`](../../../circuitgenome/sizer/physics/preprocess.py#L246)),
   not 2.5×. It would also be invisible to every check that looks for
   `SECOND_STAGE_SLOTS` / `STAGE_SLOTS` (the gm2 requirement, the stage chain,
   the topology guard). The fix is to add the name here, which is the module
   docstring's point.
3. `mn1_second_stage`: its gate is `net_mid`, which is not a bias net.
   `mp1_second_stage` is gated by `net_bias5`, so `is_signal_device` is False
   and it is treated as the current-source load.

</details>

---

## 3.2 `equations.py` (974 lines)

**Job in one sentence.** Every formula the sizers use, written as a pure
function of numbers: no devices, no netlists, no LUT.

**Design knowledge it encodes.** Nearly the whole primer: Parts 2, 5 and 6.
The links are given per family below.

The file has six families. Read them in this order; only family E (the nodal
loop model) needs real time.

| Family | Lines | What it is | Used by |
|---|---|---|---|
| A. Level-1 device primitives | [21–178](../../../circuitgenome/sizer/physics/equations.py#L21) | square-law gm, gds, Vgs, Vdsat, caps, body effect | `Level1Model` (3.4), `GmIdModel` for caps and body effect |
| B. Gain | [185–245](../../../circuitgenome/sizer/physics/equations.py#L185) | product of stage gains in dB; "can the bench measure it?" | `metrics.py`, both sizers |
| C. Closed-form GBW and PM | [248–337](../../../circuitgenome/sizer/physics/equations.py#L248) | `gm1/(2π·Cc)`, `90° − atan(…)` | `metrics.py` (mostly as fallbacks) |
| D. RNMC closed forms and the nulling resistor | [340–408](../../../circuitgenome/sizer/physics/equations.py#L340) | pole-pair damping rule, `R = (Cc+C)/(gm·Cc)` | `rnmc.py`, `gmid/resistors.py`, `metrics.py` |
| E. Nodal loop models | [411–914](../../../circuitgenome/sizer/physics/equations.py#L411) | build `G + sC`, sweep, find 0 dB crossings, worst PM | `metrics.py`, `rnmc.py`, `stage_chain.py` |
| F. Slew, power, CMRR, PSRR | [917–974](../../../circuitgenome/sizer/physics/equations.py#L917) | `I/C`, `V·ΣI`, `gm/gd` ratios | `metrics.py` |

Units are SI throughout (A, V, F, Hz, S), with `W` and `L` in µm. A name
suffix states any exception (`_db`, `_deg`, `_hz`, `_um`).

### Family A — Level-1 device primitives (lines 21–178)

**Formulas** ([square law](00_background.md#square-law),
[gm](00_background.md#gm), [rout](00_background.md#rout),
[parasitic caps](00_background.md#parasitic-caps),
[body effect](00_background.md#vth-body)):

```
gm       = √(2·µCox·(W/L)·|Id|)                  gm(),            line 25
gm_max   = 25 /V · |Id|                          gm_ceiling(),    line 49
gd       = λ·|Id|                                gd(),            line 62
Rout     = 1/(gd_top + gd_bot)                   rout(),          line 73
Vgs      = Vth ± √(2·|Id|·L/(µCox·W))            vgs_from_ids(),  line 86
Vdsat    = √(2·|Id|·L/(µCox·W))                  vds_sat(),       line 151
Cgs      = (2/3)·W·L·Cox                         cgs(),           line 112
Cdb      = W·cdb                                 cdb(),           line 134
ΔVth     = γ·(√(2φF + Vsb) − √(2φF))             body_vth_shift(), line 165
```

Each is one or two lines of arithmetic. Three things to notice:

- [`_GM_OVER_ID_MAX = 25.0`](../../../circuitgenome/sizer/physics/equations.py#L46)
  is the physics ceiling `1/(n·φt)`. The square law would let gm grow without
  limit as W grows; real devices stop at about 25·Id in weak inversion. The
  LUT path replaces this constant with the table's own last gm/Id (24 on
  gf180).
- `cgs` and `cdb` return `0.0` when the tech YAML has no `cox` / `cdb`. A zero
  capacitance is the caller's signal "no pole can be placed here".
- `vgs_from_ids` keeps the sign of `vth` (negative for PMOS). Most other
  functions take `abs(ids)`, so one code path serves both polarities
  ([NMOS / PMOS](00_background.md#nmos-pmos)).

**Docstring check.** Clear: each docstring states the formula, the units and
what a `None` means. Fine for a layman as is.

### Family B — Gain (lines 185–245)

**Formula** ([stages](00_background.md#stages), [dB](00_background.md#db)):
`A0 = 20·log10(|∏ gm_j·Rout_j|)`.

- [`open_loop_gain_db`](../../../circuitgenome/sizer/physics/equations.py#L185):
  multiply the per-stage gains, convert to dB. `-inf` when a stage gain is 0.
- [`open_loop_gain_measurable`](../../../circuitgenome/sizer/physics/equations.py#L212)
  answers a practical question: *could the SPICE bench even read this gain?*
  The open-loop AC bench reads "DC gain" at its first frequency point, 1 mHz.
  The gain curve starts falling at the dominant pole, `GBW/A0`. If that pole
  is below 1 mHz, the bench reads a point already on the slope and reports
  less than the true DC gain. So the readable ceiling is
  `20·log10(GBW / 1 mHz)`, capped at about 212 dB where the bench's own DC
  feedback loop takes over. Without a GBW it uses the fixed
  [`OPEN_LOOP_GAIN_CEILING_DB = 180`](../../../circuitgenome/sizer/physics/equations.py#L209).
  This is advisory only: the result is a flag, the gain is still reported.

**Docstring check.** `open_loop_gain_db` is clear.
`open_loop_gain_measurable` and the comment on `OPEN_LOOP_GAIN_CEILING_DB` are
hard for a layman. They lean on bench internals (`Lfb`/`Cfb`, `AC_START_HZ`)
and issue history (#155, #287). A one-sentence summary first ("a bench that
starts its sweep at 1 mHz cannot see a dominant pole below 1 mHz") would help.

### Family C — Closed-form GBW and PM (lines 248–337)

**Formulas** ([GBW](00_background.md#gbw), [phase margin](00_background.md#phase-margin),
[Miller](00_background.md#miller), [nested Miller](00_background.md#nested-miller)):

```
GBW              = gain·gm1/(2π·Cc)                      unity_gain_bw(),                 line 248
PM (one stage)   = 90° − atan(GBW / f_mirror)            phase_margin_single_stage_deg(), line 266
PM (two stage)   = 90° − atan(gm1·CL / (gm2·Cc))         phase_margin_two_stage_deg(),    line 291
PM (NMC, 3 stg)  = 90° − atan(ωt·Cc2/gm2) − atan(ωt·CL/gm3),  ωt = gm1/Cc1
                                                         phase_margin_three_stage_deg(),  line 311
```

These are the textbook "one dominant pole plus one lag term per extra pole"
rules. Each `atan` term is the phase lag of one [pole](00_background.md#poles).

- `unity_gain_bw`'s `gain` argument is the gain of an output
  [source follower](00_background.md#source-follower) (about 0.88 on gf180),
  which scales the crossing down.
- `phase_margin_single_stage_deg` is for a load-compensated one-stage OTA
  (operational transconductance amplifier, an op-amp with no Miller cap). Its
  docstring explains well why the roles of `CL` and the internal node swap
  compared with the two-stage formula.
- On a multi-stage circuit these are now **fallbacks**. `metrics.py` prefers
  the full nodal model (family E) and only uses the two-stage PM formula when
  a three-stage chain lacks a `Cc2` (see
  [`metrics.py:269`](../../../circuitgenome/sizer/physics/metrics.py#L269)).
  `phase_margin_three_stage_deg` has no production caller at all; only a test
  uses it, as the "old formula that got it wrong".

**Docstring check.** Clear. Each states the formula and when it is valid.
`phase_margin_two_stage_deg`'s "there is no pole pair to ring" assumes the
reader knows what a pole pair is; a link to the RNMC section would do.

### Family D — RNMC closed forms and the nulling resistor (lines 340–408)

**Formulas** ([RHP zero](00_background.md#rhp-zero),
[nested Miller / RNMC](00_background.md#nested-miller)):

- [`compensation_resistor_ohm`](../../../circuitgenome/sizer/physics/equations.py#L361):
  `R = (Cc + C)/(gm_out·Cc)`. A resistor in series with the Miller cap moves
  the [RHP zero](00_background.md#rhp-zero) onto the output pole, so the two
  cancel. `C` is `CL`, except in RNMC where it is the buffer's gate (or 0).
- [`rnmc_min_gm2`](../../../circuitgenome/sizer/physics/equations.py#L379):
  the smallest `gm2` that damps the RNMC pole pair to `ζ = 0.7`. The comment
  block at [lines 340–358](../../../circuitgenome/sizer/physics/equations.py#L340)
  derives it. The denominator reduces to `1 + a1·s + a2·s²`, the damping is
  `ζ = a1/(2·√a2)`, and setting `ζ ≥ 0.7` gives a quadratic in `√gm2`. Its
  positive root is the floor. The pair is stable at all only while
  `gm3 < gm2·(1 + CL/Cc1)`.

Think of `rnmc_min_gm2` as the fast first guess, and the full model
(`rnmc_pole_damping`, family E) as the check. `rnmc.py` (3.6) uses both.

**Docstring check.** Accurate but written for a control engineer. `rnmc_min_gm2`
says "positive root of the quadratic" without saying in words *what* is being
solved for ("the least `gm2` that keeps the ringing at ζ = 0.7"). Fine to keep
the maths, but lead with the plain sentence.

### Family E — Nodal loop models (lines 411–914)

This is the part that made the analytical GBW and PM agree with SPICE (#274,
PR #279). Instead of a rule of thumb, it builds the small-signal circuit as a
matrix and *measures* its open-loop gain on a frequency sweep, the way ngspice's
`.ac` analysis would, but with a much simpler device model
([nodal analysis](00_background.md#nodal-analysis)).

The main path for a two-stage amplifier, top to bottom:

```
two_stage_loop(gm1, gm2, Cc, CL, c1, c2, g1, g2, buffer, r, cmfb)     line 625
  └ _floor_g                 keep every output conductance > 0           line 503
  └ _miller_network(half,…)  one half (SE) or two coupled halves (FD)     line 558
      └ _two_stage_half      input stage → inverting stage → Cc branch → load   line 532
          └ _Network         the matrix builder (stamps)                  line 411
  └ _loop_gbw_pm(G, C, b, out, w_t)                                       line 597
      └ _poles               eigenvalues of −C⁻¹G                         line 711
      └ _crossings           every 0 dB crossing, refined                 line 882
          └ _sweep           4000-point log sweep                         line 871
              └ _open_loop_gain  solve (G + jωC)v = b at every ω          line 740
      └ _has_rhp_pole        any pole growing instead of decaying?        line 728
```

**Walkthrough.**

1. **Build the circuit, one element at a time.**
   [`_Network`](../../../circuitgenome/sizer/physics/equations.py#L411) keeps
   lists of `(row, col, value)` entries for `G` and `C` and a dict for `b`.
   [`_stamp`](../../../circuitgenome/sizer/physics/equations.py#L426) is the
   conductance/capacitor [stamp](#stamping); `None` as a node means ground.
   The element methods are the op-amp vocabulary:
   - [`input_stage(gm, g, c)`](../../../circuitgenome/sizer/physics/equations.py#L443):
     a new node with `b = −gm`. The input is a 1 V test signal (`Vin = 1`), so
     the input pair pushes `gm·1` amps out of its output node. The node gets its
     output conductance `g` and parasitic `c` to ground.
   - [`stage(ctrl, gm, g, c, inverting, mirror_pole_hz)`](../../../circuitgenome/sizer/physics/equations.py#L451):
     a [common-source stage](00_background.md#common-source): a VCCS
     `gm·v[ctrl]` into a new node with `g` and `c`. A non-inverting stage
     (one that goes through a current mirror) gets an extra mirror node first,
     a unity-gain buffer with one pole.
   - [`branch(i, j, c, r)`](../../../circuitgenome/sizer/physics/equations.py#L467):
     a compensation cap from `i` to `j`. With a series resistor `r` it adds a
     middle node: cap from `i` to the middle, `1/r` from the middle to `j`.
   - [`load(v, cl_f, buffer)`](../../../circuitgenome/sizer/physics/equations.py#L476):
     `CL` on `v`, or a [source follower](00_background.md#source-follower)
     whose output carries `CL`. The follower is a VCCS `gm_f·v` into its
     output node, which has conductance `gm_f + g_f`: its DC gain is
     `gm_f/(gm_f + g_f)`, a bit under 1.
   - [`matrices()`](../../../circuitgenome/sizer/physics/equations.py#L490):
     sum the entries into dense numpy arrays `(G, C, b)`.
2. **Assemble a half circuit.**
   [`_two_stage_half`](../../../circuitgenome/sizer/physics/equations.py#L532)
   is four lines:

   ```python
   v1 = net.input_stage(drive, g1, c1_f)        # first-stage output node
   v2 = net.stage(v1, gm2, g2, c2_f)            # inverting second stage
   net.branch(v1, v2, cc_f, r_ohm)              # Miller cap (+ nulling R)
   return v1, net.load(v2, cl_f, buffer)        # CL, or follower then CL
   ```

   That *is* the [Miller-compensated two-stage op-amp](00_background.md#miller),
   drawn as code. [`_nmc_half`](../../../circuitgenome/sizer/physics/equations.py#L544)
   (nested Miller) and [`_rnmc_network`](../../../circuitgenome/sizer/physics/equations.py#L510)
   (reversed nested Miller) are the same with a third stage and the two caps
   wired as in [nested Miller / RNMC](00_background.md#nested-miller).
3. **Single-ended or fully differential.**
   [`_miller_network`](../../../circuitgenome/sizer/physics/equations.py#L558)
   builds one half for a single-ended amplifier. For an FD one it builds two
   halves driven by `+gm1/2` and `−gm1/2` and reads the output as their
   difference, `out = (p, n)`. When the two halves' compensation differs, they
   are coupled through the [CMFB](00_background.md#cmfb): an extra node holds
   the outputs' average (behind one pole), and a VCCS `gm_c` feeds it back into
   both first-stage nodes. Matched halves give exactly the single-half answer.
4. **Floor the conductances.**
   [`_floor_g`](../../../circuitgenome/sizer/physics/equations.py#L503) raises
   any output conductance to at least `1e-9·gm`. A zero conductance (an ideal
   current source) would make the DC gain infinite and the matrix singular.
   The floor changes nothing near the crossing.
5. **Find the poles.**
   [`_poles`](../../../circuitgenome/sizer/physics/equations.py#L711) returns
   the [eigenvalues](#eigen-poles) of `−C⁻¹G`. If `C` is singular (the middle
   node of a cap-plus-resistor branch has no capacitance to ground), it adds a
   tiny `1e-9 × max` capacitance to every node, which parks that pole far
   above everything else.
6. **Sweep the frequency.**
   [`_sweep`](../../../circuitgenome/sizer/physics/equations.py#L871) picks
   4,000 log-spaced frequencies from two decades below the slowest pole to two
   decades above the fastest (and the nominal crossover `gm1/Cc`).
   [`_open_loop_gain`](../../../circuitgenome/sizer/physics/equations.py#L740)
   solves `(G + jωC)·v = b` at all of them in one batched `np.linalg.solve`,
   reads `v[out]`, and flips the sign so the DC gain is positive. The sweep
   returns `(ω, |A| in dB, phase in degrees)`.
7. **Find every 0 dB crossing.**
   [`_crossings`](../../../circuitgenome/sizer/physics/equations.py#L882) looks
   for sign changes in `|A|` (dB), refines each with three rounds of
   [false position](#false-position) in `(log ω, dB)`, and reads the phase at
   the refined point. Each crossing gives `(ω, PM = 180° + phase)`.
8. **Turn crossings into GBW and PM.**
   [`_loop_gbw_pm`](../../../circuitgenome/sizer/physics/equations.py#L597):
   - no crossing → `(None, None)`: the gain never reaches 1;
   - an RHP pole ([`_has_rhp_pole`](../../../circuitgenome/sizer/physics/equations.py#L728))
     → PM = `0.0`: the loop oscillates whatever the phase says;
   - otherwise **GBW = the first crossing** (what the AC bench reports) and
     **PM = the worst margin** over every crossing *and* every "return". A
     return is a stretch past the first crossing where the gain, having dipped
     more than [`_NEAR_CROSSING_DB = 0.5`](../../../circuitgenome/sizer/physics/equations.py#L594)
     dB below 0 dB, climbs back within 0.5 dB of it. A lightly damped pole pair
     can do that, and SPICE may see it poke just above 0 dB, a second crossing
     with much less margin. Taking the worst one is the
     [phase-margin](00_background.md#phase-margin) lesson from #269.
9. **The public entry points.**
   [`two_stage_loop`](../../../circuitgenome/sizer/physics/equations.py#L625)
   and [`nested_miller_loop`](../../../circuitgenome/sizer/physics/equations.py#L672)
   return `(GBW Hz, PM °)` through steps 1–8. The RNMC trio is the same machine
   with its own network:
   [`rnmc_pole_damping`](../../../circuitgenome/sizer/physics/equations.py#L752)
   (worst `ζ = −Re(p)/|p|` over the non-dominant poles),
   [`phase_margin_rnmc_deg`](../../../circuitgenome/sizer/physics/equations.py#L782)
   and [`unity_gain_bw_rnmc_hz`](../../../circuitgenome/sizer/physics/equations.py#L846).
   The caller is `metrics._miller_loop`
   ([`metrics.py:58`](../../../circuitgenome/sizer/physics/metrics.py#L58)),
   which pulls `gm`, `g` and node caps out of the stage chain (3.9–3.10).

**Running example.** For `circuit_0010` the final call is

```
two_stage_loop(gm1 = 60 µS, gm2 = 575 µS, Cc = 1.25 pF, CL = 5 pF,
               c1 = 23.3 fF, c2 = 0, g1 = 0.861 µS, g2 = 3.36 µS)
```

(`gm1` is 60 µS rather than the 15.7 µS the GBW spec asks for; 3.4 explains
why.) The network has two nodes, `v1` = `net_mid` (first-stage output) and
`v2` = `out`:

```
        v1                         v2
G = [ g1  = 0.861 µS          0              ]   KCL at net_mid
    [ gm2 = 575 µS            g2 = 3.36 µS   ]   KCL at out: gm2·v1 + g2·v2 + …
C = [ c1 + Cc = 1.273 pF     −Cc = −1.25 pF  ]
    [ −Cc     = −1.25 pF     Cc + CL = 6.25 pF ]
b = [ −gm1 = −60 µS, 0 ]
```

What the model finds, against the closed forms and SPICE:

| | Value | Compare |
|---|---|---|
| Poles | 632 Hz (dominant), 18.1 MHz (output) | `gm2/(2π·CL)` = 18.3 MHz |
| DC gain | 11,926 = **81.53 dB** | `(gm1/g1)·(gm2/g2)`, exactly; the reported `gain_db` |
| First 0 dB crossing (GBW) | **7.06 MHz** | closed form `gm1/(2π·Cc)` = 7.64 MHz; SPICE 6.93 MHz |
| PM at that crossing | **63.2°** | closed form 67.3°; SPICE 63.5° |

The output pole at 18 MHz is only 2.6× above the crossing. It pulls the real
crossing about 8 % under `gm1/(2π·Cc)` and costs a few extra degrees. The
model also sees the [RHP zero](00_background.md#rhp-zero) (at
`gm2/(2π·Cc)` = 73 MHz) for free, because `Cc` couples the two rows of `C`.
The closed forms see neither. This is one of the "which code made it right"
answers from the README: GBW is now within 2 % of SPICE and PM within 0.3°.

For contrast, adding the nulling resistor `compensation_resistor_ohm(Cc, CL,
gm2)` = 8.7 kΩ (`circuit_0010` has none) gives 7.54 MHz and 89.6°: the zero
cancels the output pole, as `test_a_nulling_resistor_cancels_the_output_pole`
expects.

**Docstring check.** This family needs the most help for a layman.

- `_Network` and its methods use "VCCS", "drawn out of node", "Vin = 1"
  without defining them. One sentence on the sign convention (row `i` of
  `G·v = b` is "current leaving node `i`") would unlock the whole class.
- `_loop_gbw_pm`'s "return toward 0 dB" is the subtlest idea in the file. Its
  docstring is correct but needs a picture-in-words ("a ringing pole pair can
  push the gain back up to almost 0 dB; SPICE may see that bump as a second
  crossing").
- `_crossings` says "false position" without naming what it is.
- `two_stage_loop` and `phase_margin_rnmc_deg` are good: they say what the
  full model sees that the closed form misses.

### Family F — Slew, power, CMRR, PSRR (lines 917–974)

**Formulas** ([slew](00_background.md#slew), [power](00_background.md#power),
[CMRR](00_background.md#cmrr), [PSRR](00_background.md#psrr)):

```
SR    = I / C                              slew_rate_vps(),   line 917
P     = (Vdd − Vss)·Σ|I_supply|            quiescent_power(), line 933
CMRR  ≈ gm1 / (2·gd_tail)    (in dB)       cmrr_db(),         line 946
PSRR+ ≈ gm2 / gd_bias        (in dB)       psrr_db_approx(),  line 960
```

All one-liners. The hard part of each lives elsewhere: *which* nodes slew
(`metrics._slew_rate`), *which* currents come from the supply
(`stage_chain.vdd_supply_currents`), and the nodal CMRR/PSRR that replaces
these closed forms on most single-ended circuits (`stage_chain.py`, 3.9).

**Docstring check.** Clear. One nit: `psrr_db_approx` writes the formula as
`gm2 / gd_bias_mirror` but the parameter is `gd_bias`, and "PSRR+" (rejection
of the *positive* supply) is not defined.

**Test to read.**

- [`test_sizer.py::test_open_loop_gain_ceiling_follows_predicted_gbw`](../../../tests/test_sizer.py#L190):
  the measurable ceiling is 160 dB at 100 kHz, 180 dB at 1 MHz, capped near
  212 dB.
- [`test_sizer.py::test_phase_margin_formula`](../../../tests/test_sizer.py#L242)
  and [`::test_single_stage_phase_margin_formula`](../../../tests/test_sizer.py#L254):
  the closed forms, including "GBW on the mirror pole costs exactly 45°".
- [`test_stage_chain.py::test_a_bare_miller_cap_pulls_the_crossing_under_its_output_pole`](../../../tests/test_stage_chain.py#L104):
  at ~55° PM the full model's crossing is 10–20 % under `gm1/(2π·Cc)`, as
  SPICE measured.
- [`test_stage_chain.py::test_a_nulling_resistor_cancels_the_output_pole`](../../../tests/test_stage_chain.py#L112):
  with `R = (Cc+CL)/(gm2·Cc)`, PM > 85° and GBW within 2 % of the closed form.
- [`test_stage_chain.py::test_a_return_toward_0_db_counts_as_a_crossing`](../../../tests/test_stage_chain.py#L136):
  a real gf180 NMC case where the gain comes back to −0.3 dB; the margin is
  read there (40–50°), not at the first crossing (~98°).
- [`test_stage_chain.py::test_the_cmfb_couples_mismatched_halves_only`](../../../tests/test_stage_chain.py#L164):
  matched FD halves ignore the CMFB; mismatched ones are pulled between their
  two margins.
- [`test_rnmc.py::test_round_off_on_the_dominant_pole_is_not_a_rhp_pole`](../../../tests/test_rnmc.py#L72)
  and [`::test_second_zero_db_crossing_is_a_negative_margin`](../../../tests/test_rnmc.py#L88):
  the RHP-pole test ignores round-off noise; a second crossing past −180°
  reads as a negative margin.

**Check yourself.**

1. In `_two_stage_half`, which single line makes this a *Miller* amplifier,
   and what would the model compute without it?
2. Why does `_loop_gbw_pm` report the *first* crossing as GBW but the *worst*
   one as PM?
3. Why does `_floor_g` exist? What would `_poles` and `_open_loop_gain` do
   with an exactly zero output conductance?

<details><summary>Answers</summary>

1. `net.branch(v1, v2, cc_f, r_ohm)`, the cap from the second stage's input
   to its output. Without it the two high-resistance nodes each make a
   low-frequency pole and nothing splits them: two close poles, a crossing
   with close to 180° of lag, and next to no phase margin.
2. GBW is defined as where the gain first drops to 1, and that is what the AC
   bench reports. Stability, though, is lost at *any* crossing with too little
   margin, so the safe answer is the minimum (#269).
3. With `g = 0` the DC gain is infinite: `G` is singular, so the DC solve in
   `_open_loop_gain` (`np.linalg.solve(G, b)`, used for the sign) fails, and
   the dominant pole sits at exactly 0. Flooring at `1e-9·gm` keeps everything
   finite while moving nothing near the crossing.

</details>

---

## 3.3 `gmid_lut.py` (161 lines)

**Job in one sentence.** It loads one technology's measured gm/Id table and
answers "what does a transistor at this gm/Id and this L look like?" by
[bilinear interpolation](#bilinear).

**Design knowledge it encodes.**

- [The gm/Id method](00_background.md#gmid): gm/Id and L fix a device's
  current density, intrinsic gain, speed, `Vdsat` and `Vgs`, whatever its
  width.
- [Inversion levels](00_background.md#inversion): the gm/Id axis runs from
  strong inversion (low gm/Id) to weak inversion (high gm/Id).
- [ft](00_background.md#ft), [Vdsat](00_background.md#vdsat),
  [rout](00_background.md#rout) (as `gm/gds`), [body effect](00_background.md#vth-body)
  (as `gmb/gm`).
- The caveat from the primer: the table is measured at `Vds = Vdd/2` and
  `Vsb = 0`. Two optional correction tables cover devices far from that point
  (#273).

**Walkthrough.**

1. [`__init__`](../../../circuitgenome/sizer/physics/gmid_lut.py#L50) loads a
   `*_gmid.npz` (produced by `tools/extract_tech.py --gm-id`). It reads two
   axes, `gm_id_axis` (1/V) and `l_axis` (µm), and five fields per polarity
   ([`_FIELDS`](../../../circuitgenome/sizer/physics/gmid_lut.py#L37)):
   `id_w` (A/µm), `gm_gds`, `ft` (Hz), `vdsat` (V), `vgs` (V, magnitude).
   Each field is a 2-D array shaped `(n_L, n_gmid)`.
   [Line 59](../../../circuitgenome/sizer/physics/gmid_lut.py#L59) also loads
   the optional bias tables when the file has their axes: `gds_scale` over a
   `vds_axis`, and `gmb_gm` over a `vsb_axis`. Each is a stack of the same 2-D
   grids, one per bias value.
2. [`_row`](../../../circuitgenome/sizer/physics/gmid_lut.py#L67): step 1 of
   the interpolation. Clamp `L` into the axis, find the two bracketing rows,
   blend them.
3. [`_lookup`](../../../circuitgenome/sizer/physics/gmid_lut.py#L85): step 2.
   Clamp gm/Id into the axis and `np.interp` along the blended row. Every
   public read (`id_per_w`, `gm_gds`, `ft`, `vdsat`, `vgs`, lines
   [111–145](../../../circuitgenome/sizer/physics/gmid_lut.py#L111)) is one
   call to `_lookup` with a field name.
4. [`_bias_lookup`](../../../circuitgenome/sizer/physics/gmid_lut.py#L91): the
   same bilinear blend on every bias layer, then a third interpolation along
   the bias axis. For `gds_scale` that last step is in log space (see
   [bilinear](#bilinear)). It returns `None` when the table has no such axis.
   - [`gds_scale(dtype, gm_id, L, vds)`](../../../circuitgenome/sizer/physics/gmid_lut.py#L119):
     "how many times the table's gds is this device's gds, at its own `Vds`".
     It is 1 at `Vdd/2` and rises steeply toward the saturation knee, which is
     where a device under a [cascode](00_background.md#cascode) sits. It
     returns 1.0 without the table.
   - [`gmb_gm(dtype, gm_id, L, vsb)`](../../../circuitgenome/sizer/physics/gmid_lut.py#L129):
     body transconductance as a fraction of gm. It returns 0.0 without the
     table.
5. [`max_gm_id`](../../../circuitgenome/sizer/physics/gmid_lut.py#L147): the
   last gm/Id on the axis, the table's weak-inversion ceiling. (It ignores its
   `dtype` and `l_um` arguments.)
6. [`gm_id_from_idw`](../../../circuitgenome/sizer/physics/gmid_lut.py#L151):
   the **inverse** read. Given a solved `(W, L, Id)`, compute `Id/W` and find
   the gm/Id that gives it. `id_w` falls as gm/Id rises, so the code reverses
   the row to make it ascending (which `np.interp` needs) and interpolates the
   other way. This is how every metric is later evaluated on the *sized*
   circuit (3.4, `_gm_id_at`).

**Running example.** The gf180 table
(`circuitgenome/sizer/config/models/gf180mcu_gmid.npz`):

- `gm_id_axis`: 6 → 24 /V in steps of 0.5 (37 columns). So gm/Id = 6 is the
  strongest inversion it knows and 24 the weakest.
- `l_axis`: 0.28, 0.38, 0.5, 0.68, 0.92, 1.22, 1.64, 2.22, 2.98, 4.0 µm (10
  rows).
- `vds_axis`: 0.099 → 2.97 V (26 points); `vsb_axis`: 0 → 1.98 V (7 points).

Reads at gm/Id = 10, the current-source value, at the two lengths
`circuit_0010` uses:

| Device | L (µm) | Id/W (µA/µm) | \|Vgs\| (V) | Vdsat (V) | gm/gds | ft |
|---|---|---|---|---|---|---|
| NMOS | 0.56 | 4.012 | 0.841 | 0.168 | 212 | 4.6 GHz |
| NMOS | 1.12 | 2.049 | 0.832 | 0.171 | 411 | 1.16 GHz |
| PMOS | 0.56 | 1.126 | 0.945 | 0.169 | 320 | 1.05 GHz |
| PMOS | 1.12 | 0.475 | 0.942 | 0.154 | 717 | 222 MHz |

Read it as the primer says. Doubling L roughly halves the current density and
doubles the intrinsic gain (a better [current source](00_background.md#rout))
but cuts `ft` by about 4× (slower). PMOS carries 3.5–4.3× less current per
µm than NMOS ([NMOS / PMOS](00_background.md#nmos-pmos)).

Neither 0.56 nor 1.12 is a grid row, so each read is interpolated. For NMOS
`id_w` at gm/Id = 10: row `L = 0.5` gives 4.378, row `L = 0.68` gives 3.279.
0.56 is one third of the way, so `4.378 − (4.378 − 3.279)/3 = 4.012`.
(gm/Id = 10 lies on a column, so only the L step blends.)

The correction tables are not small effects. At gm/Id = 10, L = 0.56 and
`Vds` = 0.2 V (just above the knee), `gds_scale` is 16.6 for NMOS and 21.7 for
PMOS: such a device leaks 17–22 times what the table's `Vdd/2` value says. At
`Vsb` = 0.5 V, `gmb_gm` is 0.29 (NMOS) and 0.35 (PMOS).

**Test to read.** [`tests/test_gmid_lut.py`](../../../tests/test_gmid_lut.py)
(it uses the ptm45 table, not gf180):

- [`test_idw_gmid_round_trip`](../../../tests/test_gmid_lut.py#L23):
  `gm_id_from_idw(id_per_w(g, L), L)` recovers `g` within 0.3 /V.
- [`test_idw_decreases_with_gm_id`](../../../tests/test_gmid_lut.py#L32) and
  [`test_intrinsic_gain_rises_with_length`](../../../tests/test_gmid_lut.py#L39):
  the two physical trends the inverse and the L policy rely on.
- [`test_off_grid_length_interpolates`](../../../tests/test_gmid_lut.py#L50):
  a value between two L rows lies between them.
- [`test_gds_scale_is_one_at_the_characterisation_point`](../../../tests/test_gmid_lut.py#L78),
  [`test_gds_rises_toward_the_saturation_knee`](../../../tests/test_gmid_lut.py#L84)
  (> 3× near the knee),
  [`test_gmb_gm_falls_with_reverse_body_bias`](../../../tests/test_gmid_lut.py#L93).
- [`test_table_without_bias_axes_reads_single_point`](../../../tests/test_gmid_lut.py#L98):
  an older `.npz` reads `gds_scale = 1`, `gmb_gm = 0`.

**Check yourself.**

1. A device at gm/Id = 3 is requested on gf180. What does `id_per_w` return,
   and why might that surprise a caller?
2. Why does `gm_id_from_idw` reverse the curve before calling `np.interp`?
3. Why is `gds_scale` blended in log space but `gmb_gm` linearly?

<details><summary>Answers</summary>

1. The value at gm/Id = 6, the axis edge. `_lookup` clamps instead of
   extrapolating. A caller that solved gm/Id = 3 would get a W for gm/Id = 6,
   so the device delivers 2× the gm it asked for. `GmIdModel` makes this
   explicit with its own floor and `realized_gm` (3.4).
2. `np.interp` needs ascending x values. `id_w` falls as gm/Id rises, so the
   reversed row is ascending in current density.
3. `gds_scale` spans decades (1× to 20×) and is convex toward the knee; a
   linear blend between a 16× and a 4× point would overshoot the middle.
   `gmb_gm` stays between 0 and 1 and varies gently.

</details>

---

## 3.4 `device_model.py` (403 lines)

**Job in one sentence.** One interface (`DeviceModel`) for "give me gm, gds,
Vgs, Vdsat and the caps of this device", with two backends behind it: the
square law (`Level1Model`) and the LUT (`GmIdModel`). The LUT backend also
holds the gm/Id **forward recipe**, `geometry_for`.

**Design knowledge it encodes.**

- [Search vs forward pass](00_background.md#two-strategies): the topology math
  is shared; only the device primitives differ.
- [Square law](00_background.md#square-law) vs [gm/Id](00_background.md#gmid).
- [Inversion levels](00_background.md#inversion) and [rout](00_background.md#rout):
  the role policy (signal / current source / cascode) picks a gm/Id region and
  an L.
- [ft](00_background.md#ft) and [parasitic caps](00_background.md#parasitic-caps):
  gate capacitance from `gm/(2π·ft)`.
- [Body effect](00_background.md#vth-body) and the [cascode](00_background.md#cascode)
  `gm + gmb` boost.

**Walkthrough.**

1. **Three roles.** [`device_model.py:34–36`](../../../circuitgenome/sizer/physics/device_model.py#L34):
   `SIGNAL` (a device whose gm matters: input pair, gain-stage driver),
   `CURRENT_SOURCE` (tail, loads, bias), `CASCODE`. Roles are plain strings.
   Stop 4's intent registry assigns one to every device.
2. **The interface.** [`DeviceModel`](../../../circuitgenome/sizer/physics/device_model.py#L39)
   is a `typing.Protocol` (an interface checked by shape, not inheritance).
   Its methods and the two answers:

   | Method | `Level1Model` (square law) | `GmIdModel` (LUT) |
   |---|---|---|
   | `gm(dtype, W, L, Id)` | `√(2µCox·W/L·Id)` | `(gm/Id)·Id`, gm/Id recovered from `Id/W` |
   | `gds(…, vds=None)` | `λ·Id`, ignores `vds` | `gm / (gm/gds)`, scaled by `gds_scale` at `vds` |
   | `gmb(…, vsb)` | `0.0` | `gm·gmb_gm(vsb)` |
   | `vds_sat(…)` | `√(2·Id·L/(µCox·W))` | LUT `vdsat` |
   | `cgs(W, L)` | `(2/3)·W·L·Cox` | same (from `cox`) |
   | `cgg(W, L, Id)` | `cgs` | `gm/(2π·ft)` from the LUT |
   | `cdb(W)` | `W·cdb` | same |
   | `vgs(…)` | `Vth + Vov`, signed | LUT `vgs`, signed |
   | `body_vth_shift(vsb)` | `equations.body_vth_shift` | same |
   | `gm_ceiling(Id, L)` | `25·Id` | `24·Id` on gf180 (table's last gm/Id) |
   | `gds_estimate(Id, role)` | `λ·Id` | gds at the role's nominal gm/Id and L |
   | `realized_gm(gm_req, Id)` | gm after CP-SAT rounds W up to the grid | `max(gm_req, 6·Id)` on gf180 |

   The last two exist because requirements are derived *before* geometry
   exists (3.7). `gds_estimate` guesses gds from the role alone.
   `realized_gm` says what gm the geometry step will really deliver, so a
   downstream requirement (gm2 for phase margin depends on gm1) uses the gm1
   that will be built, not the one that was asked for.
3. **`Level1Model`** ([line 142](../../../circuitgenome/sizer/physics/device_model.py#L142))
   is a thin wrapper that forwards each method to family A of `equations.py`
   with the tech's `mu_cox`, `lam`, `vth`, `cox`. Its only real logic is
   [`realized_gm`](../../../circuitgenome/sizer/physics/device_model.py#L196):
   the analytical sizer's CP-SAT (constraint-programming) solver picks W in
   integer grid steps, so it overshoots the requested gm. The method computes
   that overshoot in advance. Built in
   [`analytical/level1.py:67`](../../../circuitgenome/sizer/analytical/level1.py#L67)
   (Stop 5).
4. **`GmIdPolicy`** ([line 216](../../../circuitgenome/sizer/physics/device_model.py#L216)):
   the L multiples and nominal gm/Id per role. Signal L = 2 × Lmin, current
   source 4 × Lmin at gm/Id 10, cascode 3 × Lmin at gm/Id 8, signal nominal
   gm/Id 14 (for `gds_estimate` only). In practice the defaults are always
   overwritten: [`gmid/plan.py:95`](../../../circuitgenome/sizer/gmid/plan.py#L95)
   builds the policy from the intent registry (same numbers today).
5. **Reading a sized device.** Every "geometry known" method of `GmIdModel`
   starts with [`_gm_id_at`](../../../circuitgenome/sizer/physics/device_model.py#L273):
   `Id/W` → `lut.gm_id_from_idw` → the device's actual gm/Id. Everything else
   follows from that one number and L:
   - [`gm`](../../../circuitgenome/sizer/physics/device_model.py#L280) = gm/Id × Id;
   - [`gds`](../../../circuitgenome/sizer/physics/device_model.py#L284) = gm ÷
     LUT `gm_gds`. When the caller knows the device's own `Vds`
     (from [node levels](#38-node_levelspy-141-lines)), it multiplies by
     `gds_scale(vds)`. `vds` is first raised to at least `Vdsat`: below it the
     device would be in [triode](00_background.md#regions), meaning the level
     estimate itself has failed, so the code reads at the knee instead of
     extrapolating;
   - [`cgg`](../../../circuitgenome/sizer/physics/device_model.py#L321) =
     `gm/(2π·ft)`. This is the simulator's own gate capacitance at that
     inversion level. A wide device in weak inversion carries much less than
     `(2/3)·W·L·Cox` (#274);
   - [`vgs`](../../../circuitgenome/sizer/physics/device_model.py#L335): the
     LUT magnitude, made negative for PMOS.
6. **The L policy.** [`role_length`](../../../circuitgenome/sizer/physics/device_model.py#L258)
   picks the role's multiple; [`length_for`](../../../circuitgenome/sizer/physics/device_model.py#L264)
   multiplies by `length.min` and snaps to the grid. On gf180
   (Lmin = 0.28 µm, step 0.02) that gives signal 0.56, cascode 0.84, current
   source 1.12 µm. Longer L for current sources is the
   [rout](00_background.md#rout) rule: longer valve body, more stubborn.
7. **The forward recipe**, [`geometry_for`](../../../circuitgenome/sizer/physics/device_model.py#L368).
   This is the primer's [gm/Id steps 3–5](00_background.md#gmid), in 15 lines
   ([389–403](../../../circuitgenome/sizer/physics/device_model.py#L389)):

   ```python
   l_um = l_um if l_um is not None else self.role_length(role)    # L by policy
   if role != SIGNAL or not gm_target or ids <= 0:
       gm_id = gm_id if gm_id is not None else self._role_gm_id(role)  # fixed region
   else:
       gm_id = gm_target / abs(ids)                                # solved from spec
       ceiling = self.lut.max_gm_id(dtype, l_um)
       if gm_id > ceiling: gm_id, capped = ceiling, True           # weak-inversion wall
       gm_id = max(gm_id, float(self.lut.gm_id_axis[0]))           # table floor
   if gm_id_min is not None: gm_id = max(gm_id, gm_id_min)         # swing floor
   w_um = abs(ids) / self.lut.id_per_w(dtype, gm_id, l_um)         # W = Id / (Id/W)
   ```

   - A signal device with a gm target gets `gm/Id = gm_target / Id`. If that
     exceeds the table's weak-inversion end, it is clamped and `capped` is set
     (the caller turns that into a "will fall short" warning). If it is below
     the table's start, it is raised to the start, which over-delivers gm.
   - Every other device uses its role's region (or a per-device override from
     the intent registry).
   - `gm_id_min` raises gm/Id further for an output device whose `Vdsat` must
     fit the [swing](00_background.md#swing) budget. Weaker inversion means a
     smaller `Vdsat`, and gm only grows, so this is spec-safe.
   - W is **not** snapped here. `gmid/geometry.py` snaps it (Stop 4).

   Helpers: [`_role_gm_id`](../../../circuitgenome/sizer/physics/device_model.py#L268)
   (cascode → 8, anything else → the current-source 10),
   [`_params`](../../../circuitgenome/sizer/physics/device_model.py#L134)
   (NMOS or PMOS parameter block).

**Running example.** Every `geometry_for` call while sizing `circuit_0010`
(captured by wrapping the method; `gm/Id` in 1/V):

| Device | Type | Id | Role | gm target | L | gm/Id used | Id/W (µA/µm) | W (µm) |
|---|---|---|---|---|---|---|---|---|
| `m1/m2_input_pair` | PMOS | 10 µA | signal | 15.7 µS | 0.56 | **6.0** (floor) | 3.256 | 3.7 |
| `m1/m2_load` | NMOS | 10 µA | signal | none | 0.56 | 10 | 4.012 | 2.49 |
| `m1/m2_tail_current` | PMOS | 20 µA | current source | — | 1.12 | 10 | 0.475 | 42.09 |
| `mn1_second_stage` | NMOS | 50 µA | signal | 416 µS, then 575 µS | 0.56 | **11.5** (swing floor) | 2.800 | 17.86 |
| `mp1_second_stage` | PMOS | 50 µA | current source | — | 1.12 | 10 | 0.475 | 105.2 |
| bias gen NMOS ×3 | NMOS | 20 µA | current source | — | 1.12 | 10 | 2.049 | 9.76 |
| `mp5_bias_gen` | PMOS | 20 µA | current source | — | 1.12 | 10 | 0.475 | 42.09 |

The widths match the README table after snapping (3.7, 2.495, 42.085,
17.855, 105.21).

The input pair is the instructive row. The GBW spec asks for
`gm1 = 2π·GBW·Cc = 2π × 2 MHz × 1.25 pF = 15.7 µS`. At 10 µA that is
gm/Id = 1.57, far stronger inversion than the table goes. So the recipe floors
it at gm/Id = 6 and the pair delivers **60 µS**, 3.8× the request. That is
why the predicted GBW is 7.06 MHz against a 2 MHz spec, and it is exactly the
case `realized_gm`'s docstring cites ("gf180 at 10 µA: 60 µS for a 16 µS
ask"). The pair's `Vgs` = −1.083 V and `Vdsat` = 0.274 V are the gm/Id = 6
reads.

The second-stage driver shows the swing floor. Its gm target alone would give
gm/Id ≈ 8.3 (`Vdsat` 0.20 V). The 0.3 V swing spec leaves the output device a
`Vdsat` budget of half that, 0.15 V, and the first LUT column under it is
gm/Id = 11.5 (11.0 reads 0.152 V). The resulting `Vdsat` = 0.1446 V is exactly
the reported analytical `output_swing_min_v` of 0.14 V. The later re-plan
(Stop 4) re-solves the device at 575 µS, the gm it already has, so nothing
moves.

Two more reads on the sized `m1_input_pair` (W = 3.7, L = 0.56, 10 µA):
`cgg` = 5.37 fF (from `ft`) against `cgs` = 5.01 fF (from `Cox`). In strong
inversion and narrow, the two estimates agree, as the primer says they
should.

Note that the mirror load (`m1_load`) gets role `signal` but no gm target, so
it falls to `_role_gm_id`, the *current-source* value 10, at the *signal* L.

**Test to read.**

- [`test_gmid_lut.py::test_level1_model_matches_equations`](../../../tests/test_gmid_lut.py#L61):
  `Level1Model` returns exactly `equations.*` (a regression guard for the
  generic path).
- [`test_gmid_lut.py::test_gmid_model_reads_gds_no_deeper_than_vdsat`](../../../tests/test_gmid_lut.py#L110):
  `gds(vds=0)` equals `gds(vds=Vdsat)`, that knee value is above the table
  value, and `gds(vds=Vdd/2)` equals the table value.
- [`test_gmid_lut.py::test_gmid_gate_capacitance_follows_the_inversion_level`](../../../tests/test_gmid_lut.py#L133):
  a 100 µm PTM45 device in weak inversion reads `cgg` ≈ 34 fF, under 30 % of
  its `Cox` estimate; narrow ones converge.
- [`test_gmid_geometry.py::test_realized_gm_matches_geometry_floor`](../../../tests/test_gmid_geometry.py#L46):
  a request below the table floor is delivered at the floor, and
  `realized_gm` predicts the gm the geometry actually builds.
- [`test_gmid_geometry.py::test_over_ceiling_request_warns_and_clamps`](../../../tests/test_gmid_geometry.py#L221)
  and [`::test_swing_budget_floors_output_gm_id`](../../../tests/test_gmid_geometry.py#L242):
  the clamp and the swing floor, seen through `gmid/geometry.py`.

**Check yourself.**

1. Run `geometry_for` by hand for the tail: PMOS, 20 µA, current source. Which
   two numbers do you need from the LUT table in 3.3?
2. Why does the requirement step need `realized_gm` at all? Give the
   `circuit_0010` numbers.
3. `GmIdModel.gds` is called with `vds = 0.05 V` for a device whose `Vdsat`
   is 0.17 V. What does it read, and why not extrapolate?

<details><summary>Answers</summary>

1. Only `L` and `Id/W`. The role gives L = 4 × 0.28 = 1.12 µm and
   gm/Id = 10. The LUT gives PMOS `Id/W` = 0.475 µA/µm there, so
   `W = 20 / 0.475 = 42.1 µm`.
2. The gm2 needed for phase margin grows with the gm1 the pair really has
   (`PM ≈ 90° − atan(gm1·CL/(gm2·Cc))`). The GBW spec asks for 15.7 µS, but
   the geometry will build 60 µS. Sizing gm2 against 15.7 µS would leave the
   built amplifier short of phase margin.
3. It reads `gds_scale` at 0.17 V (the knee), not at 0.05 V. Below `Vdsat` the
   device would be in triode, which means the node-level estimate that
   produced `vds` (which assumes every device is saturated) is already wrong.
   The knee value keeps the predicted gain on the optimistic side instead of
   inventing a triode number.

</details>

---

## 3.5 `circuit_view.py` (216 lines)

**Job in one sentence.** Turn the functional-block recognizer's (FBR) slot
assignments into one read-only `CircuitView`: which transistors, resistors and
capacitors sit in which slot, each transistor listed once, plus warnings.

**Design knowledge it encodes.**
- The sizer trusts the FBR slots and the naming conventions
  ([pipeline layers](00_background.md#pipeline-layers)). It never re-discovers
  structure from scratch.
- A device can belong to two blocks. In `circuit_0010` the tail mirror's diode
  `m1_tail_current` is both the [current mirror](00_background.md#current-mirror)
  reference of the tail and a leg of the
  [bias generator](00_background.md#bias-generator). It must still get exactly
  one current and one size.

**Walkthrough.**
1. [`CircuitView`](../../../circuitgenome/sizer/physics/circuit_view.py#L23):
   a plain dataclass. `slot_transistors` / `slot_resistors` / `slot_capacitors`
   map slot name → devices. `all_transistors` maps `ref → (device, owning slot)`.
   `compensation_scheme` is copied from the template (`"nested_miller"`,
   `"reversed_nested_miller"` or `None`).
2. [`analyze_circuit`](../../../circuitgenome/sizer/physics/circuit_view.py#L198)
   is the only public entry point. It runs the steps below in a fixed order, so
   no caller can assemble them wrongly.
3. [`_extract_slot_transistors`](../../../circuitgenome/sizer/physics/circuit_view.py#L85)
   (and the resistor/capacitor twins at
   [L97](../../../circuitgenome/sizer/physics/circuit_view.py#L97) and
   [L109](../../../circuitgenome/sizer/physics/circuit_view.py#L109)) filter
   each FBR slot's devices by type.
4. **Orphan adoption**,
   [`_adopt_orphan_mosfets`](../../../circuitgenome/sizer/physics/circuit_view.py#L51).
   Pattern matching sometimes leaves a MOSFET in no slot (two identical
   diodes on one net, say). Such a device would be **unsized**: ngspice would
   simulate it at a default W/L. Synthesized netlists name every device
   `{ref}_{slot}`, so the code matches the ref's suffix against the slot names,
   longest first ([L72](../../../circuitgenome/sizer/physics/circuit_view.py#L72),
   so `second_stage_p` wins over `second_stage`). Each adoption is returned in
   `adopted`, and
   [`adoption_warnings`](../../../circuitgenome/sizer/physics/circuit_view.py#L174)
   turns the list into one advisory. Both sizers append it to
   `SizingResult.warnings`. Analogy: a lost parcel delivered by the address
   label because the sorting machine could not read the barcode. It arrives,
   but someone should fix the machine.
5. **Deduplication**,
   [`_deduplicate_devices`](../../../circuitgenome/sizer/physics/circuit_view.py#L148).
   Slots are visited in a fixed priority
   ([L158](../../../circuitgenome/sizer/physics/circuit_view.py#L158):
   `input_pair`, `load`, `tail_current`, the stage slots, `output_stage*`,
   `bias_gen`; anything else last), and the first slot seen owns the device.
   This is what keeps `m1_tail_current` a tail device rather than a bias-gen
   device.
6. **Topology mismatch**,
   [`_check_topology_match`](../../../circuitgenome/sizer/physics/circuit_view.py#L125).
   Every gain-stage slot of a real circuit holds one *signal* transistor
   (`is_signal_device`, from Stop 3.1). If a stage slot holds only bias devices,
   the netlist was probably sized against the wrong `--topology` (single-ended
   netlist, fully-differential template). Without this warning the gain, PM and
   PSRR metrics would just silently disappear.

The gm/Id sizer wraps this view in its own richer one:
[`gmid/analyze.py::analyze_circuit`](../../../circuitgenome/sizer/gmid/analyze.py#L172)
calls this function first, then adds typed blocks and cascode refs (Stop 4).

**Running example.** For `circuit_0010`: five slots (`input_pair`, `load`,
`tail_current`, `bias_gen`, `second_stage`), and `bias_gen` lists five MOSFETs
because it includes `m1_tail_current`. After deduplication `all_transistors`
has 12 entries and `m1_tail_current` is owned by `tail_current`.
`adopted = []`, `warnings = []`, `compensation_scheme = None` (two-stage).

**Test to read.**
- [`test_circuit_view.py`](../../../tests/test_circuit_view.py#L10)
  `test_adoption_warning_names_every_device_and_its_slot` and
  `test_adoption_warning_groups_by_slot`: the advisory names every adopted ref,
  sorted, grouped by slot. That is the only direct test of this file; adoption,
  dedup priority and the mismatch check are only covered end to end.

**Check yourself.**
1. Why is an orphan MOSFET dangerous if nobody adopts it?
2. A device sits in both `tail_current` and `bias_gen`. Which current will
   `assign_ids` give it, and why?
3. What does the mismatch check look for, and what would you lose without it?

<details><summary>Answers</summary>

1. Nobody gives it a W/L, so the deck keeps the simulator's default size and
   the circuit is simulated with a random-sized device in it.
2. The `tail_current` one: `_deduplicate_devices` visits `tail_current` before
   `bias_gen`, so that slot owns the ref, and `assign_ids` reads the owning slot.
   Both slots happen to be in `FULL_BIAS_SLOTS` (`ibias`), so the answer is the
   same here, but ownership also decides gm requirements.
3. A stage slot with no signal device. Without it, sizing a netlist against the
   wrong template would silently drop gain, PM and PSRR instead of saying why.

</details>

## 3.6 `rnmc.py` (277 lines)

**Job in one sentence.** Pick `(gm2, Cc2, gm3)` for a three-stage
reversed-nested-Miller amplifier so its inner pole pair is well damped and its
phase margin meets the spec.

**Design knowledge it encodes.**
- [RNMC](00_background.md#nested-miller): both caps start at the first-stage
  output. The two non-dominant poles become a *pair* that can ring, with
  damping ζ. The pair is only stable while `gm3 < gm2 · (1 + CL/Cc1)`.
- [Phase margin](00_background.md#phase-margin) read off a full nodal model,
  worst over every 0 dB crossing.
- The [nulling resistor](00_background.md#rhp-zero) in series with a
  compensation cap.

You have a pending review of PR #249, which introduced this file. The long
explanation (transfer functions, what ζ means, how the sizer builds
`(G + sC)·v = b` and finds the poles) is
[`docs/theory/rnmc_stability.rst`](../../../docs/theory/rnmc_stability.rst);
read it next to this section. Its sibling
[`rnmc_buffered_oscillation.rst`](../../../docs/theory/rnmc_buffered_oscillation.rst)
covers the buffered variant (#266).

**Walkthrough.**
1. Constants ([L45–L61](../../../circuitgenome/sizer/physics/rnmc.py#L45)):
   `ZETA_TARGET = 0.7` ("roughly critical": one smooth settle),
   `ZETA_FLOOR = 0.5` (still settles, ~16 % overshoot), the Cc2 multipliers
   `(1, 1.5, 2, 3, 4)` capped at Cc1, gm2 rising ×1.15 per step, gm3 falling
   ×0.85 per step.
2. [`design_rnmc`](../../../circuitgenome/sizer/physics/rnmc.py#L84). Analogy:
   tuning a car's suspension with three knobs, cheapest first.
   - `score(gm2, cc2, gm3)` ([L146](../../../circuitgenome/sizer/physics/rnmc.py#L146))
     asks the full model for ζ and PM and marks the choice `met` when
     `ζ ≥ 0.7` and `PM ≥ spec`.
   - If the starting point already meets both, it is returned unchanged
     ([L156](../../../circuitgenome/sizer/physics/rnmc.py#L156)).
   - Otherwise the loops at [L173–L189](../../../circuitgenome/sizer/physics/rnmc.py#L173)
     fix the **knob order**: for each gm3 (starting value first, then lower),
     for each Cc2 (starting value first, then larger), raise gm2 from the
     closed-form damping floor (`eq.rnmc_min_gm2`) up to its ceiling. The first
     `met` choice wins. So gm2 moves first, Cc2 second, and gm3 (which costs
     DC gain) only when nothing else works.
   - Nothing met: prefer the phase margin (which SPICE verifies) over the
     damping heuristic, among choices with `ζ ≥ 0.5`
     ([L191–L197](../../../circuitgenome/sizer/physics/rnmc.py#L191)).
3. [`comp_resistances`](../../../circuitgenome/sizer/physics/rnmc.py#L200):
   the series resistors `(R1, R2)` each half would get, sized like
   `size_resistors` sizes them (`eq.compensation_resistor_ohm`, against gm3).
   Returns one entry per *distinct* half; a bare cap gets 0 Ω.
4. [`worst_half`](../../../circuitgenome/sizer/physics/rnmc.py#L220): ζ and PM
   of the least stable half. A fully-differential amp whose `_p` and `_n`
   halves are compensated differently is only as stable as its worse half
   (issues #266, #269).
5. Helpers: [`signal_cgs_estimate`](../../../circuitgenome/sizer/physics/rnmc.py#L236)
   (gate cap of a device before it has a width, used for the third stage's
   gate on the second-stage output);
   [`design_warnings`](../../../circuitgenome/sizer/physics/rnmc.py#L257)
   (plain-language advisories for what the design had to change).

Who calls it: the Level-1 sizer inside `compute_requirements` (3.7 step 9) on
estimates, and the gm/Id sizer in
[`gmid/rnmc_refine.py:119`](../../../circuitgenome/sizer/gmid/rnmc_refine.py#L119)
on the sized circuit. `metrics.py` reuses `comp_resistances` and `worst_half`
to *report* the RNMC phase margin.

**Running example.** `circuit_0010` is two-stage, so nothing here touches it.
The test suite's single-ended RNMC fixture
[`three_stage_rnmc_se_fbr`](../../../tests/test_sizer.py#L803) is the
`three_stage_opamp_rnmc_single_ended` template with a PMOS input pair, a folded
cascode load, a common-source NMOS second stage (`mn1_second_stage`, output
`net_mid2`), a non-inverting PMOS third stage (`mp1_third_stage` driving an
NMOS mirror onto `out`), `c1_comp1` from `net_mid1` to `out` (Cc1) and
`c1_comp2` from `net_mid1` to `net_mid2` (Cc2). Sized on the `generic` tech
with `_THREE_STAGE_SPEC` (`ibias = 10 µA`, `CL = 20 pF`, `SR ≥ 3.5 V/µs`,
`PM ≥ 60°`), `compute_requirements` hands `design_rnmc`:

| | Start | After `design_rnmc` |
|---|---|---|
| gm2 | 41.9 µS | **516 µS** (ceiling 625 µS) |
| Cc2 | 0.714 pF (Cc1/4) | **2.86 pF** (= Cc1, the last step) |
| gm3 | 1.17 mS | **0.997 mS** (capped, on `mp1_third_stage` only) |
| ζ | **−0.22**: the pair is in the right half plane | 0.708 |
| PM | — | 65.5° (`met = True`) |

(Cc1 = 2.86 pF is the slew bound `10 µA / 3.5 V/µs`, below `0.25·CL = 5 pF`.)
The start violates the stability rule: `gm2·(1 + CL/Cc1) = 41.9 µS · 8 = 0.34 mS`
is far below `gm3 = 1.17 mS`. The search raised gm2 to its ceiling at every
Cc2 with the original gm3 and never reached ζ = 0.7, then stepped gm3 down
once (×0.85) and succeeded at the largest Cc2. The design also emits a
"third-stage gm capped" advisory.

**Test to read.** [`tests/test_rnmc.py`](../../../tests/test_rnmc.py) uses
numbers from a frozen ptm45 FD fixture (module docstring).
- [`test_design_raises_gm2_first`](../../../tests/test_rnmc.py#L156): with room
  on gm2, only gm2 moves.
- [`test_design_caps_gm3_only_when_gm2_and_cc2_run_out`](../../../tests/test_rnmc.py#L172):
  at a 0.9 mS gm2 ceiling the design ends with gm2 at the ceiling and a lower
  gm3, still stable.
- [`test_stability_boundary_is_one_plus_cl_over_cc1`](../../../tests/test_rnmc.py#L42):
  ζ changes sign exactly at `gm3 = gm2·(1 + CL/Cc1)`.
- [`test_metrics_report_the_worse_half`](../../../tests/test_rnmc.py#L280):
  the reported PM is the worse half's.
- [`test_sizer.py:951`](../../../tests/test_sizer.py#L951)
  `test_rnmc_requirements_damp_the_inner_pole_pair`: the running example above,
  asserted.

**Check yourself.**
1. Why is "cap gm3" the last knob?
2. What does a negative ζ mean physically?
3. Why does the gm/Id sizer not use the RNMC re-plan inside
   `compute_requirements`?

<details><summary>Answers</summary>

1. The DC gain is the product of the stages' gains, so a lower gm3 means lower
   gain. Raising gm2 or Cc2 costs current or area but keeps the gain.
2. The pole pair sits in the right half plane: every bounce grows, so the
   amplifier oscillates.
3. Its geometry step (bias repair) moves gm1 far from the estimate, and the
   third stage's real gate capacitance is only known after sizing. So it runs
   `design_rnmc` on the sized circuit (`gmid/rnmc_refine.py`).

</details>

## 3.7 `preprocess.py` (706 lines)

**Job in one sentence.** Do the "pencil and paper" part of sizing that needs no
geometry: give every transistor its DC current, size load resistors and
compensation caps, and turn the spec into **gm targets** per device. It also
hosts the one requirement that is found on the sized circuit instead
(`lift_nodal_cmrr`).

**Design knowledge it encodes.**
- [KCL](00_background.md#kcl): currents at every node must balance, so the
  per-device currents follow from `ibias` and the stage ratios.
- [GBW](00_background.md#gbw) `= gm1 / (2π·Cc)`, turned around: `gm1 ≥ 2π·GBW·Cc`.
- [Miller compensation](00_background.md#miller): `Cc ≈ 0.25·CL` as the
  pole-split floor; [slew](00_background.md#slew) `I/Cc` only caps Cc from above.
- [Phase margin](00_background.md#phase-margin) closed form, turned around:
  `gm2 ≥ gm1·CL / (Cc·tan(90° − PM))`.
- Gain `A0 = k_fs·gm1·Rout1·gm2·Rout2` ([stages](00_background.md#stages),
  [rout](00_background.md#rout)).
- The weak-inversion ceiling `gm ≤ ~25·Id` ([gm](00_background.md#gm),
  [inversion](00_background.md#inversion)).
- [Nested Miller / RNMC](00_background.md#nested-miller) for three stages.
- [CMRR](00_background.md#cmrr) as a gm1 floor.

**Walkthrough.** Read the functions in the order the sizers call them.

1. **Currents**: [`assign_ids`](../../../circuitgenome/sizer/physics/preprocess.py#L220).
   One loop over `all_transistors`, one rule per slot family (the families come
   from [taxonomy](../../../circuitgenome/sizer/physics/taxonomy.py#L17)):
   `input_pair` / `load` → `ibias / n` per same-type device (a pair splits the
   tail current); `tail_current` / `bias_gen` → `ibias`; second stage →
   `ibias · second_stage_current_ratio`; third stage → its own ratio; anything
   else → `ibias`. Two structures need real KCL instead of a rule:
   - [`_cascode_load_current_plan`](../../../circuitgenome/sizer/physics/preprocess.py#L132):
     in a *folded* [cascode](00_background.md#cascode) the bottom "folding"
     devices carry the pair's branch current **plus** the cascode branch's, or
     the pair's current has nowhere to go and the load rails.
   - [`_cmfb_current_plan`](../../../circuitgenome/sizer/physics/preprocess.py#L175):
     inside the [CMFB](00_background.md#cmfb) amp, tails get `ibias`, pair
     devices split their tail, and the rest are found by a small fixed-point
     loop (sum of currents into a drain net, or copy of the diode it mirrors).
2. **Load resistors**:
   [`size_load_resistors`](../../../circuitgenome/sizer/physics/preprocess.py#L51).
   Only rail-referenced resistors in the `load` slot. Each carries `ibias/2`, and
   `R = V_drop / (ibias/2)`, where `V_drop` is the turn-on gate voltage of the
   next stage's input device (the [stage interface](00_background.md#stage-interface)):
   the gm/Id path passes the LUT `Vgs` (`seed_v`, issue #158), Level-1 falls back
   to `|Vth| + 0.15 V`
   ([`_RESISTOR_LOAD_OVERDRIVE`](../../../circuitgenome/sizer/physics/preprocess.py#L34)).
   A one-stage amp drops to `Vcm` instead, because feedback holds its output
   there (issue #284). This is the "spring stiffness" of a
   [resistor load](00_background.md#loads), limited by headroom.
3. **Requirements**:
   [`compute_requirements`](../../../circuitgenome/sizer/physics/preprocess.py#L305),
   the heart of the file (~340 lines). It returns
   `(gm_req_map, vod_max_map, cc_pf, cc2_pf, warnings)`. Step by step:
   1. *Estimate output resistances before any geometry exists*
      ([L357–L394](../../../circuitgenome/sizer/physics/preprocess.py#L357)):
      `model.gds_estimate(type, Id, role)`, combined in parallel with
      `eq.rout`. A resistor load adds its `1/R` to the first stage.
   2. *The first-stage factor* `k_fs`
      ([`_first_stage_gain_factor`](../../../circuitgenome/sizer/physics/preprocess.py#L281)):
      a pair tapped on one side only delivers half its gm, unless a
      current-mirror load folds the other half back in
      ([`_mirror_reference`](../../../circuitgenome/sizer/physics/preprocess.py#L251)
      finds the diode). So `k_fs = 1.0` for a mirror load or a fully-differential
      output, `0.5` otherwise.
   3. *CMRR floor* ([L400–L405](../../../circuitgenome/sizer/physics/preprocess.py#L400)):
      `gm1 ≥ CMRR · 2 · gd_tail`, the closed form `CMRR = gm1/(2·gd_tail)`
      turned around. Skipped when the caller passes `cmrr_gm1` (step 5 below).
   4. *Cc* ([L410–L422](../../../circuitgenome/sizer/physics/preprocess.py#L410)):
      the **smallest** stable cap, `max(cap.min, min(0.25·CL, ibias/SR_min))`
      ([`_CC_STABILITY_RATIO`](../../../circuitgenome/sizer/physics/preprocess.py#L39)).
      Every extra pF raises the gm1 that GBW needs (issue #108).
   5. *gm1 from GBW* ([L433–L434](../../../circuitgenome/sizer/physics/preprocess.py#L433)):
      ```python
      gm1_req = max(gm1_req, 2.0 * math.pi * spec.gbw_min_hz * cc_f / k_fs)
      ```
      If CMRR already pushed gm1 higher, Cc is raised to keep GBW on the spec
      ([L437](../../../circuitgenome/sizer/physics/preprocess.py#L437)). A
      `cmrr_gm1` floor from the sized circuit is applied *after* this, so it
      keeps the GBW-planned Cc ([L446](../../../circuitgenome/sizer/physics/preprocess.py#L446)).
   6. *Two-stage gm2* ([L489–L516](../../../circuitgenome/sizer/physics/preprocess.py#L489)):
      the larger of "gain" (`A0 / (k_fs·gm1·Rout1·Rout2)`) and "phase margin"
      (`k_fs·gm1_eff·CL / (Cc·tan(90° − PM))`). `gm1_eff` is
      `model.realized_gm`: the gm1 the pair will *really* have, which can be
      more than asked for.
   7. *Three-stage* ([L449–L487](../../../circuitgenome/sizer/physics/preprocess.py#L449)):
      `Cc2 = Cc1/4` by default (NMC: sized for a damping ζ = 0.3 with
      `gm3 ≥ 3.5·gm2`), phase budget split between the two non-dominant poles,
      and gm3 solved from the gain target.
   8. *Clamp to the ceiling* ([L535–L558](../../../circuitgenome/sizer/physics/preprocess.py#L535)):
      any gm above `model.gm_ceiling(Id)` is clamped, with a warning ("the spec
      needs more current than the device can give").
   9. *RNMC re-plan* ([L563–L578](../../../circuitgenome/sizer/physics/preprocess.py#L563)):
      only when the caller asks for `"reversed_nested_miller"` (the Level-1
      sizer). Calls `rnmc.design_rnmc` (3.6). The gm/Id sizer passes `None`
      here ([plan.py:127](../../../circuitgenome/sizer/gmid/plan.py#L127)) and
      re-plans RNMC after geometry instead (Stop 4, `rnmc_refine.py`).
   10. *Per-device map* ([L584–L602](../../../circuitgenome/sizer/physics/preprocess.py#L584)):
       gm1 on both pair devices, gm2/gm3 on each stage's signal device only;
       everything else gets no gm target (a current source is sized for
       headroom, not gm).
   11. *Vdsat caps from swing* ([L604–L644](../../../circuitgenome/sizer/physics/preprocess.py#L604)):
       `Vdsat ≤ Vdd − swing_max` for load and stage PMOS, `Vdsat ≤ swing_min − Vss`
       for stage NMOS ([swing](00_background.md#swing)).
4. **Caps onto devices**:
   [`size_compensation_caps`](../../../circuitgenome/sizer/physics/preprocess.py#L105)
   writes `cc_pf` onto every `comp*` capacitor and `cc2_pf` onto `comp2*` ones,
   in farads.
5. **CMRR on the sized circuit**:
   [`lift_nodal_cmrr`](../../../circuitgenome/sizer/physics/preprocess.py#L649)
   (issue #296). For a single-ended chain the *reported* CMRR is the nodal one
   (3.9), and on a mirror load it follows the pair's drain imbalance, not the
   tail: `CMRR ≈ gm1/|gds1 − gds2|`. Raising gm1 against `2·gd_tail` can even
   lower it. So: size once with no CMRR floor; while the reported CMRR is
   short, raise gm1 by the shortfall (`gm1 · 10^(short/20)`, capped at the
   widest device and the ceiling) and re-size, at most twice
   ([`_CMRR_PASSES`](../../../circuitgenome/sizer/physics/preprocess.py#L48)).
   [`_lifts_cmrr`](../../../circuitgenome/sizer/physics/preprocess.py#L697)
   keeps a re-size only if CMRR went up, bias stayed feasible, and no other spec
   got worse. Both sizers call it only when there is a CMRR target and
   `reports_nodal_rejection` is true
   ([gmid_sizer.py:94](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L94),
   [level1.py:69](../../../circuitgenome/sizer/analytical/level1.py#L69)).
   Analogy: a thermostat that is allowed two nudges and must not make any
   other room colder.

**Running example.** What `circuit_0010` gets (gf180, spec
`ibias = 20 µA`, `CL = 5 pF`, `GBW ≥ 2 MHz`, `PM ≥ 60°`, `SR ≥ 0.3 V/µs`):

| Step | Result |
|---|---|
| `assign_ids` | pair and load devices 10 µA each, tail and bias-gen devices 20 µA, `mn1/mp1_second_stage` 50 µA (2.5 × 20 µA) |
| `size_load_resistors` | `{}`: no resistor load |
| `k_fs` | 1.0: `m1_load` is a diode, so the load is a mirror |
| Cc | `min(0.25 · 5 pF, 20 µA / 0.3 V/µs = 66.7 pF)` = **1.25 pF** |
| gm1 (GBW) | `2π · 2 MHz · 1.25 pF` = **15.7 µS** |
| `realized_gm` | **60 µS**: the gf180 LUT cannot go below gm/Id = 6 at 10 µA, so a 15.7 µS ask is delivered at 60 µS (this is why GBW ends at ~7 MHz, not 2 MHz) |
| gm2 (gain, 60 dB) | 250 µS |
| gm2 (PM) | `60 µS · 5 pF / (1.25 pF · tan 30°)` = **415.7 µS** (wins) |
| `vod_max_map` | 0.3 V on `m1/m2_load`, `mp1_second_stage` (Vdd − 3.0 V) and `mn1_second_stage` (0.3 V − 0) |
| `lift_nodal_cmrr` | not called: the spec has no `cmrr_min_db` |

The second stage ends up at 575 µS, not 415.7 µS; the geometry and
stage-interface steps of Stop 4 explain that.

**Test to read.**
- [`test_sizer.py:533`](../../../tests/test_sizer.py#L533)
  `test_size_two_stage_cc_stability_floor`: a loose slew spec leaves Cc on
  `0.25·CL`, not on the slew bound.
- [`test_sizer.py:1254`](../../../tests/test_sizer.py#L1254)
  `test_folded_cascode_load_current_plan` (and the four after it): the folding
  sinks carry 20 µA, every other load device 10 µA; telescopic loads carry the
  pair current all the way up.
- [`test_sizer.py:209`](../../../tests/test_sizer.py#L209)
  `test_compensation_caps_*`: `comp2*` gets Cc2, falls back to Cc1, and nothing
  without a Cc.
- [`test_sizer.py:1741`](../../../tests/test_sizer.py#L1741) to
  [L1811](../../../tests/test_sizer.py#L1811): the `lift_nodal_cmrr` family. A
  met target changes nothing; a short one raises the pair's width (but not to
  the maximum); an unreachable one costs no other spec; FD and telescopic
  designs still use the tail-only floor; a lift that costs PM is refused.

**Check yourself.**
1. Why does the code choose the *smallest* stable Cc instead of the largest the
   slew spec allows?
2. `k_fs = 0.5` for a resistor load. Which formulas does it enter, and which
   one deliberately not?
3. Why is the CMRR target handled by re-sizing (`lift_nodal_cmrr`) and not by a
   floor inside `compute_requirements` for single-ended amps?

<details><summary>Answers</summary>

1. GBW needs `gm1 = 2π·GBW·Cc/k_fs`. A bigger Cc demands a bigger gm1, which
   pushes the input pair toward its weak-inversion ceiling (issue #108). The
   slew spec only says "Cc must not be larger than ibias/SR".
2. The first-stage gain (`k_fs·gm1·Rout1`) and gm1's role in GBW and PM
   (`k_fs·gm1`). Not the raw gm1 used for CMRR.
3. On a mirror-loaded single-ended pair the reported (nodal) CMRR is set by the
   drain imbalance `|gds1 − gds2|`, which only exists once devices have sizes
   and bias levels. The closed-form floor follows the wrong path and can lower
   the real CMRR (issue #296).

</details>

## 3.8 `node_levels.py` (141 lines)

New since the #250 checklist. It needs only `device_model` and `taxonomy`,
but it sits here, right before `stage_chain.py` (3.9), because that file is
its only caller: read it, then see it used.

**Job in one sentence.** It estimates the DC voltage on every net of a sized
circuit, without solving the circuit, so that each device can be read from the
LUT at its own `Vds` and `Vsb` instead of the table's `Vdd/2`.

**Design knowledge it encodes.**

- [Bias point first](00_background.md#small-signal): small-signal parameters
  depend on the DC point.
- The LUT caveat in [gm/Id](00_background.md#gmid): the device under a
  [cascode](00_background.md#cascode) sits just above its knee, where its gds
  is several times the table's (#273).
- [Body effect](00_background.md#vth-body): a source above its bulk raises
  `|Vgs|`.
- [Current mirror](00_background.md#current-mirror): a diode-connected device's
  gate sits one `Vgs` from its source. [Ohm's law](00_background.md#voltage-current)
  for resistors: `V = I·R`.

**Analogy.** Sudoku. Start from the cells you are given (the rails, the input
common mode), fill in every cell you can deduce from a filled neighbour, and
repeat until a pass fills nothing new.

**Walkthrough.**

1. **Start from the pinned nets.**
   [`estimate_node_levels`](../../../circuitgenome/sizer/physics/node_levels.py#L72)
   copies `pinned` into `levels`
   ([line 93](../../../circuitgenome/sizer/physics/node_levels.py#L93)). The
   caller pins the rails, the input gates at `Vcm`, and sometimes the output.
   Pinned values are never overwritten. It also counts how many sized
   resistors touch each net ([lines 95–98](../../../circuitgenome/sizer/physics/node_levels.py#L95)).
2. **Propagate in passes.** [Line 99](../../../circuitgenome/sizer/physics/node_levels.py#L99)
   runs at most one pass per device. Each pass collects new levels in `new`
   and commits them only at the end
   ([lines 129–131](../../../circuitgenome/sizer/physics/node_levels.py#L129)),
   so it spreads outward like a breadth-first search. When two devices reach
   the same net in one pass, the first in list order wins (`put`,
   [line 102](../../../circuitgenome/sizer/physics/node_levels.py#L102)).
3. **MOSFET rule** ([lines 106–118](../../../circuitgenome/sizer/physics/node_levels.py#L106)).
   Every sized device carries its planned current at its planned `|Vgs|`
   (from `TransistorSizing.vgs_v`). So:
   - gate known, source unknown → `Vs = Vg − sign·|Vgs|` (a cascode's source,
     the pair's tail node, a follower's output). The `|Vgs|` includes the body
     shift at the source level being solved, so this is a
     [fixed-point iteration](#fixed-point) of
     [`_BODY_PASSES = 4`](../../../circuitgenome/sizer/physics/node_levels.py#L40)
     rounds;
   - source known, gate unknown → `Vg = Vs + sign·|Vgs|` (a diode node, a
     mirror gate, a common-source stage's input).

   `sign` is +1 for NMOS, −1 for PMOS. A MOSFET never sets its **drain** as
   such (a diode's drain is known only because it is also its gate): in
   saturation the drain voltage is set by whatever else sits on that net, not
   by the device.
4. **Resistor rule** ([lines 119–128](../../../circuitgenome/sizer/physics/node_levels.py#L119)).
   When exactly one end of a sized resistor is known, the other end is
   `known ∓ I·R`. The current `I` is read with
   [`_current_into`](../../../circuitgenome/sizer/physics/node_levels.py#L53)
   (the MOSFET current that drains into, or out of, that net) at whichever end
   has only this one resistor. That covers a bias leg's floor resistor and a
   degeneration resistor pair.
5. **Body shift helper.** [`_vgs_mag`](../../../circuitgenome/sizer/physics/node_levels.py#L43)
   returns `|Vgs|` plus `model.body_vth_shift(|Vs − Vb|)` when the bulk is
   known and is not the source. That is the only thing this file needs from
   `DeviceModel`.
6. **What callers read.** [`device_bias`](../../../circuitgenome/sizer/physics/node_levels.py#L135)
   turns the levels into `(|Vds|, |Vsb|)` for one device, each `None` if a net
   was not reached. A `None` means "read at the table's own bias", the old
   behaviour. The callers are in `stage_chain.py`:
   [`node_rout`](../../../circuitgenome/sizer/physics/stage_chain.py#L96)
   (the cascode-aware output-resistance walk),
   [line 753](../../../circuitgenome/sizer/physics/stage_chain.py#L753)
   (the nodal CMRR/PSRR solve, output pinned at `Vcm`) and
   [line 1141](../../../circuitgenome/sizer/physics/stage_chain.py#L1141)
   (the bias-aware first-stage chain, output left unpinned).

**Running example.** For `circuit_0010` the pinned nets are `vdd!` = 3.3,
`gnd!` = `vss!` = `0` = 0, `in1` = `in2` = 1.65 V (plus `out` = 1.65 V for the
CMRR/PSRR solve). The estimate reaches every other net in the first pass:

| Net | Level (V) | How |
|---|---|---|
| `net_tail` | 2.733 | gate `in1` known → source = 1.65 + 1.083 (PMOS pair \|Vgs\|; bulk = source, no body shift) |
| `net_diff1` | 0.841 | `m1_load` source on ground → its diode gate = 0 + 0.841 |
| `net_mid` | 0.807 | `mn1_second_stage` source on ground → its gate = 0 + 0.807 |
| `net_bias7` | 2.358 | `m1_tail_current` source on `vdd!` → gate = 3.3 − 0.942 |
| `net_bias5` | 2.358 | `mp5_bias_gen` / `mp1_second_stage`, same PMOS \|Vgs\| |
| `ibias` | 0.832 | `mnref_bias_gen` diode |
| `out` | not reached (unless pinned) | only drains touch it |

What that does to the LUT reads (`GmIdModel.gds` with `vds` against without):

| Device | Own \|Vds\| | gds factor vs the `Vdd/2` read |
|---|---|---|
| `m2_tail_current` | 3.3 − 2.733 = 0.567 V | ×2.27 |
| `m2_input_pair` | 2.733 − 0.807 = 1.926 V | ×0.90 |
| `m2_load` | 0.807 V | ×1.30 |
| `mn1`, `mp1_second_stage` | 1.65 V (or unknown) | ×1.00 |

The first-stage output conductance is `g1 = gds(m2_input_pair) + gds(m2_load)`.
With levels it is 0.248 + 0.612 = **0.861 µS**; with the single-point read it
would be 0.277 + 0.472 = 0.750 µS. That 15 % moves the predicted gain from
82.7 dB to **81.5 dB**, against SPICE's 81.2 dB. The tail's ×2.27 matters for
CMRR (the tail's leak is what CMRR measures). This is a second "which code
made it right" answer, alongside the loop model.

**Test to read.** [`tests/test_node_levels.py`](../../../tests/test_node_levels.py),
which uses a fake model whose body shift is `k·Vsb`, so every answer can be
checked by hand:

- [`test_known_gate_sets_the_source_and_known_source_sets_the_gate`](../../../tests/test_node_levels.py#L29):
  both MOSFET rules for NMOS and PMOS; a drain-only net stays unknown.
- [`test_floor_resistor_drops_its_leg_current`](../../../tests/test_node_levels.py#L47):
  20 µA × 10 kΩ lifts a diode leg by 0.2 V, and the cascode it gates puts the
  device below at that floor.
- [`test_unsized_resistor_drops_nothing`](../../../tests/test_node_levels.py#L65):
  a resistor with no value blocks propagation.
- [`test_degeneration_resistor_reads_its_current_at_the_pair_source`](../../../tests/test_node_levels.py#L73):
  two resistors meeting on the tail; each current is read at its pair end.
- [`test_body_effect_raises_a_cascode_vgs`](../../../tests/test_node_levels.py#L88):
  the fixed point `Vs = (Vg − Vgs)/(1 + k)`.
- [`test_pinned_levels_are_never_overwritten`](../../../tests/test_node_levels.py#L96).

**Check yourself.**

1. Why can't the estimate set a MOSFET's drain level?
2. What happens to a device on a net the estimate never reaches?
3. Why does the bias-aware chain leave `out` unpinned, while the CMRR/PSRR
   solve pins it at `Vcm`?

<details><summary>Answers</summary>

1. In saturation a MOSFET is a current source: its current hardly depends on
   `Vds`, so its drain can sit anywhere above `Vdsat`. The drain level is set
   by whatever else is on that net (a diode, the next stage's gate, the
   feedback that holds the output).
2. `device_bias` returns `None` for that terminal, and `GmIdModel.gds` /
   `gmb` read at the table's own bias (`Vdd/2`, `Vsb = 0`), the pre-#273
   behaviour.
3. The `stage_chain.py` docstring
   ([line 1108](../../../circuitgenome/sizer/physics/stage_chain.py#L1108))
   says a device draining onto the output then reads at `Vdd/2`, which on a
   cascode errs to the safe, over-predicting side. The rejection solve needs
   the output-side drain level, because that sets how far a mirror-loaded
   pair's two drains differ (#290). The benches' DC loop holds the output at
   `Vcm`, so that is the honest pin there.

</details>

---

## 3.9 `stage_chain.py` (1318 lines)

**Job in one sentence.** Read a fully sized circuit and boil it down to a small
`StageChain`: per-stage gm and output resistance, plus everything the metrics
need (caps on each node, supply currents, swing headroom, a nodal CMRR/PSRR).
After this, `metrics.py` never looks at a device again.

This is the densest file of the stop. It is easiest read in five parts:
(A) the cascode-aware resistance walk, (B) the two dataclasses, (C)
`build_stage_chain`, (D) CMRR/PSRR, (E) power and swing.

### Background for this section

- [rout](00_background.md#rout) and parallel resistances; [cascode](00_background.md#cascode);
  [degeneration](00_background.md#degeneration); [nodal analysis](00_background.md#nodal-analysis);
  [CMRR](00_background.md#cmrr); [PSRR](00_background.md#psrr); [power](00_background.md#power);
  [swing](00_background.md#swing); [source follower](00_background.md#source-follower).
- **Node levels** (Stop 3.8): `estimate_node_levels` guesses the DC voltage of
  every net from the rails, the input common mode and each device's `Vgs`;
  `device_bias(device, levels)` returns that device's `(Vds, Vsb)`.
- **Looking-in resistance.** "The resistance looking into a drain" means: hold
  everything else still, wiggle the drain voltage, and see how much the current
  changes. For a bare transistor that is `ro = 1/gds`. If something *below* the
  device has resistance `R` (another transistor, or a resistor), the device
  fights back: when the drain rises, its source rises a little too, which
  lowers its `Vgs`, which cuts the current. The result is

  ```
  R_in = ro · (1 + gm · R)       (the code's form; ≈ gm·ro·R, the cascode boost)
  ```

  Analogy: a pressure regulator on top of a flow regulator. The more the bottom
  one resists, the harder the top one works to hold the flow.

### A. The cascode-aware walk: `node_rout`

1. [`node_rout(out_net, ...)`](../../../circuitgenome/sizer/physics/stage_chain.py#L119):
   the resistance at a node is the parallel combination of every device whose
   drain sits on it ([`_parallel_rout`](../../../circuitgenome/sizer/physics/stage_chain.py#L50),
   using [`_by_drain`](../../../circuitgenome/sizer/physics/stage_chain.py#L41)).
2. Each device's share is
   [`_looking_in_drain`](../../../circuitgenome/sizer/physics/stage_chain.py#L60),
   the heart of the walk ([L93–L116](../../../circuitgenome/sizer/physics/stage_chain.py#L93)):

   ```python
   vds, vsb = device_bias(device, levels or {})
   gds = model.gds(device.type, s.w_um, s.l_um, s.ids_a, vds=vds)
   ro = 1.0 / gds if gds > 0 else float("inf")
   ...
   if r_deg:                                    # source-degeneration resistor
       return ro * (1.0 + gm_boost() * r_deg)
   if src in RAILS or src in stop or src is None:
       return ro                                # source on AC ground
   below = [d for d in by_drain.get(src, []) if d.ref != device.ref]
   r_src = _parallel_rout(below, ...)           # recurse: everything below
   return ro * (1.0 + gm_boost() * r_src)
   ```

   Three cases, three design ideas:
   - **Source on a rail or in `stop`** → bare `ro`. `stop` holds the input
     pair's tail node, which is AC ground for a differential signal (both
     sides move in opposite directions, so the shared source stays still).
   - **Source on a degeneration resistor** (`degen`, from
     [`_source_degeneration_r`](../../../circuitgenome/sizer/physics/stage_chain.py#L777))
     → `ro·(1 + gm·R)`, the [degeneration](00_background.md#degeneration) boost
     (issue #226). Checked *before* `stop`.
   - **Source on another device's drain** → that is a cascode. `R_source` is
     *every* device draining onto that net, in parallel (issue #240: a folded
     cascode's folding node has the pair *and* the current sink on it).
     The recursion handles stacks of any height.
3. **Each device at its own bias** (issue #273). When `levels` are given, `gds`
   is read at the device's own `Vds` rather than the LUT's single `Vdd/2`
   point, and `gm_boost` adds the body transconductance `gmb` when the source
   is off the bulk (the source swing also moves the threshold). Under a cascode
   the bottom device sits just above its saturation knee, where its `gds` is
   several times the table value; reading it there removed most of an 8–20 dB
   gain over-estimate on cascode loads.

The exact looking-in resistance is `ro_c + R_below + gm_c·ro_c·R_below`. The
code's `ro_c·(1 + gm_c·R_below)` drops the small `R_below` term, and both
reduce to `gm_c·ro_c·R_below` for a good cascode.

### B. The vocabulary: `Stage` and `StageChain`

- [`Stage`](../../../circuitgenome/sizer/physics/stage_chain.py#L141): three
  floats, `gm`, `rout` and `ids` (the most current the stage can push, used
  for slew).
- [`StageChain`](../../../circuitgenome/sizer/physics/stage_chain.py#L157): the
  stages, input pair first (a follower `output_stage` has gain ≈ 1 and is
  **not** a stage), plus 19 more fields. The docstring
  ([L158–L242](../../../circuitgenome/sizer/physics/stage_chain.py#L158)) is
  the reference. The ones that matter most:

  | Field | What it is | Used for |
  |---|---|---|
  | `k_fs`, `k_slew` | first-stage factor (3.7) and the share of tail current that can slew the first node | gain, GBW, PM, slew |
  | `se_rejection_db` | `(cmrr_db, psrr_db)` from the nodal solve, or `None` | CMRR/PSRR (single-ended) |
  | `gd_tail`, `gd_output_load` | closed-form conductances | CMRR/PSRR when `se_rejection_db is None` |
  | `node_caps_f` | capacitance on each gain stage's output node | full-model GBW and PM |
  | `supply_currents` | current of each branch drawing from `vdd!` | power |
  | `swing_headroom` | `(below_vdd, above_vss)` | swing |
  | `cc_pf`, `cc2_pf`, `comp_resistors`, `output_buffer`, `cmfb_coupling`, mirror poles | compensation details | the loop models |
  | `gain_measurable` | `False` when the DC operating point does not exist (#148) | withholds gain-derived metrics |

- Small, pure transforms return new chains (frozen dataclass):
  [`withheld`](../../../circuitgenome/sizer/physics/stage_chain.py#L264),
  [`with_first_stage_gm`](../../../circuitgenome/sizer/physics/stage_chain.py#L268)
  (degeneration lowers gm1),
  [`with_tail_conductance`](../../../circuitgenome/sizer/physics/stage_chain.py#L275)
  (a resistor tail),
  [`with_output_loading`](../../../circuitgenome/sizer/physics/stage_chain.py#L285)
  (a resistive CMFB sense loads the output). `gmid/resistors.py`'s
  `MetricModifiers` applies them (Stop 4).

### C. `build_stage_chain`: the main path

[`build_stage_chain(view, sizing, model, spec, *, cc_pf, cc2_pf, gd_load_r, resistor_ohms, bias_aware)`](../../../circuitgenome/sizer/physics/stage_chain.py#L1076).
Its callers: the gm/Id evaluator with `bias_aware=True`
([evaluate.py:112](../../../circuitgenome/sizer/gmid/evaluate.py#L112)); the
gm/Id re-plans `rnmc_refine` and `two_stage_refine`; the Level-1 sizer
([level1.py:136](../../../circuitgenome/sizer/analytical/level1.py#L136)),
all with the default `bias_aware=False`.

1. **AC grounds and degeneration**
   ([L1132–L1134](../../../circuitgenome/sizer/physics/stage_chain.py#L1132)):
   the pair's source net goes in `stop`; sized degeneration resistors go in
   `degen`. An unsized one (the synthesizer's 1 kΩ placeholder) boosts nothing.
2. **Node levels** ([L1136–L1142](../../../circuitgenome/sizer/physics/stage_chain.py#L1136)),
   only when `bias_aware`: rails pinned to their voltages, pair gates pinned at
   `Vcm`. The amplifier output is deliberately left unpinned.
3. **gm per device** ([`_gm`, L1144](../../../circuitgenome/sizer/physics/stage_chain.py#L1144)):
   `min(model.gm, model.gm_ceiling)`. A device can never be credited more than
   the weak-inversion ceiling.
4. **Which slots are stages** ([L1164–L1171](../../../circuitgenome/sizer/physics/stage_chain.py#L1164)):
   the first present of `second_stage*`, then `third_stage*`
   ([`_STAGE_SLOT_GROUPS`](../../../circuitgenome/sizer/physics/stage_chain.py#L305)),
   and each one's signal device.
5. **Stage 1** ([L1174–L1189](../../../circuitgenome/sizer/physics/stage_chain.py#L1174)).
   The first stage's output is **the next stage's signal gate**, not the pair's
   drain. On a folded cascode those are different nets. With no next stage
   (one-stage OTA), the output is where the pair's signal path *ends*:
   [`_signal_path_nets`](../../../circuitgenome/sizer/physics/stage_chain.py#L401)
   climbs from the pair's drains through every cascode, and
   [`_single_ended_output_net`](../../../circuitgenome/sizer/physics/stage_chain.py#L422)
   picks the end that is not the mirror's diode (issue #228).

   ```python
   stages = [Stage(gm=_gm(ip_devs[0]) if ip_devs else 0.0,
                   rout=_rout(out1, gd_load_r, levels),
                   ids=sum(_ids(d) for d in ip_devs if is_signal_device(d)))]
   for sig in signal_devs:
       stages.append(Stage(gm=_gm(sig) if sig is not None else 0.0,
                           rout=_rout(sig.terminals.get("d") if sig else None),
                           ids=_ids(sig)))
   ```

   `_rout` ([L1155](../../../circuitgenome/sizer/physics/stage_chain.py#L1155))
   is `node_rout` plus the load resistor's `1/R` in parallel (the walk only
   sees MOSFETs). Only stage 1 gets `levels`; later stages are read at the
   table point (their signal drain can be a mirror's diode node, and reading
   those at their own bias moved three-stage gains 1–3.5 dB under SPICE).
6. **Stages 2 and 3**: same, at each signal device's drain.
7. **Closed-form helpers**: `gd_output_load`
   ([L1213–L1230](../../../circuitgenome/sizer/physics/stage_chain.py#L1213),
   the second stage's current-source load, or on a one-stage amp the
   cascode-aware load branch on the output, or `1/R`) and `gd_tail`
   ([L1238–L1242](../../../circuitgenome/sizer/physics/stage_chain.py#L1238),
   the whole tail stack via `node_rout` from the net the tail really drains on,
   hopping a degeneration resistor with
   [`_tail_current_net`](../../../circuitgenome/sizer/physics/stage_chain.py#L324)).
8. **What the loop models need** ([L1244–L1288](../../../circuitgenome/sizer/physics/stage_chain.py#L1244)):
   `node_caps_f` = gate caps of every device gated on each stage output, and
   behind a follower the follower's gate net plus its drain junctions; RNMC
   uses `cgs`, everything else `cgg` at its inversion level (#274). Also the
   mirror poles and which compensation branches have a series resistor.
9. **The rest** ([L1293–L1318](../../../circuitgenome/sizer/physics/stage_chain.py#L1293)):
   `se_rejection_db` (part D), `supply_currents` (part E), `swing_headroom`
   (part E), `_mirror_pole_hz`, `_output_buffer` (follower gm, gate cap and the
   conductances on its output, including body effect: an NMOS follower's gain
   is ~0.88 on gf180, not 1), and `_cmfb_coupling` (a fully-differential
   amp's CMFB loop as `(gm_c, pole)`, from a small nodal network of the
   `cmfb` slot; #274). `k_slew` is 0.5 for FD, else `k_fs`.

### D. CMRR and PSRR: a DC nodal solve

**Why not a formula?** The closed form `CMRR = gm1/(2·gd_tail)` assumes the
common mode leaks only through the tail. A current-mirror load *cancels* most
of that leak: the mirror copies the common-mode current from one side and
subtracts it on the other. What is left is the two pair devices' **drain
imbalance**: one drain sits on the mirror's diode node, the other on the
output, so their `Vds` and `gds` differ. A common-mode input then turns into
output current through `|gds1 − gds2|`, and `CMRR ≈ gm1/|gds1 − gds2|`
(issue #290). PSRR has the same problem: supply ripple reaches the output
through whichever bias nodes do *not* follow Vdd, which no single formula
tracks for every topology.

**How the code does it.**
1. [`reports_nodal_rejection`](../../../circuitgenome/sizer/physics/stage_chain.py#L724)
   decides who gets the nodal answer: single-ended, a two-device pair, and not
   a telescopic cascode load
   ([`_telescopic_cascode_load`](../../../circuitgenome/sizer/physics/stage_chain.py#L708);
   there the level estimate cannot see a ~1 mV drain imbalance and the nodal
   CMRR over-predicts by ~47 dB, so the safe-side closed form stays). The
   sizers use the same test to decide whether to run `lift_nodal_cmrr`.
2. [`_circuit_rejection_db`](../../../circuitgenome/sizer/physics/stage_chain.py#L737)
   prepares the network: node levels with the **output pinned at Vcm**
   ([L751](../../../circuitgenome/sizer/physics/stage_chain.py#L751)), as the
   benches' DC loop holds it, because the output's level is what sets the
   imbalance. Every MOSFET gets `(gm, gds at its own Vds, gmb)`. Resistors take
   their sized value, else the load conductance, else the netlist value
   ([`_netlist_ohms`](../../../circuitgenome/sizer/physics/stage_chain.py#L620)).
3. [`_se_rejection_db`](../../../circuitgenome/sizer/physics/stage_chain.py#L637)
   is a tiny DC circuit solver. It writes [KCL](00_background.md#kcl) at every
   undriven net (`G · v = rhs`), with three stimuli solved at once
   ([L672](../../../circuitgenome/sizer/physics/stage_chain.py#L672)):

   ```python
   stimuli = ({inputs[0]: 1.0}, {inputs[0]: 1.0, inputs[1]: 1.0}, {"vdd!": 1.0})
   ...
   adm, acm, avdd = (abs(x) for x in v)          # v = output voltage per stimulus
   return tuple(20.0 * math.log10(adm / a) if a > 0.0 else None
                for a in (acm, avdd))
   ```

   One input alone (differential gain), both together (common-mode gain), and
   `vdd!` (supply gain), each 1 V, exactly as the SPICE benches drive them.
   CMRR = `Adm/Acm`, PSRR+ = `Adm/Avdd`. Capacitors are open at DC and the
   `ibias` pin floats on its ideal current source. Analogy: the spreadsheet of
   "flow in = flow out" from the [nodal-analysis](00_background.md#nodal-analysis)
   primer, filled in three times.

### E. Power and swing

**Power**: [`vdd_supply_currents`](../../../circuitgenome/sizer/physics/stage_chain.py#L352)
(issue #285). [KCL](00_background.md#kcl) at the positive rail: every MOSFET
with its drain or source on `vdd!` contributes its planned current, and every
resistor on `vdd!` contributes the current of the channels at its far end
(crossing resistor-only junctions, for a resistor tail feeding degeneration
resistors).

```python
currents = [ids(d) for d in mosfets if "vdd!" in channel(d)]
for r in resistors.values():
    if "vdd!" in (r.terminals.get("t1"), r.terminals.get("t2")):
        currents.append(fed(far_end(r, "vdd!"), r, set()))
```

So each branch is counted once, whatever slot it is in. The external
reference current is **not** counted: the bench injects `Iref` from ground,
so the supply current SPICE reads (`i(Vsup)`) does not include it either.
Analogy: read the building's water meter at the main pipe, instead of adding
up every tap and counting shared pipes twice.

**Swing**:
- [`output_swing_headroom`](../../../circuitgenome/sizer/physics/stage_chain.py#L963)
  takes the tighter of two limits per side:
  [`_stage_swing_headroom`](../../../circuitgenome/sizer/physics/stage_chain.py#L876)
  (no buffer: the second stage's rail-side `Vdsat`; behind a
  [follower](00_background.md#source-follower): the driver's `Vdsat` plus the
  follower's `|Vgs|`, with body effect solved as a short fixed point), and
  [`cmfb_sense_range`](../../../circuitgenome/sizer/physics/stage_chain.py#L930)
  (a difference-difference CMFB loses the common mode beyond
  `Vocm ± √2·Vdsat(sense device)`, issue #272).
  [`_fd_symmetric`](../../../circuitgenome/sizer/physics/stage_chain.py#L825)
  mirrors a buffered FD amp's edges (one output's high edge is the other's low
  edge, #281).
- Two **gates** that can reject a sizing:
  [`check_follower_swing`](../../../circuitgenome/sizer/physics/stage_chain.py#L994)
  (the follower's level shift misses the swing spec by more than
  [`_FOLLOWER_SWING_TOL_V = 0.2 V`](../../../circuitgenome/sizer/physics/stage_chain.py#L809))
  and [`check_cmfb_sense_swing`](../../../circuitgenome/sizer/physics/stage_chain.py#L1038)
  (DDA sense range, 0.15 V tolerance). They return `(warnings, feasible)`;
  the gm/Id sizer folds `feasible` into `bias_feasible`
  ([gmid_sizer.py:166–169](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L166)).
  No sizing knob fixes a follower's level shift: its `|Vgs|` cannot go below
  roughly `Vth`.

**Also in this file**: `_mirror_pole_hz` (mirror node pole `gm/(2π·ΣCgs)`, the
non-dominant pole of a one-stage OTA; `None` for resistor loads on purpose),
`_output_buffer`, `_cmfb_coupling`, `_follower`, `_vdsat_of`,
`_is_fully_differential`.

### Running example: the chain of `circuit_0010`

Captured by wrapping `gmid.evaluate.build_stage_chain` during
`size_circuit(...)` (the same trick as the #274 diagnosis):

```python
import circuitgenome.sizer.gmid.evaluate as ev
orig, seen = ev.build_stage_chain, []
ev.build_stage_chain = lambda *a, **k: seen.append(orig(*a, **k)) or seen[-1]
result = size_circuit(parsed, sr, fbr, topology, tech, spec)   # as run_example.py
chain = seen[-1]
```

**Stage 1** (input pair → `net_mid`, the gate of `mn1_second_stage`). Two
devices drain on `net_mid`, both with sources on AC ground (`m2_input_pair`
on the tail node in `stop`, `m2_load` on `gnd!`), so no cascode boost, just
two `ro` in parallel, each read at its own `Vds`:

| Device on `net_mid` | Vds (levels) | gds at own Vds | gds at table point |
|---|---|---|---|
| `m2_input_pair` | 1.93 V | 248.3 nS | 277.2 nS |
| `m2_load` | 0.81 V | 612.3 nS | 472.3 nS |
| **Rout1** | | **1/(860.5 nS) = 1.162 MΩ** | 1/(749.5 nS) = 1.334 MΩ |

**Stage 2** (`mn1_second_stage` → `out`). `out` is not pinned, so both devices
read at the table point: `mn1_second_stage` 2664 nS, `mp1_second_stage`
697.6 nS → **Rout2 = 297 kΩ**.

**The gain**:

| Stage | gm | rout | gm · rout | dB |
|---|---|---|---|---|
| 1 | 60.0 µS (× `k_fs` = 1.0) | 1.162 MΩ | 69.7 | 36.9 |
| 2 | 575.0 µS | 0.297 MΩ | 171.0 | 44.7 |
| total | | | 11 920 | **81.5 dB** (SPICE 81.2 dB) |

With `bias_aware=False` (the table point everywhere) Rout1 is 1.334 MΩ and the
gain 82.7 dB, which is what `a193951` reported. The mirror load is read at
0.81 V, well below `Vdd/2`, where its `gds` is ~30 % higher.

**The other fields**:

| Field | Value | Note |
|---|---|---|
| `k_fs`, `k_slew` | 1.0, 1.0 | mirror load |
| `node_caps_f` | (23.3 fF, 0) | `mn1_second_stage`'s gate on `net_mid`; no buffer |
| `cc_pf`, `comp_resistors` | 1.25, `((False, False),)` | bare Miller cap |
| `supply_currents` | 20, 20, 50, 20 µA | `m1_tail_current`, `m2_tail_current`, `mp1_second_stage`, `mp5_bias_gen` → 110 µA |
| `swing_headroom` | (0.154 V, 0.145 V) | `Vdsat` of `mp1` / `mn1_second_stage` |
| `se_rejection_db` | (80.1 dB, 90.5 dB) | nodal CMRR, PSRR+ |
| `gd_tail` | 279 nS | closed-form CMRR would be 40.6 dB (not reported) |
| `gd_output_load` | 697.6 nS | closed-form PSRR would be 58.3 dB (not reported) |
| `mirror_pole_hz` | 1.98 GHz | only a one-stage OTA uses it |

For the nodal solve the levels put the pair's drains at 0.841 V (diode side)
and 0.807 V (`net_mid`), so `m1_input_pair` reads 251.6 nS and
`m2_input_pair` 248.3 nS: a 3.3 nS imbalance against a 60 µS gm1. The
mirror (not the tail) sets the CMRR here. For PSRR, an experiment on the same
network: holding one PMOS current source's gate still (the second-stage load
`mp1_second_stage`, or the tail mirror), instead of letting
`net_bias5`/`net_bias7` follow Vdd through their diodes, drops the nodal PSRR
from 90.5 dB to 37–38 dB. The high PSRR comes from the bias rails riding
with the supply, so the PMOS sources keep a constant `Vsg`.

### Tests to read

- [`test_cascode_rout.py:50`](../../../tests/test_cascode_rout.py#L50)
  `test_node_rout_cascode_boost`: a two-high stack gives exactly
  `ro·(1 + gm·ro)`. Then
  [`L81`](../../../tests/test_cascode_rout.py#L81) (degeneration boost),
  [`L122`](../../../tests/test_cascode_rout.py#L122) (folding node: every drain
  counts, #240) and
  [`L150`](../../../tests/test_cascode_rout.py#L150)
  `test_levels_read_the_device_under_the_cascode_at_its_own_vds` (#273: the
  bottom device at 0.3 V has 4× the gds, and the boost drops by 4×).
- [`test_stage_chain.py:870`](../../../tests/test_stage_chain.py#L870)
  `test_resistor_pair_rejection_is_gm_times_the_tail_resistance`: on ideal
  devices the nodal CMRR is exactly `gm·RT`, twice the closed form.
- [`test_stage_chain.py:900`](../../../tests/test_stage_chain.py#L900)
  `test_mirror_load_cmrr_is_set_by_the_pair_drain_imbalance`: a five-transistor
  OTA at a real gf180 operating point gives `≈ gm1/|gds2 − gds1|`, 61.4 dB as
  ngspice measured, and almost the same with an ideal tail.
- [`test_stage_chain.py:984`](../../../tests/test_stage_chain.py#L984) /
  [`L997`](../../../tests/test_stage_chain.py#L997): telescopic loads keep the
  closed forms; mirror loads get the nodal answer.
- [`test_stage_chain.py:1019`](../../../tests/test_stage_chain.py#L1019) to
  [`L1064`](../../../tests/test_stage_chain.py#L1064): supply-current KCL. A
  bias leg is one branch even when its diode sits in two slots; the reference
  diode is off-chip; every branch on `vdd!` counts whatever its slot; resistor
  tails reach degenerated pairs.
- [`test_stage_chain.py:617`](../../../tests/test_stage_chain.py#L617) to
  [`L855`](../../../tests/test_stage_chain.py#L855): headroom, follower and
  DDA swing gates, and FD symmetry.
- [`test_single_stage_output_node.py:62`](../../../tests/test_single_stage_output_node.py#L62)
  and [`L78`](../../../tests/test_single_stage_output_node.py#L78): the
  one-stage output node is the signal-path end that is not the diode, and
  `None` (fallback to the pair's drain) for a resistor load.

### Check yourself

1. Why does the walk put the input pair's source net in `stop`?
2. In `circuit_0010`, why is Rout1 lower with `bias_aware=True`?
3. Why does the code solve a whole DC network for CMRR instead of
   `gm1/(2·gd_tail)`, and which loads still get the formula?
4. Why does `vdd_supply_currents` not count the 20 µA in `mnref_bias_gen`?

<details><summary>Answers</summary>

1. For a differential signal the shared tail node does not move (one side goes
   up while the other goes down), so it is an AC ground. Without `stop` the
   walk would treat the tail as a resistor under the pair and boost the pair's
   `ro` like a cascode.
2. Each device is read at its own `Vds`. The mirror's output device sits at
   0.81 V, far below the table's `Vdd/2`, where its `gds` is 612 nS instead of
   472 nS. More conductance means less resistance: 1.162 MΩ instead of
   1.334 MΩ, and 81.5 dB instead of 82.7 dB.
3. A mirror load cancels the tail's common-mode current, so the tail formula
   follows the wrong path. What sets CMRR is the drain imbalance between the
   two pair devices, which only a network solve with real bias levels sees.
   Fully-differential chains and telescopic cascode loads still use the
   closed forms.
4. The bench injects the reference current into that diode from ground, so it
   never flows out of `vdd!`. Counting it would disagree with what SPICE
   measures as supply current (issue #285).

</details>

---

## 3.10 `metrics.py` (334 lines)

**Job in one sentence.** Turn a `StageChain` and a spec into `(metrics,
margins)`, with no device, technology or model in sight; and offer the
two-stage re-plan (which gm1, gm2 the full model needs).

**Design knowledge it encodes.** Each of the [eight spec metrics](00_background.md#spec-metrics),
and the margin convention: positive means the spec is met.

**Walkthrough.**
1. [`evaluate_metrics`](../../../circuitgenome/sizer/physics/metrics.py#L189),
   top to bottom:
   - **Gain** ([L235–L239](../../../circuitgenome/sizer/physics/metrics.py#L235)):
     the product of `gm·rout` per stage, the first one times `k_fs`, up to the
     first stage with infinite `rout`. Withheld when `gain_measurable` is false.
   - **GBW and PM, multi-stage** ([L250–L273](../../../circuitgenome/sizer/physics/metrics.py#L250)):
     always from a **full model** (#274). RNMC →
     [`_rnmc_phase_margin`](../../../circuitgenome/sizer/physics/metrics.py#L36)
     / [`_rnmc_gbw`](../../../circuitgenome/sizer/physics/metrics.py#L47) (worst
     half, 3.6); two-stage and NMC →
     [`_miller_loop`](../../../circuitgenome/sizer/physics/metrics.py#L58),
     which calls `eq.two_stage_loop` or `eq.nested_miller_loop` with the
     chain's gms, `1/rout`, `node_caps_f`, buffer, CMFB coupling and series
     resistors. If the loop never reaches 0 dB, GBW falls back to
     `gm1/(2π·Cc)` times the follower gain.
   - **GBW and PM, one-stage** ([L281–L298](../../../circuitgenome/sizer/physics/metrics.py#L281)):
     *load*-compensated, so `CL` takes Cc's place and the mirror pole is the
     non-dominant pole. Resistor loads get no PM (no internal node, #228).
   - **Slew** ([`_slew_rate`](../../../circuitgenome/sizer/physics/metrics.py#L154)):
     the slowest of every current-limited node, `I/C`: the first stage's
     `k_slew·I_tail` into Cc; the last stage's current into its output's caps
     (+CL unless buffered); NMC's inner cap; a follower into CL.
   - **Power** ([L301](../../../circuitgenome/sizer/physics/metrics.py#L301)):
     `(Vdd − Vss) · Σ supply_currents`.
   - **Swing** ([L306](../../../circuitgenome/sizer/physics/metrics.py#L306)):
     each rail minus its headroom.
   - **CMRR, PSRR** ([L320–L332](../../../circuitgenome/sizer/physics/metrics.py#L320)):
     `se_rejection_db` when present, else the closed forms `eq.cmrr_db` and
     `eq.psrr_db_approx`.
2. [`two_stage_replan`](../../../circuitgenome/sizer/physics/metrics.py#L97).
   The requirement side (3.7) sized gm1 and gm2 with closed forms that ignore
   the output pole's pull on the 0 dB crossing, the bare Miller cap's
   [RHP zero](00_background.md#rhp-zero) and the first-stage node's cap; a
   design sized exactly to the spec reads 2–5° short in the full model. So,
   on the sized chain: if GBW is short, find the smallest gm1 that meets
   `GBW·(1 + 0.5 %)`; then, at that gm1, the smallest gm2 that meets
   `PM + 0.5°` ([L143–L150](../../../circuitgenome/sizer/physics/metrics.py#L143)).
   The search is a geometric bisection (`least`, 40 steps,
   [L131](../../../circuitgenome/sizer/physics/metrics.py#L131)). `c1_at(gm2)`
   lets the caller say how the first-stage node's cap grows when the second
   stage is widened. It returns gm targets, not widths: the caller re-sizes
   ([two_stage_refine.py:69](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L69),
   [level1.py:163](../../../circuitgenome/sizer/analytical/level1.py#L163)).
   Analogy: tasting the soup after cooking and adding exactly enough salt.

**Running example.** On `circuit_0010`'s chain:
- gain 81.5 dB (table above);
- `_miller_loop` → `two_stage_loop(gm1 = 60 µS, gm2 = 575 µS, Cc = 1.25 pF,
  CL = 5 pF, c1 = 23.3 fF, g1 = 1/1.162 MΩ, g2 = 1/297 kΩ)` →
  **GBW 7.06 MHz, PM 63.2°** (SPICE 6.93 MHz, 63.5°). The closed forms would
  say 7.64 MHz and 67.3°;
- slew `min(1.0 · 20 µA / 1.25 pF, 50 µA / (1.25 + 5) pF)` = **8.0 V/µs**
  (the second stage charging Cc + CL is the slower node);
- power `3.3 V · 110 µA` = **0.363 mW**;
- swing `3.3 − 0.154 = 3.15 V` and `0 + 0.145 = 0.14 V`;
- CMRR 80.1 dB, PSRR 90.5 dB from `se_rejection_db`;
- `two_stage_replan` returns `(None, None)`: 7.06 MHz ≥ 2 MHz and
  63.2° ≥ 60°, so nothing is raised.

**Test to read.** All in [`tests/test_stage_chain.py`](../../../tests/test_stage_chain.py):
- [`L51`](../../../tests/test_stage_chain.py#L51) to
  [`L79`](../../../tests/test_stage_chain.py#L79): gain is the cascade product,
  `k_fs` scales the first stage only, the product stops at the first infinite
  `rout`.
- [`L104`](../../../tests/test_stage_chain.py#L104)
  `test_a_bare_miller_cap_pulls_the_crossing_under_its_output_pole` and
  [`L112`](../../../tests/test_stage_chain.py#L112)
  `test_a_nulling_resistor_cancels_the_output_pole`: the #274 full model,
  pinned to what SPICE measured.
- [`L181`](../../../tests/test_stage_chain.py#L181) to
  [`L207`](../../../tests/test_stage_chain.py#L207): `two_stage_replan`
  raises gm2 for PM (to 60.5°), raises gm1 for GBW first, and returns
  `(None, None)` for an unreachable spec.
- [`L277`](../../../tests/test_stage_chain.py#L277) to
  [`L322`](../../../tests/test_stage_chain.py#L322): every slew limit.
- [`L521`](../../../tests/test_stage_chain.py#L521): margin signs for min and
  max specs.

**Check yourself.**
1. `metrics.py` is "pure algebra". What would break that promise?
2. Why does `two_stage_replan` raise gm1 before gm2, and not the other way?
3. Which metric survives `gain_measurable = False`, and why?

<details><summary>Answers</summary>

1. Reading a device, a model or the technology. Everything it needs is in the
   chain; it does call `equations` (formulas and the nodal loop solvers).
2. A larger gm2 moves the output pole up, which only raises GBW further, so it
   never undoes the gm1 step. A larger gm1, on the other hand, costs phase
   margin, so PM must be fixed last, at the final gm1.
3. Slew (and power and swing): they depend on bias currents and caps, not on
   the small-signal operating point, so a railed operating point does not
   invalidate them (#148 gates only gain-derived metrics).

</details>

---

## Exit check

**#250's question:** "Nothing here decides a width or runs a simulator — does
it hold?"

<details><summary>Answer</summary>

**Mostly yes, with four caveats.**

- **No simulator.** Nothing in `physics/` imports ngspice or runs a
  subprocess. Two places do solve small *linear* circuits with numpy:
  [`_se_rejection_db`](../../../circuitgenome/sizer/physics/stage_chain.py#L637)
  (DC, for CMRR/PSRR) and
  [`_cmfb_coupling`](../../../circuitgenome/sizer/physics/stage_chain.py#L524),
  plus the loop models in `equations.py`. They are hand-analysis models (a
  `gm`/`gds`/`cgg` per device), not SPICE. One dependency points the "wrong"
  way: [`equations.py:18`](../../../circuitgenome/sizer/physics/equations.py#L18)
  imports two bench constants (`AC_LOOP_LIMIT_DB`, `AC_START_HZ`) from
  `verify.measure`.
- **No transistor width is output.** No function returns a `TransistorSizing`
  or a W for a circuit device. `rnmc.signal_cgs_estimate`
  ([L236](../../../circuitgenome/sizer/physics/rnmc.py#L236)) computes a width
  internally, but only to estimate a gate capacitance.
- **But physics does decide *values*.** It sets the passive sizes outright:
  load resistors
  ([`size_load_resistors`](../../../circuitgenome/sizer/physics/preprocess.py#L51))
  and compensation caps (`compute_requirements` →
  [`size_compensation_caps`](../../../circuitgenome/sizer/physics/preprocess.py#L105)).
  It also sets every gm target and Vdsat cap, and `design_rnmc` /
  `two_stage_replan` choose gm2, Cc2, gm3 and gm1. The sizers turn those into
  widths.
- **One function steers re-sizing.**
  [`lift_nodal_cmrr`](../../../circuitgenome/sizer/physics/preprocess.py#L649)
  takes the sizer as a `size(gm1)` callback and decides which `SizingResult`
  to keep. It never computes a width itself, but it is a sizing loop that
  lives in `physics/`.

So the rule holds for "widths" and "simulator", if "decides" means "outputs a
W". It is more precise to say: `physics/` decides *what the circuit must
deliver* (currents, gm, caps, resistors) and *what a sized circuit
delivers* (the chain and its metrics); `gmid/` and `analytical/` decide
the widths in between.

</details>

**Follow-up from #250:** the running-example gaps measured on `a193951`
(PM 67° vs 83°, power 0.56 vs 0.37 mW, CMRR 41 vs 78 dB, PSRR 58 vs 86 dB) are
now closed (63.2 vs 63.5°, 0.363 vs 0.378 mW, 80 vs 78 dB, 90 vs 87 dB). Which
code in `physics/` made each one right?

<details><summary>Answer</summary>

Re-running `run_example.py` on an extracted copy of `a193951`, with the same
`circuit_0010` netlist, reproduces #250's numbers exactly (PM 67.3° / 83.3°,
power 0.561 / 0.373 mW, CMRR 40.6 / 77.6 dB, PSRR 58.4 / 86.3 dB). The sizing
is the same as today's except `mp1_second_stage` (100 µm then; 105.2 µm in two
fingers now). So the gaps came from how each number was *computed*:

| Metric | `a193951` formula | Now | Code that fixed it | PR |
|---|---|---|---|---|
| **PM** (analytical) | `90° − atan(gm1·CL/(gm2·Cc))` = 67.3° | full nodal loop: 63.2° | [`metrics._miller_loop`](../../../circuitgenome/sizer/physics/metrics.py#L58) → [`equations.two_stage_loop`](../../../circuitgenome/sizer/physics/equations.py#L625) / [`_loop_gbw_pm`](../../../circuitgenome/sizer/physics/equations.py#L597), fed by `StageChain.node_caps_f` | #279 (issue #274), commit `1c0f2c9` |
| **PM** (SPICE) | bench had **no load cap**: 83.3° | 63.5° | not physics: `verify/deck` now hangs `CL` on every output | #237 (issue #222), commit `ad9347a` |
| **Power** | `ibias + ibias·ratio + ibias·(bias_gen devices)` = 20 + 50 + 5·20 µA = 170 µA | 110 µA | [`stage_chain.vdd_supply_currents`](../../../circuitgenome/sizer/physics/stage_chain.py#L352) | #291 (issue #285), commit `e210407` |
| **CMRR** | `gm1/(2·gd_tail)` = 40.6 dB | 80.1 dB | [`_se_rejection_db`](../../../circuitgenome/sizer/physics/stage_chain.py#L637) via [`_circuit_rejection_db`](../../../circuitgenome/sizer/physics/stage_chain.py#L737), gated by [`reports_nodal_rejection`](../../../circuitgenome/sizer/physics/stage_chain.py#L724) | #294 (issue #290), commit `a41828b` |
| **PSRR** | `gm2/gd_output_load` = 58.3 dB | 90.5 dB | same nodal solve (third stimulus, `vdd!`) | #294 (issue #290) |

Why each one was wrong, in plain words:

- **PM, two causes.** On the SPICE side, the old bench measured an *unloaded*
  amplifier. Today's bench run with `CL = 1 fF` still gives 83.4° and
  7.47 MHz, the old numbers. Attaching `CL` moved the SPICE number by ~20°;
  the model only had to move ~4°. On the model side, the closed form ignores the bare Miller cap's
  [right-half-plane zero](00_background.md#rhp-zero) (at `gm2/(2π·Cc)` =
  73 MHz, ~5° of lag at a 7 MHz crossing) and the output pole pulling the
  crossing down (7.64 → 7.06 MHz). The full model sees both; the first-stage
  node cap adds only 0.4°. Today's closed form still says 67.3° for this chip,
  so `two_stage_loop` is what makes the analytical 63.2° match.
- **Power.** The old sum counted `ibias` once per bias-generator device. That
  includes `m1_tail_current` (already counted as the tail) and
  `mnref_bias_gen`, whose current comes from the external reference, not from
  `vdd!`. KCL at the rail counts four branches: two tail-mirror legs (20 µA
  each), the second stage (50 µA) and the `mp5` bias leg (20 µA). The
  remaining 4 % against SPICE (0.363 vs 0.378 mW) is SPICE's real branch
  currents differing slightly from the planned ones; not traced further here.
- **CMRR.** A mirror load cancels what the tail lets through, so `gd_tail` is
  the wrong path. The nodal solve, reading each device at its own `Vds` with
  the output pinned at Vcm, sees the pair's drain imbalance (0.841 V vs
  0.807 V) that actually sets it. It depends on
  [`node_levels`](../../../circuitgenome/sizer/physics/node_levels.py#L72)
  and the bias-aware `gds` from #273 (PR #277). `lift_nodal_cmrr` (#296) is
  not involved: this spec has no CMRR target.
- **PSRR.** The closed form assumes supply ripple leaks only through the
  second stage's load `gds`. In this circuit the PMOS sources' gate rails
  (`net_bias5`, `net_bias7`) come from diodes tied to `vdd!`, so they move with
  the supply and the PMOS `Vsg` stays nearly constant. The nodal solve sees
  that; holding either gate still in the same network drops PSRR to 37–38 dB.

Bonus, not in #250's list: **gain** moved from 82.7 dB to 81.5 dB (SPICE
81.2 dB) because the evaluator now builds the chain `bias_aware`
([evaluate.py:112](../../../circuitgenome/sizer/gmid/evaluate.py#L112)),
reading the mirror load at 0.81 V (#273, PR #277, commit `68df946`). And
**slew** moved from 16 to 8 V/µs (SPICE 8.9) because `_slew_rate` now takes
the slowest of every current-limited node, not just `I_tail/Cc`.

</details>

---

## Simplification notes

### 3.1–3.4 and 3.8 (taxonomy, equations, gmid_lut, device_model, node_levels)

Logged while writing these sections. Not fixed.

| Location | Observation |
|---|---|
| [`equations.py:18`](../../../circuitgenome/sizer/physics/equations.py#L18) | `physics/` imports `verify.measure` (for `AC_START_HZ`, `AC_LOOP_LIMIT_DB`), so the math layer depends on the simulator layer and loads `verify.deck`/`verify.rig` on import. It breaks the "physics never touches verify" layering; the two constants could live in `physics` (or `models`) and be imported by `verify`. |
| [`equations.py:1–13`](../../../circuitgenome/sizer/physics/equations.py#L1) | Module docstring is stale: titled "Level-1 MOSFET equations", and says only RNMC is solved numerically. Two-stage and nested-Miller are numeric too since #274 (`two_stage_loop`, `nested_miller_loop`), and most of the file is op-amp metrics. |
| [`equations.py:311`](../../../circuitgenome/sizer/physics/equations.py#L311) | `phase_margin_three_stage_deg` has no production caller; only `test_rnmc.py:67` (as the "old formula") and `docs/theory/sizing_flow.rst:970` use it. |
| [`equations.py:782`](../../../circuitgenome/sizer/physics/equations.py#L782) vs [`:597`](../../../circuitgenome/sizer/physics/equations.py#L597) | RNMC PM takes the min over plain crossings, while two-stage/NMC go through `_loop_gbw_pm`, which also counts "returns" within 0.5 dB of 0 dB. The two paths disagree on the rule, and RNMC builds the same network twice (once for PM, once for GBW in `unity_gain_bw_rnmc_hz`). |
| [`equations.py:610–616`](../../../circuitgenome/sizer/physics/equations.py#L610) | `_loop_gbw_pm` calls `_crossings` (which runs `_sweep`) and then runs `_sweep` again on the same matrices. |
| [`stage_chain.py:610`](../../../circuitgenome/sizer/physics/stage_chain.py#L610) | Uses the private `eq._poles`: another cross-module private helper (README candidate #2). |
| `equations.py` family E docstrings | For the "rewrite docstrings" TODO: `_Network` and its methods use "VCCS", "drawn out of node", "Vin = 1" undefined; `_crossings` names "false position" unexplained; `_loop_gbw_pm`'s "return" concept needs a plain sentence. `open_loop_gain_measurable` and `rnmc_min_gm2` lead with mechanism rather than purpose. `psrr_db_approx` formula says `gd_bias_mirror`, param is `gd_bias`. Families A, C, F are already clear. |
| [`gmid_lut.py:147`](../../../circuitgenome/sizer/physics/gmid_lut.py#L147) | `max_gm_id(dtype, l_um)` ignores both arguments (returns the axis end). |
| [`gmid_lut.py:67`](../../../circuitgenome/sizer/physics/gmid_lut.py#L67) vs [`:40`](../../../circuitgenome/sizer/physics/gmid_lut.py#L40) | `_row` re-implements the clamp-and-bracket logic that `_bracket` already provides (used only by `_bias_lookup`). |
| [`device_model.py:268`](../../../circuitgenome/sizer/physics/device_model.py#L268) vs [`:352`](../../../circuitgenome/sizer/physics/device_model.py#L352) | A `SIGNAL` device with no gm target (e.g. `circuit_0010`'s mirror load) is sized by `geometry_for` at the current-source gm/Id (10), while `gds_estimate` for the same role assumes `signal_nominal_gmid` (14). The pre-geometry estimate and the built device disagree on its operating point. |
| [`device_model.py:216–229`](../../../circuitgenome/sizer/physics/device_model.py#L216) | `GmIdPolicy` defaults duplicate the constants in [`gmid/intent.py:76–84`](../../../circuitgenome/sizer/gmid/intent.py#L76), and `plan._model_for` always overwrites them. Its docstring reasons from 45 nm (gm/gds ≈ 7 at Lmin). |
| [`device_model.py:143`](../../../circuitgenome/sizer/physics/device_model.py#L143), [`:12`](../../../circuitgenome/sizer/physics/device_model.py#L12) | "byte-for-byte the current generic behaviour" / "reproduces the existing numbers exactly" are migration-era phrasing; no "existing" code remains to compare against. (The `sizer.sizer` and "PTM nodes" errors are README #4 and #6.) |
| [`taxonomy.py:38`](../../../circuitgenome/sizer/physics/taxonomy.py#L38) | `is_signal_device` means "gate not on a bias net", not "carries the signal": a mirror load (gate on its diode net, `circuit_0010`'s `m1_load`) returns True, and [`preprocess.py:359`](../../../circuitgenome/sizer/physics/preprocess.py#L359) picks its `gds_estimate` role from it. A name like `is_signal_gated` or a docstring note would prevent misreading. |
| [`taxonomy.py:33–35`](../../../circuitgenome/sizer/physics/taxonomy.py#L33) | `"0"` is in `RAILS` but not in `BIAS_NETS`, so a gate tied to net `0` counts as a signal gate while `gnd!` does not. `BIAS_NETS` is used only inside this file. |
| [`taxonomy.py:16`](../../../circuitgenome/sizer/physics/taxonomy.py#L16) | Comment says `HALF_BIAS_SLOTS` carry `iBias/2`; `assign_ids` actually gives `ibias / n` (n same-polarity devices in the slot), which is `ibias/2` only for a plain pair. |
| [`__init__.py:1–18`](../../../circuitgenome/sizer/physics/__init__.py#L1) | The package docstring's module map omits `node_levels` and `rnmc` (and lists `equations` / `gmid_lut` only as `device_model`'s backends). |

### 3.5–3.7, 3.9–3.10 (circuit_view, rnmc, preprocess, stage_chain, metrics)

| Location | Observation |
|---|---|
| `circuit_view.py:121-122` | Orphan comment ("Overdrive (V) used when sizing a load resistor...") with no constant under it; the constant is `preprocess._RESISTOR_LOAD_OVERDRIVE` (`preprocess.py:34`) |
| `circuit_view.py:158` `_deduplicate_devices` | Priority list has no `cmfb`, so a device shared by `cmfb` and `bias_gen` goes to `bias_gen`; the docstring lists a shorter order than the code |
| `circuit_view.py` | `_adopt_orphan_mosfets`, `_deduplicate_devices` and `_check_topology_match` have no unit test; `test_circuit_view.py` only tests the advisory text |
| `preprocess.py:505-506` | Comment says "gm/Id delivers the request"; `GmIdModel.realized_gm` floors at the LUT's smallest gm/Id (`circuit_0010`: 15.7 µS asked, 60 µS delivered) |
| `preprocess.py:305` `compute_requirements` | ~340 lines, 5-tuple return, one-, two- and three-stage plus NMC and RNMC rules in one body |
| `preprocess.py:649` `lift_nodal_cmrr` | A sizing loop (callback into the sizer, judges `SizingResult`s) living in `physics/` |
| `stage_chain.py:32`, `stage_chain.py:610` | More private helpers across modules: `preprocess._mirror_reference` (next to `_first_stage_gain_factor`, already in README #2) and `equations._poles` |
| `stage_chain.py:870-871` `_follower` | Re-spells the output-stage slot tuple instead of using `_OUTPUT_STAGE_ORDER` (`stage_chain.py:311`) |
| `stage_chain.py:521`, `stage_chain.py:615` | Port names hard-coded (`"out"`, `("outp", "outn")`); the nodal CMRR/PSRR returns `None` for an output port named anything else |
| `stage_chain.py:1076` `build_stage_chain` | Always runs the nodal CMRR/PSRR solve, supply KCL, swing and CMFB coupling, also for the re-plan callers (`rnmc_refine`, `two_stage_refine`, Level-1 re-plan) that only read stages and node caps |
| `stage_chain.py:637`, `stage_chain.py:524`, `equations.py:411` | Three separate nodal-matrix builders (`_se_rejection_db`, `_cmfb_coupling`, `equations._Network`) with their own stamping code |
| `stage_chain.py:157` `StageChain` | 20 fields; several serve one path only (`second_stage_mirror_pole_hz` NMC, `third_stage_mirror_pole_hz` RNMC, `cmfb_coupling` FD); `gd_tail`/`gd_output_load` are computed on SE chains where the nodal values are reported instead |
| `stage_chain.py:1258-1264` | Node caps use `cgs` for RNMC and `cgg` for every other scheme (kept on purpose, #266-#270); two gate-cap models side by side |
| `stage_chain.py:1086` `bias_aware` | Only the gm/Id evaluator passes `True`; the Level-1 sizer's reported gain stays on the single-point read |
| `rnmc.py:257` `design_warnings` | The "gm3 capped" advisory cites the stability bound `gm3 ≥ gm2·(1+CL/Cc1)` even when the ζ/PM target drove the cap (RNMC SE fixture: gm3 capped to 1.0 mS with the bound at 4.1 mS) |
| `rnmc.py:246` `signal_cgs_estimate` | Duck-types the model (`hasattr(model, "geometry_for")`) and does its own square-law width; a `DeviceModel` method would remove the branch |
