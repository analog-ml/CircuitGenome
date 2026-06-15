# Design Doc: Subcircuit Recognizer & Functional Block Recognizer

Status: **design only — no implementation yet.** This document captures the
architecture agreed for CircuitGenome's next phase, before any code is
written. It is intended to be read on its own by whoever picks up the
implementation, without needing the design conversation that produced it.

## 1. Motivation & Scope

CircuitGenome's Phase 1 (the **Topology Synthesizer**, `circuitgenome.synthesizer`)
enumerates op-amp circuits by combining module variants (from
`opamp_modules.yaml`) according to topology templates (from
`opamp_topologies.yaml`), and emits flat SPICE netlists.

Phase 2 is the inverse direction, described in `instructions/instruction-1.md`
and `docs/overview.rst` as two modules:

- **Subcircuit Recognizer (SR)** — takes a flat SPICE netlist and identifies
  structural subcircuits (differential pairs, current mirrors, cascode pairs,
  etc.), potentially at multiple levels of hierarchy.
- **Functional Block Recognizer (FBR)** — takes a flat SPICE netlist and
  identifies which functional role each part plays (input stage, load, bias
  generation, compensation, etc.).

Both are **rule-based** (not ML-based) by explicit decision.

### Scope staging

- **MVP**: round-trip CircuitGenome's *own* `synthesize()` output. Given a
  `SynthesizedCircuit` with a known `variant_map`, flatten it to SPICE,
  recognize it, and recover the same `variant_map`. This gives unambiguous
  ground truth for every test and guaranteed pattern coverage (patterns are
  derived from the same `opamp_modules.yaml` that generated the circuit).
- **Milestone 2**: generalize to `examples/netlist.ckt`, a hand-written,
  non-CircuitGenome netlist with generic net/ref naming, whose expected output
  is sketched (with stale ACST naming) in `examples/subcircuits.xml` and
  `examples/functional_blocks.xml`.
- **Future**: additional circuit families (LDO, comparator, ADC), richer
  functional taxonomies, CLI/visualizer integration, XML/JSON export.

This document designs the architecture so that the MVP is a clean subset of
the long-term shape — not a throwaway prototype.

## 2. Architecture Overview

A 3-layer pipeline, split along a **circuit-agnostic vs. circuit-family-specific**
boundary (not simply "first pass vs. second pass"):

```
flat SPICE text
      |
      v
+---------------------------+
| Layer 0: Netlist Parser    |   circuit-agnostic
| -> ParsedNetlist            |   (devices, nets, ports)
+---------------------------+
      |
      v
+---------------------------+
| Layer 1: Subcircuit        |   circuit-agnostic, additively
| Recognizer (SR)             |   extensible structural pattern
| -> SubcircuitRecognition    |   library (diff pairs, mirrors,
|    Result                   |   cascodes, arrays, ...)
+---------------------------+
      |
      v
+---------------------------+
| Layer 2: Functional Block   |   circuit-FAMILY-specific:
| Recognizer (FBR)            |   one engine + a taxonomy
| -> FunctionalBlock           |   config (which roles exist,
|    RecognitionResult         |   how to assign SR's structures
|    (MVP: variant_map-shaped) |   to them)
+---------------------------+
```

Why this split, and not "SR vs FBR = first pass vs second pass" generically:

- **Layer 1 (SR)** recognizes structural primitives that recur across *many*
  circuit families: differential pairs, current mirrors, cascode pairs,
  diode-connected devices, capacitor/resistor arrays, inverters. A
  cross-coupled latch (comparator regeneration stage) is just two inverter
  structures wired back-to-back — i.e. extending SR to comparators mostly
  means *adding* a few new pattern definitions, not changing existing ones.
  SR's pattern library must therefore be an **additive registry**, not a
  monolithic algorithm.
- **Layer 2 (FBR)** assigns circuit-family-specific *meaning* to SR's
  structures (op-amp gm-stage vs. load vs. bias; LDO error-amp vs. pass
  device vs. reference; etc.). This is naturally "one engine + swappable
  taxonomy config", mirroring how `enumerate_circuits` is one engine driven
  by `opamp_modules.yaml`/`opamp_topologies.yaml`.

For the MVP, FBR's "taxonomy config" is literally the existing
`opamp_modules.yaml` categories plus the matched `TopologyTemplate` — no new
YAML file is needed yet (see §6).

## 3. Data Model

