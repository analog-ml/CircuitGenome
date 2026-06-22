# CircuitGenome

A Python toolkit for analog circuit topology synthesis and recognition, focused on op-amp design.

## Modules

### 1. Topology Synthesizer
Constructs complete op-amp circuits from modular building blocks. Given a topology configuration (number of stages, output type), it enumerates every valid combination of module variants and emits SPICE netlists.

### 2. Subcircuit Recognizer
Takes a flat SPICE netlist and identifies structural subcircuits — differential pairs, current mirrors, cascode loads, bias generators, CMFB circuits, compensation networks, and gain stages — using a YAML pattern library that spans all seven supported topologies.

### 3. Functional Block Recognizer
Takes the Subcircuit Recognizer's output plus a topology template and assigns each recognized structure to its functional slot (input pair, load, tail current, bias generation, etc.), recovering the circuit's `variant_map`.

### 4. Initial Sizer
Takes the slot assignments plus a performance specification (gain, GBW, phase margin, slew rate, CMRR) and computes minimum transistor W/L values with an OR-Tools CP-SAT integer-programming solver. Supports one-, two-, and three-stage op-amps, both single-ended and fully differential.

---

## Installation

```bash
pip install circuitgenome
```

Or install from source:

```bash
pip install -e .
```

Requires Python 3.9+, PyYAML, and OR-Tools (for the sizer module).

---

## Topology Synthesizer

The synthesizer works by combining **module variants** according to a **topology template**. Each module category has a fixed port interface; variants differ only in their internal implementation.

### Module categories

| Category | Variants |
|---|---|
| Input pair | PMOS/NMOS differential pair, with/without source degeneration, inverter-based |
| Load | Resistor (VDD-side / GND-side), PMOS/NMOS active (current mirror), PMOS/NMOS current source, folded cascode (PMOS/NMOS-input, single-output & differential-output), telescopic cascode (PMOS/NMOS) |
| Tail current | Current mirror (PMOS/NMOS), cascode current mirror (PMOS/NMOS), resistor (VDD-side / GND-side) |
| Bias generation | Diode-connected MOSFET ladder, magic battery (current mirror), resistor ladder |
| CMFB | Resistive-sense 5T OTA, differential-difference amplifier (DDA) — present only when `load` has a differential-output cascode (`output_cardinality: "differential"`) |
| Compensation | Miller cap, Miller cap + nulling resistor, indirect |
| Second stage | Common-source, common-drain (source follower), differential OTA |

**Input pair**

![Input pair variants](gallery/modules-implementations/input_pair+load+tail_current/input_pair.svg)

**Load**

![Load variants](gallery/modules-implementations/input_pair+load+tail_current/load.svg)

**Tail current**

![Tail current variants](gallery/modules-implementations/input_pair+load+tail_current/tail_current.svg)

**Bias generation**

![Bias generation variants](gallery/modules-implementations/bias_generation+cmfb/bias_generation.svg)

**CMFB**

![CMFB variants](gallery/modules-implementations/bias_generation+cmfb/cmfb.svg)

### Topology templates

| Name | Stages | Output | Compensation |
|---|---|---|---|
| `one_stage_opamp` | 1 | Single-ended | — |
| `two_stage_opamp_single_ended` | 2 | Single-ended | — |
| `two_stage_opamp_fully_differential` | 2 | Fully differential | — |
| `three_stage_opamp_nmc_single_ended` | 3 | Single-ended | Nested Miller (NMC) |
| `three_stage_opamp_rnmc_single_ended` | 3 | Single-ended | Reversed Nested Miller (RNMC) |
| `three_stage_opamp_nmc_fully_differential` | 3 | Fully differential | Nested Miller (NMC) |
| `three_stage_opamp_rnmc_fully_differential` | 3 | Fully differential | Reversed Nested Miller (RNMC) |

### Compatibility filters and compensation schemes

