# Stop 5 — `analytical/`: the Level-1 CP-SAT sizer

**Files**:
[`analytical/constraints.py`](../../../circuitgenome/sizer/analytical/constraints.py) (206 lines) ·
[`analytical/level1.py`](../../../circuitgenome/sizer/analytical/level1.py) (193 lines) ·
[`analytical/__init__.py`](../../../circuitgenome/sizer/analytical/__init__.py) (4 lines) ·
companion reading: [`docs/theory/sizing_flow.rst`](../../theory/sizing_flow.rst) (1206 lines, read about 600 of them) and
[`docs/_extra/walkthrough/analytical/`](../../_extra/walkthrough/analytical/index.html) (3 HTML pages)

**Time**: about 1 h

**After this stop you can**:
- explain how CP-SAT (a constraint solver) turns "gm ≥ gm_req" into a search over integer widths and lengths;
- trace `size_level1()` from the recognized circuit to a `SizingResult`, and name each `physics/` function it calls;
- say why `circuit_0010` gets very different W/L on `generic` than on gf180, and why `solver_status == "OPTIMAL"` does not mean "the spec is met".

---

## Background for this stop

Concepts from the primer that this stop uses:

- [The square law](00_background.md#square-law): `gm = √(2·µCox·(W/L)·Id)`, `Vdsat = √(2·Id·L/(µCox·W))`, `gds = λ·Id`. This is the whole device model on this path.
- [gm](00_background.md#gm) and its hard ceiling `gm ≤ ~25·Id` ([weak inversion](00_background.md#inversion)). The square law has no such ceiling, so the code adds one by hand.
- [Vdsat / headroom](00_background.md#vdsat): the output-swing specs become "Vdsat ≤ some maximum" limits on the output devices.
- [Current mirror](00_background.md#current-mirror): `I_out / I_ref = (W/L)_out / (W/L)_ref`. The solver has to respect this, or the bias currents it assumed will not flow.
- [Two strategies: search vs forward pass](00_background.md#two-strategies): this stop is the **search** side. Stop 4 was the forward pass.
- [Technology](00_background.md#technology): `generic` is a made-up, card-less 0.25 µm-like tech. Its YAML ([`tech_generic.yaml`](../../../circuitgenome/sizer/config/tech_generic.yaml)) carries `mu_cox`, `vth`, `lam`, `cox` and a **1 µm grid** for both W and L.

### New concept: constraint programming and CP-SAT

**In plain words.** Constraint programming is a way to solve a problem by
*describing* the answer instead of computing it step by step. You declare three
things:

1. **Integer variables** with a range. Here: `W_m1_input_pair ∈ {1 … 600}` and
   `L_m1_input_pair ∈ {1 … 10}`, counted in grid steps.
2. **Linear constraints**: rules like `900·W ≥ 2014·L` or `W_a == W_b`. Each
   side is a sum of `integer coefficient × variable`. Square roots, `W/L`
   divisions and `W·L` products are not allowed in this form.
3. **An objective**: a linear expression to minimise, here `ΣW`.

A **solver** then searches the space and returns the best assignment, with a
status:

| Status | Meaning |
|---|---|
| `OPTIMAL` | found an assignment and proved none is better |
| `FEASIBLE` | found one but ran out of time before proving it is the best |
| `INFEASIBLE` | proved no assignment satisfies every rule |
| `UNKNOWN` | ran out of time without finding any |
| `MODEL_INVALID` | the model itself is malformed |

**CP-SAT** is Google OR-Tools' constraint solver. It is not a brute-force grid
search: it prunes. "W ≥ 3" plus "W_a == W_b" immediately removes most of the
grid without trying it. For software engineers it is close to a SAT/SMT solver
with an optimiser on top.

**Analogy.** A Sudoku app. You state the rules (each row holds 1–9 once, etc.);
you never write the steps to solve it. The app finds a filling that obeys every
rule at once. Here the "cells" are the W and L of every transistor, and the rules
are the gm floors, Vdsat ceilings, matched pairs and mirror ratios. The analogy
breaks in one place: Sudoku has one answer, while here many fillings are valid
and the objective picks the narrowest.

**The linearisation trick.** The physics is not linear: `gm` has a square root
and a `W/L` ratio. But each per-device current `Id` is fixed *before* the solve
(by [KCL](00_background.md#kcl), in `assign_ids`). With `Id` a constant:

```
gm ≥ gm_req
⇔ √(2·µCox·(W/L)·Id) ≥ gm_req           both sides positive: square them
⇔ 2·µCox·Id·(W/L)    ≥ gm_req²          multiply by L (positive)
⇔ 2·µCox·Id · W      ≥ gm_req² · L       linear in W and L
```

```
Vdsat ≤ Vmax
⇔ √(2·Id·L/(µCox·W)) ≤ Vmax
⇔ 2·Id · L           ≤ µCox·Vmax² · W    linear in W and L
```

Each one is a straight line through the origin in the (L, W) plane, and the legal
region is everything on one side of it. The trick only works because `Id` is a
constant. If the solver also had to choose currents, `Id·W` would be a product
of two variables, which is not linear.

**Why scale by 10¹².** CP-SAT accepts only integer coefficients. Physical ones
are tiny: `2·µCox·Id = 2 · 90e-6 · 5e-6 = 9e-10`. Rounding that to an integer
gives 0 and the rule disappears. Multiplying every coefficient by
`_SCALE = 10¹²` turns it into `900`. Both sides of each inequality are scaled by
the same factor, so the rule keeps its meaning. It is a change of units, like
writing "9 µg" instead of "0.000000009 kg". The rounding is exact only to about
one part in the coefficient's size: a `gm_req` of 1 µS would round to 1, and one
of 0.4 µS to 0. That is fine on `generic`'s 1 µm grid at µA currents. It would not
be on a fine-grid PDK, but those never reach this path.

---

## 5.1 `analytical/constraints.py` (206 lines)

**Job in one sentence.** Build the CP-SAT model: one integer W and L per
transistor, the gm and Vdsat rules, matched-pair and mirror equalities, and the
"narrowest total width" objective. It does not solve the model.

**Design knowledge it encodes.**
- [Square law](00_background.md#square-law) `gm` and `Vdsat`, rewritten in linear form (see Background).
- [Differential pair](00_background.md#diff-pair) matching: the two halves must be identical.
- [Current mirror](00_background.md#current-mirror) ratios: output W scales with its current.
- [Fully differential](00_background.md#fully-differential) symmetry: the p and n halves of each gain stage are twins.
- The manufacturing grid: W and L only come in whole steps.

**Walkthrough.** One function, [`build_model`](../../../circuitgenome/sizer/analytical/constraints.py#L43), in six blocks:

1. **Variables**, [constraints.py:71-83](../../../circuitgenome/sizer/analytical/constraints.py#L71). The tech's µm limits are divided by the grid step, and every transistor gets `W[ref]` and `L[ref]` as integer step counts. On `generic` (step = 1 µm) a step *is* a µm.
2. **gm floors**, [constraints.py:85-98](../../../circuitgenome/sizer/analytical/constraints.py#L85). For every device with `gm_req > 0` and a current, add `lhs·W ≥ rhs·L`. The grid steps are folded into the coefficients:
   ```python
   lhs = _coeff(2.0 * params.mu_cox * abs(ids_a) * w_step)  # coefficient of W
   rhs = _coeff(gm_req ** 2 * l_step)                        # coefficient of L
   if lhs > 0 and rhs > 0:
       model.add(lhs * W[ref] >= rhs * L[ref])
   ```
   This is the [gm](00_background.md#gm) requirement, in linear form.
3. **Vdsat ceilings**, [constraints.py:100-112](../../../circuitgenome/sizer/analytical/constraints.py#L100). The same pattern mirrored: `2·Id·l_step·L ≤ µCox·Vmax²·w_step·W`. This is the [headroom](00_background.md#vdsat) budget coming from the output-swing specs.
4. **Matched pairs inside a slot**, [constraints.py:114-129](../../../circuitgenome/sizer/analytical/constraints.py#L114). In `input_pair`, `load` and `tail_current`, devices are grouped by `(type, planned Id)`, and every member of a group gets the same W and L as the first. Grouping by current matters: a folded-cascode load holds same-type devices at different currents, and those must not be forced equal.
5. **FD half symmetry**, [constraints.py:131-143](../../../circuitgenome/sizer/analytical/constraints.py#L131). `second_stage_p` ↔ `second_stage_n` (and the third-stage pair) get equal W and L.
6. **Mirror ratios**, [constraints.py:145-179](../../../circuitgenome/sizer/analytical/constraints.py#L145). MOSFETs are grouped by `(gate net, type)`. A diode-connected member (gate = drain) is the reference. Every other member gets the same L, plus `den·W_out == num·W_ref`, where `num/den = I_out/I_ref` comes from `Fraction(...).limit_denominator(100)`. Without this rule, an output device's W would be set only by its own gm or Vdsat target, and the mirror would copy an arbitrary current (#67).
7. **Objective**, [constraints.py:181-182](../../../circuitgenome/sizer/analytical/constraints.py#L181): `model.minimize(sum(W.values()))`. *Verified:* it minimises the **sum of widths in grid steps**. Length is not in the objective, and neither is area `W·L`. The comment calls it a "proxy for power and area". Area, yes. Power, no: every current is already fixed before the solve, so W does not change the supply current. A narrower device does have smaller capacitances, though.
8. **Search hint**, [constraints.py:184-204](../../../circuitgenome/sizer/analytical/constraints.py#L184). `add_decision_strategy` says: decide the `bias_gen` variables first, trying small values first. This changes how fast the solver finds an answer, not which answers are allowed.

Helper: [`_coeff`](../../../circuitgenome/sizer/analytical/constraints.py#L38) is `round(value · 10¹²)`.

**Why a longer L is never "free".** With `L` absent from the objective, why does
the solver not pick L = 10? In rule 2, `W ≥ (rhs/lhs)·L`, so a longer L forces a
proportionally wider W, which the objective punishes. Devices with no gm or
Vdsat rule (the tail, most of the bias generator) have no such pressure. Their L
is free as far as the objective goes, and the `SELECT_MIN_VALUE` hint is what
lands them on L = 1.

**Running example.** `circuit_0010` on `generic` with
[`spec_generic.yaml`](../../../examples/two_stage_se_specs/spec_generic.yaml)
(ibias 10 µA, Vdd 5 V, CL 20 pF; the requirement maps come from
`compute_requirements`, Stop 3). The rules CP-SAT receives:

| Device | Id | Rule | Integer form (L in steps) | Smallest W |
|---|---|---|---|---|
| `m1/m2_input_pair` (PMOS) | 5 µA | gm ≥ 44.88 µS | `900·W ≥ 2014·L` | 3 (at L = 1) |
| `mn1_second_stage` (NMOS) | 25 µA | gm ≥ 625 µS | `13500·W ≥ 390625·L` | 29 |
| `mp1_second_stage` (PMOS) | 25 µA | Vdsat ≤ 0.4 V | `5·10⁷·L ≤ 1.44·10⁷·W` | 4, then mirror rule gives 5 |
| `m1/m2_load` (NMOS) | 5 µA | Vdsat ≤ 0.4 V | trivially met at W = 1 | 1 |
| `mp5_bias_gen` ↔ `mp1_second_stage` | 10 / 25 µA | mirror 25/10 = 5/2 | `2·W_mp1 == 5·W_mp5` | `W_mp5 = 2`, `W_mp1 = 5` |
| tail, `mnref/mn5/mn7_bias_gen` | 10 µA | mirrors 1:1 only | `W == W_ref`, `L == L_ref` | 1 |

The `mp1`/`mp5` row shows the rules interacting. The Vdsat rule alone allows
`mp1` at W = 4, but the 5:2 mirror needs `W_mp1` to be a multiple of 5, so the
solver picks 5. That drags `mp5_bias_gen` from 1 to 2. Total ΣW = 49 steps.

**Test to read.**
- [`test_sizer.py::test_current_mirror_ratios_enforced`](../../../tests/test_sizer.py#L569): on the same circuit as `circuit_0010` (the [`two_stage_fbr`](../../../tests/test_sizer.py#L400) fixture: PMOS pair, NMOS mirror load, PMOS tail, NMOS CS, Miller cap), asserts `W_mp1 = 2.5·W_mp5` with equal L, and a 1:1 tail mirror.
- [`test_sizer.py::test_size_two_stage_symmetry`](../../../tests/test_sizer.py#L553): the input pair, load and tail halves come out with equal W and L.
- There is no unit test of `build_model` on its own. It is tested only through `size_circuit`.

**Check yourself.**
1. Why must `Id` be known *before* `build_model` runs?
2. A device has `gm_req = 0`. Which rules can still constrain its W?
3. If `generic` had a 0.1 µm width step, how would the coefficient of `W` in the input pair's gm rule change?

<details><summary>Answers</summary>

1. The linear form `2·µCox·Id·W ≥ gm_req²·L` treats `2·µCox·Id` as a coefficient. If `Id` were a variable, `Id·W` would be a product of two variables, which is not linear, and CP-SAT's linear rules could not express it.
2. Its Vdsat ceiling (if it is an output-swing device), matched-pair equality, FD half symmetry, and mirror ratios ([constraints.py:89](../../../circuitgenome/sizer/analytical/constraints.py#L89) only skips the gm rule). With none of those, only the W range `[w_min, w_max]` applies, and the objective pushes it to the minimum.
3. `w_step` is a factor in `lhs`, so it drops from 900 to 90 per step. Each step is then worth a tenth as much gm, and the solver needs ten times more steps for the same µm.

</details>

---

## 5.2 `analytical/level1.py` (193 lines)

**Job in one sentence.** The Level-1 pipeline: get the shared structural view,
currents and requirements from `physics/`, ask CP-SAT for the geometry, re-solve
once or twice so a two-stage amplifier meets GBW and phase margin in the full
model, then score the result with the shared metrics.

**Design knowledge it encodes.**
- [KCL](00_background.md#kcl) first, geometry second: currents are fixed before any W is chosen.
- The [square law](00_background.md#square-law), through `Level1Model`, the `DeviceModel` with square-law formulas behind it.
- [Miller](00_background.md#miller) two-stage compensation: the [GBW](00_background.md#gbw) / [phase-margin](00_background.md#phase-margin) re-plan (#274).
- [CMRR](00_background.md#cmrr) on a mirror load is read from a nodal solve, so a CMRR target is sized by re-solving (#296).

**Walkthrough.**

1. **Structure**, [level1.py:56](../../../circuitgenome/sizer/analytical/level1.py#L56). `analyze_circuit(fbr_result, topology)` returns the shared [`CircuitView`](../../../circuitgenome/sizer/physics/circuit_view.py#L24): devices by slot, one entry per device, the compensation scheme, and warnings. This is the *plain* view. The gm/Id path wraps it in `GmIdCircuitView`, which adds typed blocks, load kinds and cascode refs ([gmid/analyze.py](../../../circuitgenome/sizer/gmid/analyze.py)). Level-1 never builds that block view.
2. **Currents**, [level1.py:59](../../../circuitgenome/sizer/analytical/level1.py#L59). `assign_ids` is KCL: tail = ibias, each pair half = ibias/2, second stage = ratio·ibias.
3. **Resistor loads**, [level1.py:61-64](../../../circuitgenome/sizer/analytical/level1.py#L61). `size_load_resistors` solves resistors directly (no solver). Their conductance `gd_load_r` feeds the first-stage [Rout](00_background.md#rout).
4. **Device model**, [level1.py:67](../../../circuitgenome/sizer/analytical/level1.py#L67). `Level1Model(tech)` ([device_model.py:142](../../../circuitgenome/sizer/physics/device_model.py#L142)) wraps `equations.gm`, `gd`, `vds_sat`, `cgs`, `gm_ceiling` behind the same interface `GmIdModel` offers.
5. **CMRR fork**, [level1.py:69-72](../../../circuitgenome/sizer/analytical/level1.py#L69). With no CMRR target, or a chain whose CMRR has a closed form, call `_size_planned` once. Otherwise `lift_nodal_cmrr` calls it up to three times with a raised `cmrr_gm1` floor, keeping a re-size only if it costs no other spec.
6. **Requirements**, [level1.py:91-97](../../../circuitgenome/sizer/analytical/level1.py#L91). `compute_requirements` turns the spec into `gm_req_map` and `vod_max_map` plus `Cc` (the "five steps" of `sizing_flow.rst`, Stop 3). `size_compensation_caps` puts `Cc` onto the netlist's capacitors.
7. **Solve**, [level1.py:99-121](../../../circuitgenome/sizer/analytical/level1.py#L99). The nested `_solve(gm_req_map)` calls `build_model`, runs `CpSolver` with `max_time_in_seconds = time_limit_s` (30 s by default), and converts steps back to µm. It then recomputes `vgs` and `vds_sat` through `Level1Model`, so the reported values come from the same square law. No `fingers` are set (default 1).
8. **No solution**, [level1.py:123-133](../../../circuitgenome/sizer/analytical/level1.py#L123). An empty `transistors` map and the status name (`INFEASIBLE`, `UNKNOWN`, ...), but resistors, caps and warnings are still returned.
9. **Two-stage re-solve**, [level1.py:139-180](../../../circuitgenome/sizer/analytical/level1.py#L139). Build the [stage chain](03_physics.md), then up to `_TWO_STAGE_PASSES = 2` times:
   - `two_stage_replan` ([metrics.py:97](../../../circuitgenome/sizer/physics/metrics.py#L97)) asks: "what gm1 meets GBW, and then what gm2 meets PM, in the *full* nodal model?" The closed forms in step 6 leave out the output pole and the [RHP zero](00_background.md#rhp-zero), which cost 2–5°.
   - The search caps each gm at `min(gm at W_max, gm_ceiling)` ([level1.py:150-152](../../../circuitgenome/sizer/analytical/level1.py#L150)).
   - The first-stage node's capacitance as gm2 grows is modelled as `c1 + Cgs·((gm/gm_now)² − 1)` ([level1.py:164](../../../circuitgenome/sizer/analytical/level1.py#L164)). Under the square law `W ∝ gm²` at fixed Id and L, and `Cgs ∝ W`.
   - The raised gm values are merged into `req` with `max` and CP-SAT is re-run. The loop stops as soon as nothing needs raising or a re-solve fails.
10. **Score**, [level1.py:182-193](../../../circuitgenome/sizer/analytical/level1.py#L182). `evaluate_metrics(chain, spec)` gives `metrics` and `margins`. `open_loop_gain_measurable` flags gains too high for the open-loop bench. `bias_feasible` is never set, so it keeps its default `True` ([models.py:300](../../../circuitgenome/sizer/models.py#L300)): this path has no DC operating-point check.

**One subtlety: CP-SAT always rounds gm up.** The rule `gm ≥ gm_req` on a whole-step
W grid delivers *more* gm than asked (input pair: 44.88 µS requested, W = 3
delivers 51.96 µS). A higher gm1 lowers phase margin, so `compute_requirements`
sizes gm2 against the gm1 the grid will actually deliver, via
[`Level1Model.realized_gm`](../../../circuitgenome/sizer/physics/device_model.py#L196),
which repeats CP-SAT's rounding by hand ([preprocess.py:511](../../../circuitgenome/sizer/physics/preprocess.py#L511)).
`GmIdModel.realized_gm` just returns the request, since the forward pass does not round gm up to a grid.

**Running example.** `circuit_0010`, `generic`, `spec_generic.yaml`
(2026-10-31, `8571bf9`). How it was run, adapted from the README's script:

```python
tech = load_tech("generic")
spec = load_spec("examples/two_stage_se_specs/spec_generic.yaml")
result = size_circuit(parsed, sr, fbr, topology, tech, spec)     # -> size_level1
spice = simulate_metrics(netlist, result, tech, spec)            # Level-1 card, see below
print(result.solver_status, result.capacitors, result.warnings)
for ref, s in result.transistors.items(): print(ref, s.w_um, s.l_um, s.ids_a)
```

Status `OPTIMAL` in 0.4 s. Cc = 2.86 pF (= ibias / SR = 10 µA / 3.5 V/µs).
Sizing (W/L in µm; every L is 1 µm, the grid minimum):

| Device | `generic` W/L @ Id | Vdsat | gf180 W/L @ Id (README) |
|---|---|---|---|
| `m1/m2_input_pair` | 3/1 @ 5 µA | 0.19 V | 3.07/0.56 @ 10 µA |
| `m1/m2_load` | 1/1 @ 5 µA | 0.19 V | 2.50/0.56 @ 10 µA |
| `m1/m2_tail_current` | 1/1 @ 10 µA | 0.47 V | 42.1/1.12 @ 20 µA |
| `mn1_second_stage` | 29/1 @ 25 µA | 0.08 V | 17.9/0.56 @ 50 µA |
| `mp1_second_stage` | 5/1 @ 25 µA | 0.33 V | 105.2/1.12 @ 50 µA (2 fingers) |
| `mnref/mn5/mn7_bias_gen` | 1/1 @ 10 µA | 0.27 V | 9.76/1.12 @ 20 µA |
| `mp5_bias_gen` | 2/1 @ 10 µA | 0.33 V | 42.1/1.12 @ 20 µA |

Metrics:

| Metric | Spec | analytical | ngspice (Level-1 card) | margin |
|---|---|---|---|---|
| Gain | ≥ 80 dB | 90.1 dB | 91.1 dB | +10.1 |
| GBW | ≥ 2.5 MHz | 2.54 MHz | 2.93 MHz | +0.04 MHz |
| Phase margin | ≥ 60° | **57.9°** | 59.1° | **−2.1** |
| Slew | ≥ 3.5 V/µs | **1.09 V/µs** | 1.37 V/µs | **−2.4 V/µs** |
| Power | ≤ 1 mW | 0.275 mW | 0.324 mW | +0.725 mW |
| Swing max | ≥ 4.6 V | 4.67 V | 5.00 V | +0.07 |
| Swing min | ≤ 0.4 V | 0.08 V | 0.01 V | +0.32 |
| CMRR | — | 87.7 dB | 79.5 dB | — |
| PSRR | — | 87.4 dB | 82.1 dB | — |

Warning: `second-stage gm requirement exceeds the weak-inversion ceiling`.

What these numbers teach:

- **`OPTIMAL` is about the model, not the spec.** The solver found the narrowest grid point that obeys every rule it was *given*. Two specs are still missed, and the only signal is the negative `margins`. Both misses are known and pinned by tests (below).
- **Why PM is short.** The PM rule needs gm2 ≈ 630 µS. That is above the [gm ceiling](00_background.md#gm) of 25·25 µA = 625 µS, so `compute_requirements` clamps it and warns. The re-plan cannot raise gm2 either (same ceiling). Forcing `_TWO_STAGE_PASSES` to 0, 1 or 2 gives the same sizing: the loop exits on its first check.
- **Why slew is short.** Cc is sized so the *first* stage slews at 3.5 V/µs. The output node is charged by the second stage's 25 µA into CL + Cc = 22.9 pF, which gives 1.09 V/µs. The metric takes the slower of the two ([slew](00_background.md#slew)).
- **The SPICE column.** `generic` has no foundry model, but it can still be simulated: `verify/deck._emit_model` writes a Level-1 `.model` card from `mu_cox`/`vth`/`lam` ([deck.py:69-74](../../../circuitgenome/sizer/verify/deck.py#L69)), so `simulate_metrics` runs in 0.2 s. Both columns use the same square law, so they agree within ~1 dB of gain and ~15 % of GBW. The card sets no `tox`, so (judging by ngspice's Level-1 defaults; not confirmed by a run) the simulated devices have no intrinsic gate capacitance, while the analytical chain counts `Cgs = ⅔·W·L·Cox`. That is one plausible source of SPICE reading a higher GBW.

**Why the `generic` and gf180 numbers differ so much.** Three separate causes:

1. **Different specs.** The two spec files are not the same design problem. `spec_generic` has ibias 10 µA, Vdd 5 V, CL 20 pF and an 80 dB gain target. `spec_gf180` has 20 µA, 3.3 V, 5 pF and 60 dB. So every current is halved on `generic`, and the load is 4× heavier.
2. **Different device model.** The square law says a device gets more gm by getting wider, with no limit. So the solver hits a 625 µS second stage with W/L = 29 at Vdsat = 80 mV. A real device at 80 mV overdrive would be in weak inversion, where the square law is wrong. gf180's LUT-based forward pass instead picks an *inversion level* per role (Stop 4) and reads W from measured data.
3. **Different selection rule.** CP-SAT minimises ΣW, so every unconstrained device sits at the 1 µm minimum. The tail's 0.47 V Vdsat is simply what a 1/1 device gives at 10 µA, because nothing asked for less. The gm/Id path does not minimise anything. It gives current sources a long L (1.12 µm) and a low gm/Id on purpose, for output resistance and matching. That is why its tail and `mp1` are 40–100 µm wide. Constant λ on `generic` means L does not change `gds`, so the solver has no reason to lengthen anything.

To isolate cause 2 from cause 1, size `generic` with the **gf180 spec** (analytical only):
`OPTIMAL`, no warnings, every margin positive. Gain 71.7 dB, GBW 4.89 MHz, PM 61.4°, Cc 1.25 pF.
The input pair comes out 1/1 µm (gf180: 3.07/0.56), the second stage 5/1 (gf180: 17.9/0.56).
The square law claims a 1/1 µm PMOS at 10 µA already gives 42 µS, more than GBW needs, so nothing grows.
*Not investigated:* ngspice on that design reads GBW 0.52 MHz and slew 0.97 V/µs, about 10× below the analytical values. The DC bias check (`check_bias_soundness`) passes.
One lead: the tail's 0.67 V Vdsat leaves only about 10 mV of headroom at the bench's Vcm = 1.65 V (hand arithmetic, not verified).
This spec was never meant for `generic`, so treat it as a curiosity, not a bug report.

**Test to read.**
- [`test_sizer.py::test_size_two_stage_all_specs`](../../../tests/test_sizer.py#L444): the running example's spec, but with **PM 50°**. The comment above the spec explains why 60° is out of reach (second stage at its gm ceiling, RHP zero, grid overshoot), which matches the 57.9° above. Asserts `OPTIMAL`/`FEASIBLE`, W/L inside the tech bounds, and gain, GBW and PM met.
- [`test_sizer.py::test_sizer_meets_slew_through_every_stage`](../../../tests/test_sizer.py#L494): a **strict xfail** that pins the 1.1 V/µs slew gap. Fixing it turns the test into an XPASS.
- [`test_sizer.py::test_impossible_gain_flagged_not_infeasible`](../../../tests/test_sizer.py#L615): a 300 dB gain target still returns `OPTIMAL`/`FEASIBLE`, with a negative gain margin and the gm-ceiling warning, never `INFEASIBLE`.
- [`test_gmid_lut.py::test_level1_model_matches_equations`](../../../tests/test_gmid_lut.py#L61): `Level1Model` returns exactly `equations.*`.
- [`test_dc_op.py::test_level1_path_bias_feasible_default_true`](../../../tests/test_dc_op.py#L75): `bias_feasible` stays `True` on this path.
- [`test_spice_sim.py::test_generic_level1_tracks_analytical`](../../../tests/test_spice_sim.py#L118): SPICE gain within 20 dB and GBW within 3× of the analytical values (needs ngspice).

Run the fast ones (about 2.5 s together):

```
PYTHONPATH=$PWD pytest -q tests/test_sizer.py::test_size_two_stage_all_specs \
  tests/test_sizer.py::test_current_mirror_ratios_enforced \
  tests/test_sizer.py::test_sizer_meets_slew_through_every_stage \
  tests/test_gmid_lut.py::test_level1_model_matches_equations
```

All of `tests/test_sizer.py` sizes on `generic` through the `_tech()` helper ([test_sizer.py:37](../../../tests/test_sizer.py#L37)) unless a test loads another tech. Tests whose status is `"GMID"` are gm/Id tests.

**How to get `INFEASIBLE`.** The spec side clamps instead of failing (the gm ceiling,
Cc bounds), so a demanding spec does not make CP-SAT infeasible. The tech grid
can. Setting `width.max = 5 µm` on `generic` makes the 29 µm second stage
impossible, and `size_circuit` returns `INFEASIBLE` with no transistors.

**Check yourself.**
1. Which line makes `level1.py` re-run CP-SAT, and what has to happen for the loop to stop early?
2. The running example reports `OPTIMAL` but misses PM. Name the two places in the result that tell you.
3. Why does `compute_requirements` need `realized_gm` on this path but not on the gm/Id path?

<details><summary>Answers</summary>

1. `_solve(req)` at [level1.py:176](../../../circuitgenome/sizer/analytical/level1.py#L176), inside the `for _ in range(_TWO_STAGE_PASSES)` loop. It stops early if the chain is not a two-stage one, if `two_stage_replan` returns `(None, None)` (nothing to raise, or no value under the cap meets the spec), or if the re-solve finds no assignment.
2. `result.margins["phase_margin_deg"]` is negative (−2.07), and `result.warnings` holds the gm-ceiling message. `solver_status` says nothing about the spec.
3. CP-SAT rounds W up to a whole step, so the input pair's real gm1 is above the request, and the PM rule must size gm2 against that larger value. The gm/Id forward pass delivers the requested gm, so `GmIdModel.realized_gm` just returns it.

</details>

---

## 5.3 `docs/theory/sizing_flow.rst` (companion, 1206 lines)

**Job in one sentence.** The math behind this path: the five-step requirement
derivation, the CP-SAT linearisation, the re-plan, and the metrics, worked
through with the `spec_generic.yaml` numbers.

**What it adds that the code does not.**
- *Why the steps run in that order* ([sizing_flow.rst:196](../../theory/sizing_flow.rst#L196) onward): CMRR → SR → GBW → gain → PM, each with an equation, intuition, "why this position" and a worked number. The numbers match the running example: Cc = 2.857 pF, gm1 = 44.88 µS, gm2 (gain) = 226 µS, gm2 (PM) = 630 µS, and the input pair rounded up to W = 3 steps (51.96 µS).
- The "worst-case gm1" argument behind `realized_gm` (Step 5, [sizing_flow.rst:393](../../theory/sizing_flow.rst#L393)).
- The assumptions list ([sizing_flow.rst:817](../../theory/sizing_flow.rst#L817)): constant λ, no mismatch, a dominant-pole PM that ignores the mirror pole.
- Lines 904 onward cover three-stage NMC/RNMC. They are not needed for `circuit_0010`.

**Read:** lines 1–620 and 817–900. Skim the rest.

**Stale parts** (checked against `8571bf9`):

| Where | What it says | What the code does |
|---|---|---|
| [L470](../../theory/sizing_flow.rst#L470) | final gm2 = max(226, 630) = **630 µA/V** | clamped to the gm ceiling, 25·25 µA = **625 µS**, with a warning (`gm_req` printed for `mn1_second_stage` = 625.00 µS) |
| [L512](../../theory/sizing_flow.rst#L512) | `rhs = round(gm_req ** 2 * _SCALE)` | `_coeff(gm_req ** 2 * l_step)`; both sides carry their grid step ([constraints.py:95-96](../../../circuitgenome/sizer/analytical/constraints.py#L95)). Identical on `generic` only because step = 1 |
| [L541-551](../../theory/sizing_flow.rst#L541) | symmetry = matched pairs in three slots | also grouping by `(type, Id)`, FD p↔n symmetry, and mirror-ratio equalities (#67) |
| [L544](../../theory/sizing_flow.rst#L544) | ΣW is a proxy for "area and quiescent power" | currents are fixed before the solve; ΣW does not change power |
| [L277](../../theory/sizing_flow.rst#L277) | setting the SR ceiling on Cc "guarantees SR is never violated" | the slew metric also includes the output node `I2/(CL+Cc)`; the running example misses slew 3× (the metrics table at L668 has the right formula) |
| [L870-892](../../theory/sizing_flow.rst#L870) and the Step 1 note | CMRR 50 dB + GBW + SR are "mutually exclusive"; CP-SAT "returns `INFEASIBLE`" | on a mirror-load SE chain CMRR is now lifted on the sized circuit (#296). `spec_generic` + `cmrr_min_db: 50` on `circuit_0010` gives `OPTIMAL`, CMRR 87.7 dB, margin +37.7. The requirement side clamps rather than going infeasible |
| [L123](../../theory/sizing_flow.rst#L123) | heading "gm/Id model (PTM nodes)" | gf180mcu and sky130 use it too (cf. README candidate #6) |

## 5.4 `docs/_extra/walkthrough/analytical/*.html`

Three illustrated pages: [`index.html`](../../_extra/walkthrough/analytical/index.html) (a map),
[`constraints_explained.html`](../../_extra/walkthrough/analytical/constraints_explained.html) and
[`level1_explained.html`](../../_extra/walkthrough/analytical/level1_explained.html). They were last touched in
`b222cff` (#296, 2026-10-31), and they are good for intuition: the lattice
picture of `W ≥ 4·L` as a ray through the origin is the clearest explanation of
the linearisation in the repo.

Staleness:
- **`constraints_explained.html`** matches the code: every snippet checked against `constraints.py` agrees. Its worked numbers use a 0.1 µm grid and W from 5 to 100. Those are illustrative, not `generic`'s 1 µm grid with W from 1 to 600.
- **`level1_explained.html`** is behind the code:
  - It has no section on the two-stage re-solve loop (#274, [level1.py:139-180](../../../circuitgenome/sizer/analytical/level1.py#L139)) or the nested `_solve()` helper. Parts 4–5 show the solve and the scoring as one straight line.
  - Its `size_load_resistors(...)` call lacks the `single_stage=` argument.
  - Its worked example mixes grid steps: 0.1 µm for W, and L "3 steps × 0.05 µm = 0.15 µm".
  - The "cast" figure still labels `physics.preprocess` as "extract · dedup". Extraction moved to `circuit_view` (the Part 3 text has it right).

---

## Exit check

**Contrast the search (here) with the forward pass (Stop 4), and list which
`physics/` functions both paths share.**

<details><summary>Answer</summary>

**The difference.** Both start from the same per-device targets (`gm_req_map`, `vod_max_map`, `Cc`) and end with the same scoring. They differ in the middle.

| | Level-1 search (`analytical/`) | gm/Id forward pass (`gmid/`) |
|---|---|---|
| Question asked | "Which grid point obeys every rule, with the least ΣW?" | "For this target gm and role, which inversion level, and so which W?" |
| Device data | closed-form square law (`Level1Model`) | measured LUT (`GmIdModel` + `GmIdLut`) |
| Coupled devices (pairs, mirrors) | equality constraints solved *together* ([constraints.py:114-179](../../../circuitgenome/sizer/analytical/constraints.py#L114)) | computed in order by construction (Stop 4 geometry/plan) |
| Selection rule | minimise ΣW; unconstrained devices go to the grid minimum | per-role intent (signal / current source / cascode): L multiple and gm/Id chosen on purpose |
| gm delivered | rounded *up* to the W grid (`realized_gm` anticipates it) | the requested gm |
| Can fail? | yes: `INFEASIBLE` / `UNKNOWN`, empty `transistors` | no: always `"GMID"`; problems become warnings and `bias_feasible = False` |
| After geometry | ≤ 2 re-solves for the two-stage GBW/PM re-plan | a refinement phase (RNMC, two-stage re-plan, stage interface, bias levels) |
| DC check | none (`bias_feasible` stays `True`) | `gmid/bias.py`, `stage_interface.py`, then SPICE in the CLI |
| Speed on `circuit_0010` | 0.4 s | 0.06 s |

**Shared `physics/` functions.** From the imports in [level1.py:19-32](../../../circuitgenome/sizer/analytical/level1.py#L19), each one confirmed in a `gmid/` import:

| `physics/` function | Level-1 use | gm/Id import |
|---|---|---|
| [`circuit_view.analyze_circuit`](../../../circuitgenome/sizer/physics/circuit_view.py#L198) | [level1.py:56](../../../circuitgenome/sizer/analytical/level1.py#L56) | [gmid/analyze.py:24](../../../circuitgenome/sizer/gmid/analyze.py#L24) (wrapped into `GmIdCircuitView`) |
| [`circuit_view.adoption_warnings`](../../../circuitgenome/sizer/physics/circuit_view.py#L174) | [level1.py:96](../../../circuitgenome/sizer/analytical/level1.py#L96) | [gmid/gmid_sizer.py:46](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L46) |
| [`preprocess.assign_ids`](../../../circuitgenome/sizer/physics/preprocess.py#L220), [`compute_requirements`](../../../circuitgenome/sizer/physics/preprocess.py#L305), [`size_load_resistors`](../../../circuitgenome/sizer/physics/preprocess.py#L51) | [level1.py:59-63, 91](../../../circuitgenome/sizer/analytical/level1.py#L59) | [gmid/plan.py:21](../../../circuitgenome/sizer/gmid/plan.py#L21) |
| [`preprocess.size_compensation_caps`](../../../circuitgenome/sizer/physics/preprocess.py#L105), [`lift_nodal_cmrr`](../../../circuitgenome/sizer/physics/preprocess.py#L649) | [level1.py:71, 97](../../../circuitgenome/sizer/analytical/level1.py#L71) | [gmid/gmid_sizer.py:47](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L47) |
| [`stage_chain.build_stage_chain`](../../../circuitgenome/sizer/physics/stage_chain.py#L1076) | [level1.py:136](../../../circuitgenome/sizer/analytical/level1.py#L136) | [gmid/evaluate.py:16](../../../circuitgenome/sizer/gmid/evaluate.py#L16), `rnmc_refine.py:27`, `two_stage_refine.py:21` |
| [`stage_chain.reports_nodal_rejection`](../../../circuitgenome/sizer/physics/stage_chain.py#L724) | [level1.py:69](../../../circuitgenome/sizer/analytical/level1.py#L69) | [gmid/gmid_sizer.py:51](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L51) |
| [`metrics.evaluate_metrics`](../../../circuitgenome/sizer/physics/metrics.py#L189) | [level1.py:182](../../../circuitgenome/sizer/analytical/level1.py#L182) | [gmid/evaluate.py:14](../../../circuitgenome/sizer/gmid/evaluate.py#L14) |
| [`metrics.two_stage_replan`](../../../circuitgenome/sizer/physics/metrics.py#L97) | [level1.py:163](../../../circuitgenome/sizer/analytical/level1.py#L163) | [gmid/two_stage_refine.py:20](../../../circuitgenome/sizer/gmid/two_stage_refine.py#L20) |
| [`equations.open_loop_gain_measurable`](../../../circuitgenome/sizer/physics/equations.py#L212) | [level1.py:190](../../../circuitgenome/sizer/analytical/level1.py#L190) | [gmid/gmid_sizer.py:185](../../../circuitgenome/sizer/gmid/gmid_sizer.py#L185) |
| `taxonomy.SECOND_STAGE_SLOTS`, `is_signal_device` | [level1.py:32](../../../circuitgenome/sizer/analytical/level1.py#L32) | `plan.py:23`, `two_stage_refine.py:22`, `analyze.py:25`, ... |

**Not shared.** The `DeviceModel` *implementation* differs: [`Level1Model`](../../../circuitgenome/sizer/physics/device_model.py#L142) here, [`GmIdModel`](../../../circuitgenome/sizer/physics/device_model.py#L248) there. The shared functions above take whichever is passed in. Only the gm/Id path imports `gmid_lut.GmIdLut`, `rnmc.RNMC`, `device_model.GmIdPolicy` and the role constants (`SIGNAL`, `CASCODE`, `CURRENT_SOURCE`), plus `stage_chain.check_cmfb_sense_swing`, `check_follower_swing`, `_tail_current_net` and `StageChain`. Level-1 imports no `physics/` function that gm/Id does not also use, apart from `Level1Model`.

In short: **the front (structure, KCL currents, spec → gm targets) and the back
(stage chain, metrics, the two-stage re-plan rule, the CMRR lift) are shared.
Only the step "targets → W/L" differs**, plus the gm/Id path's extra repair
phases.

</details>

---

## Simplification notes

| Location | Observation |
|---|---|
| [`constraints.py:60-61`](../../../circuitgenome/sizer/analytical/constraints.py#L60) | Docstring: `vod_max_map` "entries with value ≥ a large number are skipped". The code skips only `inf` or `≤ 0` ([L103](../../../circuitgenome/sizer/analytical/constraints.py#L103)) |
| [`constraints.py:62`](../../../circuitgenome/sizer/analytical/constraints.py#L62) | Docstring: `tech` supplies "µCox, Vth, λ and grids". `build_model` reads only `mu_cox` and the W/L grids |
| [`constraints.py:181`](../../../circuitgenome/sizer/analytical/constraints.py#L181), [`sizing_flow.rst:544`](../../theory/sizing_flow.rst#L544) | ΣW called a "proxy for power". Currents are fixed before the solve, so W does not move power. L is not in the objective: for devices with no gm/Vdsat rule, L = 1 comes from the `SELECT_MIN_VALUE` search hint, not from the objective |
| [`constraints.py:1-22`](../../../circuitgenome/sizer/analytical/constraints.py#L1) | `_SCALE = 10¹²` rounds coefficients to integers. Precision depends on the grid step and µA currents: fine on `generic`, but small `gm_req`s round to 0 or 1. Not documented |
| No test | `build_model` has no direct unit test. It is covered only through `size_circuit` |
| [`analytical/__init__.py:3`](../../../circuitgenome/sizer/analytical/__init__.py#L3) | "PTM nodes use the gm/Id pipeline". gf180mcu and sky130 do too (same family as README #6) |
| [`level1.py:146-180`](../../../circuitgenome/sizer/analytical/level1.py#L146) vs [`gmid/two_stage_refine.py`](../../../circuitgenome/sizer/gmid/two_stage_refine.py) | Two parallel copies of "find the signal devices → `two_stage_replan` → raise `gm_req` with `max`". They differ in small ways: gm/Id skips any `compensation_scheme`, Level-1 relies on `len(chain.stages) != 2`; Level-1 caps gm2 at `min(gm(W_max), ceiling)`, gm/Id at the ceiling only |
| [`level1.py:41-42`](../../../circuitgenome/sizer/analytical/level1.py#L41) | `parsed` and `sr_result` are unused (already README #3) |
| [`spec_generic.yaml:1, 22`](../../../examples/two_stage_se_specs/spec_generic.yaml#L1) | Header says "Feasible ... spec"; the SR comment says "(ibias / Cc)". On `circuit_0010` it misses PM (57.9° vs 60°) and slew (1.09 vs 3.5 V/µs); the tests acknowledge both (PM 50° in `test_size_two_stage_all_specs`, strict xfail for slew) |
| [`sizing_flow.rst:470`](../../theory/sizing_flow.rst#L470) | gm2 = 630 µA/V; the code clamps to the 625 µS ceiling and warns |
| [`sizing_flow.rst:512`](../../theory/sizing_flow.rst#L512) | Code snippet omits `w_step`/`l_step` |
| [`sizing_flow.rst:541`](../../theory/sizing_flow.rst#L541) | Symmetry section omits mirror-ratio and FD p↔n constraints |
| [`sizing_flow.rst:277`](../../theory/sizing_flow.rst#L277) | "SR is never violated": ignores the output-node slew term |
| [`sizing_flow.rst:870-892`](../../theory/sizing_flow.rst#L870), [`test_sizer.py:451`](../../../tests/test_sizer.py#L451), [`test_sizer.py:597`](../../../tests/test_sizer.py#L597) | "CMRR + GBW + SR mutually exclusive / CP-SAT returns INFEASIBLE". Stale since #290/#296 for mirror-load SE: `circuit_0010` + 50 dB CMRR → `OPTIMAL`, 87.7 dB |
| [`level1_explained.html`](../../_extra/walkthrough/analytical/level1_explained.html) | Missing the two-stage re-solve loop and `_solve()`; `size_load_resistors` call lacks `single_stage=`; mixed 0.1 / 0.05 µm grid in the example; cast figure says preprocess does "extract · dedup" |
| [`verify/deck.py:69-74`](../../../circuitgenome/sizer/verify/deck.py#L69) | The synthesised Level-1 card has no `tox`, so SPICE devices likely carry no gate capacitance while the analytical chain counts `Cgs`: one source of the 15 % GBW gap on `generic` |