New module: `circuitgenome/recognizer/models.py` (does not exist yet),
mirroring the style of `circuitgenome/synthesizer/models.py` (plain
dataclasses, no logic, none frozen).

```python
from dataclasses import dataclass, field
from circuitgenome.synthesizer.models import Device


@dataclass
class ParsedNetlist:
    """Output of Layer 0 (netlist parsing)."""
    name: str
    external_ports: list[str]
    devices: list[Device]
    internal_nets: set[str]


@dataclass
class RecognizedStructure:
    """A single recognized structure (one SR pattern match)."""
    name: str                          # pattern name, e.g. "differential_pair_nmos"
    category: str                      # e.g. "input_pair" (from opamp_modules.yaml)
    index: int                         # instance number, for repeated structures
    tech_type: str | None              # "n", "p", or None
    pins: dict[str, str]               # pin name -> net name
    devices: list[Device]              # leaf devices belonging to this structure
    children: list["RecognizedStructure"] = field(default_factory=list)
    # children is populated by milestone-2's multi-level composition; empty
    # for all MVP (composite-only) matches.


@dataclass
class SubcircuitRecognitionResult:
    """Output of Layer 1 (SR)."""
    structures: list[RecognizedStructure]
    # May contain MULTIPLE overlapping candidates for the same device-set
    # (see section 5.4) -- SR does not pick a winner.
    unrecognized_devices: list[Device] = field(default_factory=list)
    # MVP invariant (asserted by round-trip tests): empty for
    # CircuitGenome-generated netlists. Populated for real in milestone 2.


@dataclass
class SlotAssignment:
    """One FBR decision: a recognized structure assigned to a topology slot."""
    slot_name: str
    pattern_name: str
    structure: RecognizedStructure


@dataclass
class FunctionalBlockRecognitionResult:
    """Output of Layer 2 (FBR), MVP taxonomy (= opamp_modules.yaml categories)."""
    slot_assignments: dict[str, SlotAssignment]   # variant_map-shaped
    unassigned_structures: list[RecognizedStructure] = field(default_factory=list)
    unrecognized_devices: list[Device] = field(default_factory=list)
```

### SR pattern definitions (`PatternDef`)

Loaded from the pattern-library YAML (§5):

```python
@dataclass
class PatternDevice:
    ref: str            # template-local reference, e.g. "m1"
    type: str           # "nmos" | "pmos" | "resistor" | "capacitor"


@dataclass
class PatternDef:
    name: str                       # e.g. "differential_pair_nmos"
    category: str                   # e.g. "input_pair"
    devices: list[PatternDevice]    # template devices
    same_net: list[list[str]]       # equality constraints, e.g. [["m1.s", "m2.s"]]
    pins: dict[str, str]            # pin name -> "template_ref.terminal"
    tech_type_from: str | None      # which template device's type sets tech_type
    hook: str | None                # optional "module:function" extra-check path
```

A `FunctionalBlockRecognitionResult` for a future ACST-style taxonomy
(milestone 2+, **not implemented for the MVP**) would look like:

```python
@dataclass
class AcstFunctionalPartition:
    gm_parts: list[RecognizedStructure]
    load_parts: list[RecognizedStructure]
    bias_parts: list[RecognizedStructure]
    capacitances: list[RecognizedStructure]
    resistor_parts: list[RecognizedStructure]
    common_mode_signal_detector_parts: list[RecognizedStructure]
    positive_feedback_parts: list[RecognizedStructure]
    undefined_parts: list[RecognizedStructure]
```

This is a *different taxonomy config* over the *same* `SubcircuitRecognitionResult`
-- it does not replace `FunctionalBlockRecognitionResult`, it's an alternative
output shape FBR's engine could also produce.

## 4. Layer 0 — Netlist Parsing

New module: `circuitgenome/recognizer/netlist_parser.py` (does not exist yet).

**Responsibility**: parse flat SPICE subcircuit text into a `ParsedNetlist`
(`list[Device]` + `external_ports` + the set of internal net names). This is
the *structural inverse* of
`circuitgenome.synthesizer.netlist.to_flat_spice`.