Not every module combination forms a valid circuit, so `enumerate_circuits`
applies a set of compatibility filters — **polarity**, **output-cardinality**,
**CMFB**, and **tail-current** — that prune invalid or redundant combinations,
and the two three-stage compensation schemes (**NMC** and **RNMC**) reuse the
existing second-stage and compensation modules. The exact per-topology circuit
counts, the rules behind each filter, and how to extend them are documented in
detail in the Sphinx documentation:

**https://circuitgenome.readthedocs.io/**

---

## CLI Usage

### List available topologies

```bash
circuitgenome synthesize --list-topologies
```

```
  one_stage_opamp  (stages=1, output=single_ended)
  two_stage_opamp_single_ended  (stages=2, output=single_ended)
  two_stage_opamp_fully_differential  (stages=2, output=fully_differential)
  three_stage_opamp_nmc_single_ended  (stages=3, output=single_ended, compensation=nested_miller)
  three_stage_opamp_rnmc_single_ended  (stages=3, output=single_ended, compensation=reversed_nested_miller)
  three_stage_opamp_nmc_fully_differential  (stages=3, output=fully_differential, compensation=nested_miller)
  three_stage_opamp_rnmc_fully_differential  (stages=3, output=fully_differential, compensation=reversed_nested_miller)
```

### List available module variants

```bash
circuitgenome synthesize --list-modules
```

### Generate circuits

```bash
# All 1-stage single-ended variants, flat SPICE
circuitgenome synthesize --stages 1 --output-dir ./circuits/

# All 2-stage single-ended variants, both flat and hierarchical SPICE
circuitgenome synthesize --stages 2 --output-type single_ended --format both --output-dir ./circuits/

# Dry run — count circuits without writing files
circuitgenome synthesize --stages 2 --dry-run

# Specific topology by name
circuitgenome synthesize --topology two_stage_opamp_fully_differential --output-dir ./circuits/

# 3-stage, nested Miller compensation, single-ended
circuitgenome synthesize --topology three_stage_opamp_nmc_single_ended --output-dir ./circuits/

# Dry run — count all 3-stage variants (NMC + RNMC, single-ended + fully differential)
circuitgenome synthesize --stages 3 --dry-run
```

### Size transistors

```bash
circuitgenome size \
  circuits/two_stage_opamp_single_ended/circuit_0001_flat.ckt \
  --topology two_stage_opamp_single_ended \
  --spec examples/spec_two_stage_opamp.yaml
```

The spec file is a YAML document with SI-unit values (see `examples/spec_two_stage_opamp.yaml`):

```yaml
vdd: 5.0
ibias: 10.0e-6       # A
cl: 20.0e-12         # F
gain_min_db: 80
gbw_min_hz: 2.5e+6   # Hz  — use e+6, not e6 (PyYAML parses bare e6 as a string)
phase_margin_min_deg: 60
slew_rate_min_vps: 3.5e+6
```

### Visualize topologies

```bash
circuitgenome visualize
```

Launches a Streamlit web UI for browsing topologies and module variants: pick
a topology, swap each slot's module variant, and see the resulting block
diagram (and SPICE netlist, for valid combinations) update live. Requires the
`viz` extra:

```bash
pip install circuitgenome[viz]
```

![Topology Explorer screenshot](docs/images/topology_visualizer.png)

#### CLI options

| Flag | Description | Default |
|---|---|---|
| `--stages 1\|2\|3` | Filter by number of stages | all |
| `--output-type single_ended\|fully_differential` | Filter by output type | all |
| `--topology NAME` | Use one specific topology | all |
| `--format flat\|hierarchical\|both` | SPICE output format | `flat` |
| `--output-dir PATH` | Directory for output files | `.` |
| `--dry-run` | Count circuits without writing | off |
| `--list-topologies` | Print topology names and exit | — |
| `--list-modules` | Print module variants and exit | — |

### Output format

Each generated circuit gets its own `.ckt` file. For `--format both`, two files are written per circuit:

**Flat SPICE** (`circuit_0001_flat.ckt`) — all devices in a single `.subckt` block:

