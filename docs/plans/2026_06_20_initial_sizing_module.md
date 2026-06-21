# Initial Sizing Module Design — `circuitgenome/sizer`

**Date**: 2026-06-20  
**Reference**: Abel, Neuner, Graeb — "Constraint-Programmed Initial Sizing of Analog Operational Amplifiers", ICCD 2019 (COPRISI)  
**Solver**: OR-Tools CP-SAT  
**v1 topology scope**: `one_stage_opamp`, `two_stage_opamp_single_ended`

---

## 1. Motivation

Initial sizing is the step between topology synthesis (Layer 0/1 of CircuitGenome)
and SPICE-level optimization. Given a zero-value netlist and a performance
specification (gain, GBW, phase margin, slew rate, power, output swing), it computes
a first set of transistor W/L values that analytically satisfy the spec. This gives
SPICE optimizers a physically reasonable starting point and reduces convergence time.

The COPRISI paper formulates initial sizing as a Constraint Optimization Problem (COP)
solved by branch-and-bound over discrete transistor dimensions. CircuitGenome adapts
this approach using OR-Tools CP-SAT and reuses the existing SR+FBR pipeline for
structural analysis, eliminating the paper's separate functional block analysis step.

---

## 2. Position in the CircuitGenome Pipeline

```
synthesizer  →  netlist (zero W/L)
                     ↓
              [parse + SR + FBR]       ← existing recognizer pipeline
                     ↓
              FunctionalBlockRecognitionResult
                     ↓
              sizer.size_circuit(fbr_result, topology, tech, spec)
                     ↓
              SizingResult (W/L per transistor, Cc, computed metrics)
```

The sizer takes the **topology-mode FBR result** (`assign_slots` output) plus a
`TopologyTemplate`, `TechParams`, and `SizingSpec`. It returns a `SizingResult`
with per-transistor W/L, compensation cap value, computed performance metrics, and
safety margins.

---

## 3. Key Design Insight: iDS is Topology-Determined

In a biased op-amp, KCL + the input bias current iBias fully determines every
transistor's drain-source current before any W/L choice is made:

- `tail_current` transistors carry iBias
- Each `input_pair` transistor carries iBias/2
- Current mirror output transistors carry N×iBias (N = W/L mirror ratio, a design var)
- `second_stage` transistor carries a designer-chosen multiple of iBias

This means **W and L are the only true free design variables**. All performance
constraints reduce to inequalities in W[k] and L[k].

### Linearization

The Shichman-Hodges square-law model gives:

```
iDS = (µCox/2) · (W/L) · (vGS - Vth)²        [saturation]
gm  = √(2·µCox·(W/L)·iDS)
gd  = λ·|iDS|
```

With iDS fixed by KCL, the gm lower bound becomes:

```
gm ≥ gm_req
→ 2·µCox·iDS·W ≥ gm_req²·L          [linear in W, L]
```

Current mirror ratio constraints are also linear:

```
(W_out / L_out) / (W_ref / L_ref) = N
→ W_out · L_ref = N · W_ref · L_out  [linear in W, L]
```

This allows CP-SAT (which operates on integer domains) to handle all core
constraints natively without nonlinear extensions.

---

## 4. Solver Strategy

**OR-Tools CP-SAT** with integer W/L variables:

| Quantity | Representation |
|---|---|
| W[k], L[k] | Integer µm variables with tech-config bounds |
| iDS[k] | Float, derived analytically from KCL + iBias |
| vGS[k], vDS_sat[k] | Float, computed after solving for W/L |
| Performance metrics | Float, evaluated analytically post-solve |
| Cc | Float, derived from GBW spec: Cc = gm1 / (2π·GBW_spec) |

### Branching Heuristic (from paper, §IV)

Follows the bias current path through the circuit:
1. Branch bias transistor (whose drain is fed by iBias) first
2. Propagate to transistors connected to the bias gate (current mirror gates)
3. Branch remaining transistors in connectivity order
4. Branch cascode pairs last

Implemented via `model.add_decision_strategy([vars_in_order], CHOOSE_FIRST, ASSIGN_MIN_VALUE)`.

### Objective

Maximize the minimum performance safety margin across all constrained specs:

```
maximize: min(gain_actual/gain_req, GBW_actual/GBW_req, PM_actual/PM_req, ...)
```

Encoded as a scaled integer objective in CP-SAT.

---

## 5. Performance Equations

Based on textbook small-signal analysis (Laker & Sansen; Allen & Holberg):

### All topologies

| Metric | Equation |
|---|---|
| gm | √(2·µCox·(W/L)·iDS) |
| gd | λ·\|iDS\| |
| Rout (stage j) | 1/(gd_top + gd_bottom) |
| Stage gain | gm_j · Rout_j |
| VDS_sat | vGS - Vth = √(2·iDS·L/(µCox·W)) |
| Power | (VDD−VSS) · Σ iDS_supply |
| Output swing max | VDD − VDS_sat_load |
| Output swing min | VSS + VDS_sat_tail + VDS_sat_input |

### Two-stage only

| Metric | Equation |
|---|---|
| Open-loop gain | gm1·Rout1 · gm2·Rout2 |
| GBW | gm1 / (2π·Cc) |
| Phase margin | 90° − arctan(gm1·CL / (gm2·Cc)) |
| Slew rate | iBias / Cc |
| CMRR | ≈ gm1 / (2·gd_tail) |
| PSRR+ | ≈ gm2 / gd_biasgen  (approx.) |

---

## 6. Module Structure

```
circuitgenome/sizer/
  __init__.py             # exports: size_circuit, load_tech, SizingSpec, SizingResult, TechParams
  models.py               # TechParams, MosfetParams, GridSpec, SizingSpec, SizingResult,
                          #   TransistorSizing dataclasses
  loader.py               # load_tech(path: Path) -> TechParams
  equations.py            # Level-1 MOSFET formulas + performance metric functions
  constraints.py          # build_model() — CP-SAT model builder
  sizer.py                # size_circuit() — main entry point
  config/
    tech_generic.yaml     # Generic parameterized tech config (template/example)
```

### `models.py` — Key Dataclasses

```python
@dataclass
class TechParams:
    name: str
    nmos: MosfetParams   # mu_cox (A/V²), vth (V), lam (1/V)
    pmos: MosfetParams
    width: GridSpec      # min, max, step (µm)
    length: GridSpec     # min, max, step (µm)
    cap: GridSpec        # min_pf, max_pf, step_pf

@dataclass
class SizingSpec:
    vdd: float; vss: float; ibias: float; cl: float
    gain_min_db: float | None = None
    gbw_min_hz: float | None = None
    phase_margin_min_deg: float | None = None
    slew_rate_min_vps: float | None = None
    power_max_w: float | None = None
    output_swing_max_v: float | None = None
    output_swing_min_v: float | None = None
    cmrr_min_db: float | None = None
    psrr_min_db: float | None = None

@dataclass
class SizingResult:
    transistors: dict[str, TransistorSizing]  # ref → W/L/iDS/vGS/vDS_sat
    cc_pf: float | None                       # compensation cap (two-stage only)
    metrics: dict[str, float]                 # {'gain_db': ..., 'gbw_hz': ..., ...}
    margins: dict[str, float]                 # safety margin per spec
    solver_status: str                        # 'OPTIMAL'|'FEASIBLE'|'INFEASIBLE'|'UNKNOWN'
```

### `config/tech_generic.yaml` — Template

```yaml
name: generic_parameterized
description: "Generic CMOS — replace with actual PDK values"
nmos:
  mu_cox: 270.0e-6   # A/V²  (typical 0.25µm)
  vth: 0.5           # V
  lam: 0.04          # 1/V
pmos:
  mu_cox: 90.0e-6
  vth: -0.5
  lam: 0.05
width:
  min: 1.0; max: 600.0; step: 1.0   # µm
length:
  min: 1.0; max: 10.0;  step: 1.0   # µm
cap:
  min_pf: 0.1; max_pf: 50.0; step_pf: 0.1
```

---

## 7. CLI Interface

New `circuitgenome size` subcommand:

```bash
circuitgenome size NETLIST \
  --topology TOPOLOGY_NAME \
  --tech tech.yaml \
  --spec spec.yaml \
  [--time-limit 30]
```