**Scope for the MVP**: only `to_flat_spice()`'s own output format needs to be
handled --- a `.subckt <name> <port...>` header line, one device line per
`Device` (MOSFET/resistor/capacitor), `.ends`. Net and ref names are
arbitrary strings (no assumptions about `to_flat_spice`'s specific naming
conventions like `net_bias{N}` or `{ref}_{slot}` -- the parser must not rely
on those, since milestone 2's netlist uses completely different naming).

**Important**: the exact device-line grammar -- in particular, how a MOSFET
line's model name maps to `Device.type` (`"nmos"` vs `"pmos"`), and the
terminal ordering for each device type (`d g s b` for MOSFETs, `t1 t2` for
resistors, `p m` for capacitors) -- must be read directly from
`circuitgenome/synthesizer/netlist.py`'s `to_flat_spice` implementation at
implementation time, so the parser is a true inverse. This doc does not
restate that grammar to avoid it going stale.

**Milestone 2 additions** (not in MVP): tolerate `examples/netlist.ckt`'s
syntax quirks (e.g. the `.suckt` typo for `.subckt`), and any other minor
syntactic variation found there.

## 5. Layer 1 — Subcircuit Recognizer (SR)

New module: `circuitgenome/recognizer/subcircuit_recognizer.py` (does not
exist yet), plus a pattern-library config:
`circuitgenome/recognizer/config/subcircuit_patterns.yaml` (does not exist
yet).

### 5.1 Pattern YAML schema

Each pattern is a small template graph: a handful of typed template devices,
connectivity constraints between their terminals (and to named "pins"), and
the pin names to export. Worked example:

```yaml
patterns:
  - name: differential_pair_nmos
    category: input_pair          # matches opamp_modules.yaml category
    devices:
      - {ref: m1, type: nmos}
      - {ref: m2, type: nmos}
    same_net:
      - [m1.s, m2.s]               # shared tail node
      - [m1.b, m2.b]               # shared bulk node
    pins:
      in1: m1.g
      in2: m2.g
      out1: m1.d
      out2: m2.d
      tail: m1.s
      bulk: m1.b
    tech_type_from: m1             # tech_type = "n" (m1 is nmos)
```

`same_net` entries are the *only* required equalities; the matcher does not
require that terminals NOT listed are forced unequal (so a coincidentally
more-connected real circuit can still match -- this is intentional: SR's job
is "does this shape exist here", not "is this shape *all* that's here").

### 5.2 Relationship to `opamp_modules.yaml`

Two kinds of patterns:

- **Composite patterns** correspond 1:1 to a module variant in
  `opamp_modules.yaml` and reuse its `name` (snake_case, e.g.
  `differential_pair_nmos`, `current_mirror`, `wide_swing_current_mirror`).
  Their `devices`/`same_net`/`pins` are derived from / kept in sync with that
  variant's `devices` and `ports` entries (a variant's `devices` list with
  shared local net names *is* a connectivity template; its `ports` list *is*
  the pin export list). This gives:
  - naming alignment between SR's pattern library and `opamp_modules.yaml`'s
    vocabulary, and
  - a round-trip check: synthesize a circuit using variant X -> flatten ->
    recognize -> SR should report a `differential_pair_nmos`-named structure
    in the same place.
- **Primitive patterns** (`diode_connected_pair`, `cascode_pair`,
  `device_array`, ...) have no `opamp_modules.yaml` equivalent. They exist to
  support milestone 2's finer-grained, hierarchical recognition (mirroring
  `examples/subcircuits.xml`'s `MosfetDiodeArray` -> `MosfetDiodeStack` ->
  `MosfetCascodeCurrentMirror` nesting). **Not needed for the MVP** (see
  §5.4): `opamp_modules.yaml` variant device lists are flat, so composite
  patterns match directly against raw devices.

### 5.3 Matching algorithm

For a pattern with template devices `T1..Tn`:

1. Filter the netlist's devices by `type` to find candidates for each `Ti`.
2. Enumerate candidate assignments `Ti -> device` (a small backtracking
   search; patterns are 1-4 devices, so this is cheap -- no graph library
   needed).
3. For each assignment, check every `same_net` constraint: the named
   terminals (resolved through the assignment) must all reference the *same*
   net in the actual netlist.
4. If all constraints hold, and the pattern declares a `hook`, call it
   (`hook(assignment, parsed_netlist) -> bool`) for any extra check that's
   awkward to express declaratively (e.g. milestone-2 overlap resolution,
   symmetric-leg tie-breaking). Most patterns have no hook.
5. Emit a `RecognizedStructure`: `pins` resolved to actual net names via the
   pattern's `pins` map, `devices` = the assigned actual devices, `category`/
   `tech_type` from the pattern definition.

Run this for every pattern in the library against the full device list,
collecting all matches.