```spice
.subckt circuit_0001 ibias in1 in2 out vdd! gnd!
m1_input_pair net_diff1 in1 net_tail net_tail pmos
m2_input_pair net_mid in2 net_tail net_tail pmos
r1_load net_diff1 gnd! 1k
r2_load net_mid gnd! 1k
m1_tail_current net_bias7 net_bias7 vdd! vdd! pmos
m2_tail_current net_tail net_bias7 vdd! vdd! pmos
mn1_bias_gen ibias ibias gnd! gnd! nmos
mn6_bias_gen net_bias5 ibias gnd! gnd! nmos
mp5_bias_gen net_bias5 net_bias5 vdd! vdd! pmos
mn8_bias_gen net_bias7 ibias gnd! gnd! nmos
mp7_bias_gen net_bias7 net_bias7 vdd! vdd! pmos
c1_compensation net_mid out 1p
mn1_second_stage out net_mid gnd! gnd! nmos
mp1_second_stage out net_bias5 vdd! vdd! pmos
.ends
```

**Hierarchical SPICE** (`circuit_0001_hier.ckt`) — one `.subckt` per module, top-level uses `X` instances:

```spice
.subckt differential_pair_pmos in1 in2 out1 out2 tail vdd gnd
m1 out1 in1 tail tail pmos
m2 out2 in2 tail tail pmos
.ends

.subckt resistor_load_gnd in1 in2 out1 out2 vdd gnd
r1 in1 gnd 1k
r2 in2 gnd 1k
.ends

.subckt current_mirror_tail_pmos out bias vdd gnd
m1 bias bias vdd vdd pmos
m2 out bias vdd vdd pmos
.ends

.subckt diode_connected_mosfet_bias ibias out5 out7 vdd gnd
mn1 ibias ibias gnd gnd nmos
mn6 out5 ibias gnd gnd nmos
mp5 out5 out5 vdd vdd pmos
mn8 out7 ibias gnd gnd nmos
mp7 out7 out7 vdd vdd pmos
.ends

.subckt miller_cap in out
c1 in out 1p
.ends

.subckt common_source in out bias vdd gnd
mn1 out in gnd gnd nmos
mp1 out bias vdd vdd pmos
.ends

.subckt circuit_0001 ibias in1 in2 out vdd! gnd!
Xinput_pair in1 in2 net_diff1 net_mid net_tail vdd! gnd! differential_pair_pmos
Xload net_diff1 net_mid net_diff1 net_mid vdd! gnd! resistor_load_gnd
Xtail_current net_tail net_bias7 vdd! gnd! current_mirror_tail_pmos
Xbias_gen ibias net_bias5 net_bias7 vdd! gnd! diode_connected_mosfet_bias
Xcompensation net_mid out miller_cap
Xsecond_stage net_mid out net_bias5 vdd! gnd! common_source
.ends
```

---

## Python API

```python
from circuitgenome import synthesize
from circuitgenome.synthesizer import to_flat_spice, to_hierarchical_spice

# Generate all 2-stage single-ended circuits
circuits = synthesize({"stages": 2, "output_type": "single_ended"})
print(f"{len(circuits)} circuits generated")

# Inspect the first circuit
c = circuits[0]
print(c.topology)          # "two_stage_opamp_single_ended"
print(c.variant_map)       # {"input_pair": <ModuleVariant>, "load": <ModuleVariant>, ...}

# Serialize to SPICE
flat = to_flat_spice(c, name="my_opamp")
hier = to_hierarchical_spice(c, name="my_opamp")

# Use a specific topology by name
circuits = synthesize({"topology": "one_stage_opamp"})

# All 3-stage single-ended circuits using Reversed Nested Miller Compensation
circuits = synthesize({
    "stages": 3,
    "output_type": "single_ended",
    "compensation_scheme": "reversed_nested_miller",
})

# Load custom module/topology definitions
from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer import enumerate_circuits

modules = load_modules("path/to/my_modules.yaml")
topologies = load_topologies("path/to/my_topologies.yaml")
for circuit in enumerate_circuits(topologies[0], modules):
    print(to_flat_spice(circuit))
```

