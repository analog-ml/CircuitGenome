# CircuitGenome

A Python toolkit for analog circuit topology synthesis and recognition, focused on op-amp design.

## Modules

### 1. Topology Synthesizer *(available)*
Constructs complete op-amp circuits from modular building blocks. Given a topology configuration (number of stages, output type), it enumerates every valid combination of module variants and emits SPICE netlists.

### 2. Subcircuit Recognizer *(coming soon)*
Takes a flat SPICE netlist and identifies structural subcircuits (differential pairs, cascode mirrors, etc.) at multiple hierarchy levels.

### 3. Functional Block Recognizer *(coming soon)*
Takes a flat SPICE netlist and identifies which functional roles (input stage, load, bias, compensation, etc.) each part of the circuit plays.

---

## Installation

```bash
pip install -e .
```

Requires Python 3.9+ and PyYAML.

---

## Topology Synthesizer

The synthesizer works by combining **module variants** according to a **topology template**. Each module category has a fixed port interface; variants differ only in their internal implementation.

### Module categories

| Category | Variants |
|---|---|
| Input pair | PMOS/NMOS differential pair, with/without source degeneration, inverter-based |
| Load | Resistor, PMOS/NMOS active (current mirror), current source, folded cascode, telescopic cascode |
| Tail current | Current mirror, cascode current mirror, resistor |
| Bias generation | Diode-connected MOSFET, magic battery, resistor |
| Compensation | Miller cap, Miller cap + nulling resistor, indirect |
| Second stage | Common-source, common-drain (source follower), differential OTA |

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

A 2-stage single-ended topology with no filters yields **2430 unique circuits**
(5 × 6 × 3 × 3 × 3 × 3 module combinations). Each 3-stage single-ended topology
adds two more `second_stage` slots (gm2, gm3) and two `compensation` slots
(Cm1, Cm2), yielding **21 870 circuits** (5 × 6 × 3 × 3 × 3 × 3 × 3 × 3). Each
3-stage fully-differential topology duplicates those four slots per output
path, yielding **1 771 470 circuits** (5 × 6 × 3 × 3 × 3⁸).

### Three-stage compensation schemes

Both 3-stage templates reuse the existing `second_stage` modules for the
second (gm2) and third (gm3) gain stages, and the existing `compensation`
modules for the two Miller capacitors Cm1/Cm2 — no new module variants are
required.

- **Nested Miller (NMC)** — both Cm1 and Cm2 return to the final output node:
  Cm1 spans gm2+gm3 (outer loop), Cm2 spans gm3 only (inner loop).
- **Reversed Nested Miller (RNMC)** — Cm1 spans gm3 only (gm2's output to the
  final output), while Cm2 spans gm2 only (gm1's output to gm2's output)
  instead of returning to the final output. This reduces output-node loading,
  which is useful when gm3 is a low-gain buffer.

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
input_pair_m1 net_diff1 in1 net_tail net_tail pmos
input_pair_m2 net_mid in2 net_tail net_tail pmos
load_r1 vdd! net_diff1 1k
load_r2 vdd! net_mid 1k
tail_current_m1 net_tail_bias net_tail_bias vdd! vdd! pmos
tail_current_m2 net_tail net_tail_bias vdd! vdd! pmos
bias_gen_mp1 ibias ibias vdd! vdd! pmos
bias_gen_mn1 net_bias net_bias gnd! gnd! nmos
compensation_c1 net_mid out 1p
second_stage_mn1 out net_mid gnd! gnd! nmos
second_stage_mp1 out net_bias vdd! vdd! pmos
.ends
```

**Hierarchical SPICE** (`circuit_0001_hier.ckt`) — one `.subckt` per module, top-level uses `X` instances:

```spice
.subckt differential_pair_pmos in1 in2 out1 out2 tail vdd gnd
m1 out1 in1 tail tail pmos
m2 out2 in2 tail tail pmos
.ends

.subckt resistor_load in1 out vdd gnd
r1 vdd in1 1k
r2 vdd out 1k
.ends

* ... (one block per module variant used)

.subckt circuit_0001 ibias in1 in2 out vdd! gnd!
Xinput_pair in1 in2 net_diff1 net_mid net_tail vdd! gnd! differential_pair_pmos
Xload net_diff1 net_mid vdd! gnd! resistor_load
Xtail_current net_tail net_tail_bias vdd! gnd! current_mirror_tail
Xbias_gen ibias net_bias vdd! gnd! diode_connected_mosfet_bias
Xcompensation net_mid out miller_cap
Xsecond_stage net_mid out net_bias vdd! gnd! common_source
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

## References

- *A Data-Driven Analog Circuit Synthesizer with Automatic Topology Selection and Sizing*
- *FUBOCO: Structure Synthesis of Basic Op-Amps by FUnctional BlOck COmposition*
- *A Functional Block Decomposition Method for Automatic Op-Amp Design*