### 5.4 Ambiguity & completeness

- A device-set may match more than one pattern (e.g. two
  `opamp_modules.yaml` variants with structurally identical device templates
  but different categories/names). **SR reports all candidates** --
  `SubcircuitRecognitionResult.structures` may contain multiple
  `RecognizedStructure`s covering the same devices. SR does not pick a
  winner; FBR resolves this using topology context (§6).
- Devices matched by *no* pattern go into
  `SubcircuitRecognitionResult.unrecognized_devices`.
- **MVP invariant**: for a netlist produced by `to_flat_spice()` from a known
  `SynthesizedCircuit`, `unrecognized_devices` must be empty -- every device
  belongs to some `opamp_modules.yaml`-derived composite pattern. If a
  round-trip test finds non-empty `unrecognized_devices`, that's a
  pattern-library gap to fix, not expected runtime behavior.
- Milestone 2's `examples/netlist.ckt` is expected to exercise *both* fields
  for real: `examples/subcircuits.xml` shows `/m16` belonging to two
  different recognized structures simultaneously (multiple candidates), and
  ACST's `undefinedParts` category implies some devices may go unrecognized.

## 6. Layer 2 — Functional Block Recognizer (FBR)

New module: `circuitgenome/recognizer/functional_block_recognizer.py` (does
not exist yet).

### 6.1 Taxonomy-config concept