### Initial Sizer

```python
from circuitgenome.recognizer import parse, recognize
from circuitgenome.recognizer.functional_block_recognizer import assign_slots
from circuitgenome.sizer import size_circuit
from circuitgenome.sizer.models import SizingSpec
from circuitgenome.sizer.loader import load_tech

# Run SR + FBR first (see Recognizer section above)
parsed = parse(netlist_text)
sr_result = recognize(parsed)
fbr_result = assign_slots(sr_result, topology)

# Define performance specification
spec = SizingSpec(
    vdd=5.0, vss=0.0,
    ibias=10e-6,               # 10 µA
    cl=20e-12,                 # 20 pF
    second_stage_current_ratio=2.5,
    gain_min_db=80,
    gbw_min_hz=2.5e6,
    phase_margin_min_deg=60,
    slew_rate_min_vps=3.5e6,
)

tech = load_tech("generic_parameterized")
result = size_circuit(parsed, sr_result, fbr_result, topology, spec, tech)

print(result.status)           # "OPTIMAL"
for ref, (w_um, l_um) in result.sizes_um.items():
    print(f"  {ref:30s}  W={w_um:.2f} µm  L={l_um:.2f} µm")
print(f"Cc = {result.cc_pf:.2f} pF")
```

---

## Extending with custom modules

Add new variants to `circuitgenome/synthesizer/config/opamp_modules.yaml`:

```yaml
- name: my_custom_input_pair
  category: input_pair
  display_name: "My Custom Input Pair"
  ports:
    - {name: in1,  role: input}
    - {name: in2,  role: input}
    - {name: out1, role: output}
    - {name: out2, role: output}
    - {name: tail, role: supply_in}
    - {name: vdd,  role: supply}
    - {name: gnd,  role: supply}
  devices:
    - {ref: m1, type: pmos, d: out1, g: in1, s: tail, b: tail}
    - {ref: m2, type: pmos, d: out2, g: in2, s: tail, b: tail}
    - {ref: m3, type: nmos, d: out1, g: in1, s: gnd,  b: gnd}
    - {ref: m4, type: nmos, d: out2, g: in2, s: gnd,  b: gnd}
```

The new variant is picked up automatically — no code changes needed.

---

## Running tests

```bash
python3 -m pytest tests/ -v
```

---

## Contributing

CircuitGenome is an open research project and contributions are very welcome —
new module variants, topology templates, recognizer patterns, sizing
heuristics, bug fixes, and documentation improvements.

- Browse or open issues: <https://github.com/analog-ml/CircuitGenome/issues>
- Fork the repo, create a feature branch, and open a pull request against `main`.
- Run the test suite before submitting: `python3 -m pytest tests/ -v`.
- Adding a new module variant usually needs **no code changes** — just edit
  `opamp_modules.yaml` (see "Extending with custom modules" above).

Full developer and API documentation lives in the Sphinx docs:
<https://circuitgenome.readthedocs.io/>

---

## References

1. **A Data-Driven Analog Circuit Synthesizer with Automatic Topology Selection
   and Sizing** — S. Poddar, A. F. Budak, L. Zhao, C.-H. Hsu, S. Maji, K. Zhu,
   Y. Jia, D. Z. Pan. *Design, Automation & Test in Europe (DATE)*, 2024.
2. **FUBOCO: Structure Synthesis of Basic Op-Amps by FUnctional BlOck
   COmposition** — I. Abel, H. Graeb. *ACM Transactions on Design Automation of
   Electronic Systems (TODAES)*, 2022.
3. **A Functional Block Decomposition Method for Automatic Op-Amp Design** —
   I. Abel, M. Neuner, H. Graeb. *Integration, the VLSI Journal* (Elsevier), 2022.
4. **Constraint-Programmed Initial Sizing of Analog Operational Amplifiers** —
   I. Abel, M. Neuner, H. Graeb. *IEEE International Conference on Computer
   Design (ICCD)*, 2019.

PDFs of all four papers are in [`docs/papers/`](docs/papers/).
