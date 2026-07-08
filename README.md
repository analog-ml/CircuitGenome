<p align="center">
  <img src="docs/images/logo_transparent.png" alt="CircuitGenome logo" width="300"/>
</p>

<p align="center">
  <em>Modular synthesis, recognition, and sizing of analog op-amp circuits.</em>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://circuitgenome.readthedocs.io/en/latest/index.html"><img src="https://img.shields.io/readthedocs/circuitgenome" alt="Documentation"/></a>
  <a href="https://pypi.org/project/circuitgenome/"><img src="https://img.shields.io/pypi/v/circuitgenome" alt="PyPI"/></a>
  <a href="https://pypi.org/project/circuitgenome/"><img src="https://img.shields.io/pypi/pyversions/circuitgenome" alt="Python versions"/></a>
</p>

CircuitGenome is a Python toolkit for **analog circuit topology synthesis and
recognition**, focused on op-amp design. It takes a modular approach: a complete
circuit is assembled from independent *functional building blocks* — differential
pair, load, tail current, bias, compensation, and output stage. By enumerating
every valid combination of block implementations, the tool can quickly generate
thousands of structurally distinct op-amp netlists for dataset generation,
automated design exploration, or topology studies.

The toolkit works in both directions of the design problem. Going **forward**, it
constructs and sizes op-amps from building blocks and a performance
specification. Going **backward**, it reads a flat SPICE netlist and recovers its
structure — identifying subcircuits (differential pairs, current mirrors, cascode
loads, bias generators, CMFB, compensation) and assigning each to its functional
role. An end-to-end designer chains these layers together, keeping only the
circuits whose **ngspice-measured** metrics meet the spec.

## Key Features

- **Topology synthesis** — enumerate every valid op-amp from modular building
  blocks and emit flat or hierarchical SPICE netlists.
- **Subcircuit & functional-block recognition** — recover structure and slot
  assignments from a flat SPICE netlist.
- **Initial sizing** — compute minimum transistor W/L values that satisfy DC
  specs (gain, GBW, phase margin, slew rate, CMRR) via an OR-Tools CP-SAT solver
  and a gm/Id flow.
- **End-to-end designer** — enumerate, size, simulate, and keep only the designs
  that pass **ngspice-measured** metrics.
- **One-, two-, and three-stage op-amps**, single-ended and fully differential,
  including nested-Miller (NMC) and reversed-nested-Miller (RNMC) compensation.
- **Extensible with no code** — add new module variants by editing a YAML file.

> Have a question? [Start a discussion or open an issue on GitHub](https://github.com/analog-ml/CircuitGenome/issues).

## 📖 Documentation

Full user guide, API reference, and design theory live in the Sphinx docs:

<p>
  <a href="https://circuitgenome.readthedocs.io/en/latest/index.html">
    <img src="https://img.shields.io/badge/%F0%9F%93%96_Read_the_Docs-circuitgenome.readthedocs.io-blue?style=for-the-badge" alt="Read the Docs"/>
  </a>
</p>

## Installation

```bash
pip install circuitgenome
```

Or install from source:

```bash
git clone https://github.com/analog-ml/CircuitGenome.git
cd CircuitGenome
pip install -e .
```

Requires Python 3.9+. PyYAML and OR-Tools (for the sizer) are installed
automatically.

## Getting Started

Run the end-to-end designer: enumerate a topology, size each candidate, simulate
with ngspice, and export the designs that meet your spec.

```bash
circuitgenome design --spec spec_gf180.yaml --topology two_stage_opamp_single_ended \
    --output-dir designs/ --limit 200 --workers 4
```

For the full CLI reference, Python API, and worked examples, see the
[documentation](https://circuitgenome.readthedocs.io/en/latest/index.html).

## Contributing

CircuitGenome is an open research project and contributions are very welcome —
new module variants, topology templates, recognizer patterns, sizing
heuristics, bug fixes, and documentation improvements.

- Browse or open issues: <https://github.com/analog-ml/CircuitGenome/issues>
- Fork the repo, create a feature branch, and open a pull request against `main`.
- Run the test suite before submitting: `python3 -m pytest tests/ -v`.
- Adding a new module variant usually needs **no code changes** — just edit
  `opamp_modules.yaml` (see the docs for details).

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