### Example `spec.yaml`

```yaml
vdd: 5.0
vss: 0.0
ibias: 10.0e-6    # A
cl: 20.0e-12      # F (output load cap)

gain_min_db: 80
gbw_min_hz: 2.5e6
phase_margin_min_deg: 60
slew_rate_min_vps: 3.5e6
power_max_w: 1.0e-3
output_swing_max_v: 4.6
output_swing_min_v: 0.4
```

### Sample Output

```
Netlist: circuit_0001_flat.ckt  |  Topology: two_stage_opamp_single_ended

Transistor sizing:
  m1_input_pair    W=21µm  L=2µm  IDS=5.00µA  VGS=-0.87V  VDS_sat=0.37V
  m2_input_pair    W=21µm  L=2µm  (matched)
  m1_load          W=10µm  L=3µm  IDS=5.00µA  VGS=0.71V   VDS_sat=0.21V
  m2_load          W=10µm  L=3µm  (matched)
  m1_tail          W=21µm  L=3µm  IDS=10.0µA  VGS=0.71V   VDS_sat=0.21V
  mn_second_stage  W=21µm  L=1µm  IDS=25.0µA  VGS=0.79V   VDS_sat=0.29V
  mp_second_stage  W=112µm L=3µm  IDS=25.0µA  VGS=-0.76V  VDS_sat=0.26V
  mn1_bias_gen     W=10µm  L=3µm  IDS=10.0µA  VGS=0.71V   VDS_sat=0.21V
  Cc = 4.5pF

Performance metrics:
  Open-loop gain    90.1 dB   [spec ≥ 80 dB]     margin +10.1 dB
  GBW               3.0 MHz   [spec ≥ 2.5 MHz]   margin +0.5 MHz
  Phase margin      62.4°     [spec ≥ 60°]        margin +2.4°
  Slew rate         4.4 V/µs  [spec ≥ 3.5 V/µs]  margin +0.9 V/µs
  Power             0.66 mW   [spec ≤ 1.0 mW]    margin 0.34 mW
  Output swing max  4.79 V    [spec ≥ 4.6 V]     margin +0.19 V
  Output swing min  0.21 V    [spec ≤ 0.4 V]     margin 0.19 V

Solver: OPTIMAL  (0.8s)
```

---

## 8. Dependencies

Add to `pyproject.toml`:
```toml
[project]
dependencies = ["pyyaml>=6.0", "ortools>=9.8"]

[tool.setuptools.package-data]
"circuitgenome.sizer" = ["config/*.yaml"]
```

---

## 9. v1 Topology Scope

| Topology | Slots used | Notes |
|---|---|---|
| `one_stage_opamp` | input_pair, load, tail_current, bias_generation | No Cc; GBW/PM/SR not applicable |
| `two_stage_opamp_single_ended` | + second_stage, compensation | Full perf spec coverage |

Three-stage and fully-differential topologies deferred to v2 (same equations, more slots).

---

## 10. Known Limitations (v1)

- **Model accuracy**: Shichman-Hodges (Level-1) deviates from BSIM accuracy at short L.
  Results are "reasonable starting point" quality, intended for SPICE optimization input.
- **Body effect**: Not modeled; vth assumed constant.
- **Feedback loops**: Not handled (paper limitation — future work).
- **Cascode loads**: Mirror-ratio constraints for folded/telescopic cascode loads are
  more complex; handled in v2.
- **CMRR/PSRR**: Approximation formulas only; accurate values require simulation.

---

## 11. Test Plan

`tests/test_sizer.py`:

| Test | Assertion |
|---|---|
| `test_load_tech` | Load generic YAML → valid TechParams |
| `test_equations_gm` | gm formula matches known numerical value |
| `test_equations_gain_two_stage` | gain = gm1·Rout1·gm2·Rout2 dB |
| `test_size_one_stage` | Solve one-stage, gain ≥ spec, status OPTIMAL |
| `test_size_two_stage_all_specs` | All specs satisfied, all margins > 0 |
| `test_symmetry_constraints` | Matched pair transistors have equal W, L |
| `test_infeasible_spec` | Impossibly tight spec → status INFEASIBLE |