FBR is designed as **one engine parameterized by a taxonomy config**: "which
functional roles exist for this circuit family, and how is each recognized
from SR's output (+ raw device graph, for things SR didn't group -- e.g.
clock/control nets in future ADC/comparator work)". Different circuit
families (or different "views" of the same family, like ACST's taxonomy vs.
`opamp_modules.yaml`'s categories) are different taxonomy configs over the
same engine.

### 6.2 MVP taxonomy: the 7 `opamp_modules.yaml` categories

For the MVP, **no new taxonomy YAML file is needed**. The taxonomy *is*:

- the 7 canonical categories already defined in `opamp_modules.yaml`
  (`input_pair`, `load`, `tail_current`, `bias_generation`, `cmfb`,
  `compensation`, `second_stage`) -- each composite `RecognizedStructure`
  already carries one of these as its `category` (§5.2); and
- the matched `TopologyTemplate` (loaded via
  `circuitgenome.synthesizer.loader.load_topologies()`), which defines the
  slots that need filling and how each slot's ports connect to global nets.

A standalone taxonomy-config file becomes necessary only when (a) a non-opamp
circuit family is added (no `TopologyTemplate`-shaped equivalent exists yet),
or (b) the ACST-style taxonomy (§6.4) is implemented.

**MVP assumption**: the `TopologyTemplate` is *known* (the round-trip test
generated the circuit from a specific topology, so it knows which one). Full
topology identification from an arbitrary netlist -- "which of the 7
topology templates, if any, does this netlist match?" -- is explicitly **out
of scope for the MVP** and listed under deferred work (§7), since it's only
needed once the topology is *not* known in advance (true for milestone 2's
`examples/netlist.ckt`).

### 6.3 Slot-assignment algorithm

Given `SubcircuitRecognitionResult` and the known `TopologyTemplate`:

1. For each `Slot` in `topology.slots`, collect SR candidates whose
   `category == slot.category`.
2. If only one slot in the topology has this category, and exactly one
   candidate has this category, assign it directly:
   `slot_assignments[slot.name] = SlotAssignment(slot.name, candidate.name, candidate)`.
3. If multiple slots share a category (e.g. `comp_1`/`comp_2` in a 3-stage
   topology, or `second_stage`/`third_stage`), disambiguate using net
   connectivity: for each candidate, compare its `pins` (pin name -> net in
   *this* netlist) against `topology.slot_connections(slot.name)` (port ->
   expected global net, from the synthesizer's own wiring). The candidate
   whose pins line up with a given slot's expected nets is assigned to that
   slot.
4. Any SR candidates not assigned to a slot go into
   `unassigned_structures`; any `unrecognized_devices` from SR pass through
   unchanged.

Output: `FunctionalBlockRecognitionResult.slot_assignments`, which is
isomorphic to `SynthesizedCircuit.variant_map` (`{slot_name: variant name}`)
-- the MVP round-trip test compares these directly (§8).

### 6.4 Future taxonomy: ACST-style (not implemented for the MVP)

`examples/functional_blocks.xml`'s grouping (`gmParts` with
`firstStage`/`primarySecondStage`, `loadParts`, `biasParts`, `capacitances`,
`resistorParts`, `commonModeSignalDetectorParts`, `positiveFeedbackParts`,
`undefinedParts`) does **not** map 1:1 onto the 7 `opamp_modules.yaml`
categories -- e.g. ACST's `biasParts` appears to merge `bias_generation` and
`tail_current`-related structures, and `capacitances` includes a "load"
capacitor with no corresponding synthesizer slot at all. This is therefore a
**separate taxonomy config** (`AcstFunctionalPartition`, §3), to be designed
when milestone 2 is tackled -- not a replacement for §6.2-6.3.

## 7. Proposed Package Layout

For when implementation starts (not created by this design doc):

```
circuitgenome/recognizer/
  __init__.py                     # public API surface
  models.py                       # dataclasses from §3
  netlist_parser.py                # Layer 0 (§4)
  subcircuit_recognizer.py         # Layer 1 (§5)
  functional_block_recognizer.py   # Layer 2 (§6)
  config/
    subcircuit_patterns.yaml       # SR pattern library (§5.1-5.2)
  CLAUDE.md                        # internals doc, written once code exists
                                    # (mirrors synthesizer/CLAUDE.md)
```

Sibling to `circuitgenome/synthesizer/` and `circuitgenome/visualizer/`.

## 8. MVP Scope & Validation Strategy

Round-trip test shape (new test module, e.g. `tests/test_recognizer.py`,
not created by this design doc):

```python
circuits = synthesize({"topology": "one_stage_opamp"})
circuit = circuits[0]                       # known variant_map

spice = to_flat_spice(circuit)
parsed = netlist_parser.parse(spice)

sr_result = subcircuit_recognizer.recognize(parsed)
assert sr_result.unrecognized_devices == []

fbr_result = functional_block_recognizer.assign_slots(sr_result, circuit.topology)

for slot_name, variant in circuit.variant_map.items():
    assert fbr_result.slot_assignments[slot_name].pattern_name == variant.name
```

**Starting point**: `one_stage_opamp` (fewest slot categories: `input_pair`,
`load`, `tail_current`, `bias_generation`, possibly `cmfb`). Expanding to
2-stage / 3-stage topologies and full `opamp_modules.yaml` variant coverage
(~34 variants total) is **incremental follow-up work**, not committed to by
this design doc -- the architecture is intended to make that expansion purely
additive (new pattern YAML entries; no engine changes).

## 9. Deferred / Future Work

- **Milestone 2**: `examples/netlist.ckt` + renamed `subcircuits.xml`/
  `functional_blocks.xml` as a generality check on a non-CircuitGenome
  netlist. Likely needs:
  - primitive patterns (§5.2) and multi-level composition (primitives ->
    composites, mirroring the 3-level nesting in `subcircuits.xml`);
  - parser tolerances for `examples/netlist.ckt`'s syntax (e.g. `.suckt`);
  - genuine exercise of `unrecognized_devices` and multi-candidate
    `structures` (§5.4).
- **Topology auto-identification**: determining *which* `TopologyTemplate`
  (if any) a netlist matches, needed once the topology isn't known in advance
  (true for milestone 2).
- **ACST-style FBR taxonomy** (§6.4): `gmParts`/`loadParts`/`biasParts`/
  `capacitances`/... as an additional taxonomy config.
- **Additional circuit families** (LDO, comparator, ADC): new taxonomy
  configs, plus a handful of new SR primitives (cross-coupled latch,
  transmission-gate switch, sampling capacitor).
- **XML/JSON export** serializers for `RecognizedStructure` /
  `FunctionalBlockRecognitionResult` -- not designed here.
- **CLI / visualizer integration**: a `circuitgenome recognize` subcommand
  and/or a visualizer overlay showing recognized structures on the topology
  graph -- out of scope for this phase.

## 10. Open Assumptions to Revisit

- The MVP assumes the `TopologyTemplate` is known at recognition time (true
  for the round-trip test harness, which generated the circuit). This
  assumption does not hold for milestone 2 and must be revisited there
  (topology auto-identification).
- SR pattern-library coverage starts with whatever variants the first
  targeted topology (`one_stage_opamp`) needs, not all ~34
  `opamp_modules.yaml` variants at once. Each additional topology/variant is
  an additive pattern-YAML entry, per the architecture's extensibility goal.
