# circuitgenome/synthesizer — Topology Synthesizer

This package is the **Topology Synthesizer** (Phase 1 of CircuitGenome, the
only currently-implemented module). It enumerates op-amp circuits by combining
**module variants** (concrete circuit implementations of a functional
category) according to **topology templates** (wiring blueprints), and emits
SPICE netlists.

## File map

- `models.py` — plain dataclasses, no logic: `Device`, `PortDef`,
  `ModuleVariant`, `Slot`, `Connection`, `TopologyTemplate`,
  `SynthesizedCircuit`. **None are frozen** — `dataclasses.replace()` is the
  standard way to derive a modified copy (used by `bias_pruning.py`).
- `loader.py` — YAML → dataclasses. `Device.terminals` is built from *all*
  YAML device-entry keys except `ref`/`type` (so `d/g/s/b` for MOSFETs,
  `t1/t2` for resistors, `p/m` for capacitors — whatever the YAML uses).
- `config/opamp_modules.yaml` — module variant definitions, grouped by
  category (input_pair, load, tail_current, bias_generation, compensation,
  second_stage).
- `config/opamp_topologies.yaml` — topology templates: slots (which
  categories are needed, and under what local slot name) + `{slot, port,
  net}` connection rules.
- `compatibility.py` — polarity compatibility filter (`is_combination_valid`).
- `bias_pruning.py` — bias-rail pruning (`needed_bias_outputs`,
  `prune_bias_generation`).
- `synthesizer.py` — `enumerate_circuits`/`synthesize`, the orchestration
  pipeline. **Primary integration point for any new per-combination
  filter/transform.**
- `netlist.py` — `to_flat_spice` / `to_hierarchical_spice` serializers.
- `__init__.py` — public API surface (`__all__`); `compatibility` and
  `bias_pruning` are intentionally **not** exported (see pattern below).

## Module categories & canonical ports

Every variant in a category exposes the same **canonical port signature** —
the topology wires ports to global nets by name only; internal device
structure is invisible to the template. The full per-category port table and
variant list live in `docs/overview.rst` ("Module categories" / "Modular
interface contract") — check there (or the YAML) rather than assuming this
list is current.

## Net-naming & wiring conventions (synthesizer.py)

- `TopologyTemplate.connections` maps `(slot, port) -> global net`.
- `_resolve_devices`: device refs get prefixed `{slot_name}_{ref}`; any
  device terminal whose local net is *not* a connected port gets prefixed
  `{slot_name}_{local_net}` (internal node).
- A port declared `role: optional` that the topology does **not** wire gets
  `{slot_name}_{port}_nc` (explicit not-connected placeholder) for any device
  terminal that references it.
- `vdd`/`gnd` ports auto-connect to `vdd!`/`gnd!` unless the topology
  overrides them.
- **Bias rails**: `net_bias1..4` connect `bias_gen.out1-4` to
  `load.bias1/bias2/bias3/bias_cmfb` (same index) and, in multi-stage
  topologies, also to `second_stage*/third_stage*.bias -> net_bias1`.
  `tail_current.bias` is wired to its own dedicated `net_bias{N}` rail
  (N = 1-5, via `assign_tail_bias_rail`), never shared with
  load/second_stage/third_stage — this overrides the topology YAML's static
  `tail_current.bias -> net_tail_bias` connection, which is vestigial for
  bias-needing tails. `resistor_tail_vdd/gnd` declare `bias` as `optional`
  and are never wired.

## Polarity compatibility filter (`compatibility.py`)

Each `input_pair`/`load`/`tail_current` variant declares
`polarity: pmos_input | nmos_input | None`. `input_pair` is the reference:
`is_combination_valid` rejects a combination if any other tagged variant's
polarity doesn't match `input_pair`'s (untagged variants, e.g.
`inverter_based_input` and all `bias_generation` variants, impose no
constraint). To support a new/edited variant, just add the right `polarity:`
tag in YAML — no code changes needed.

## Bias-rail pruning (`bias_pruning.py`)

- `needed_bias_outputs(topology, variant_map)` does a **structural** check
  (actual device-terminal references, not declared `role`) of which of
  `out1..out4` are consumed by the `load` and any `second_stage`/
  `third_stage` slots.
- `prune_bias_generation(variant, needed)` keeps a contiguous prefix
  `out1..out_max(needed)` and drops the rest, along with the devices that
  exist only to drive dropped rails — a single shared-reference-plus-legs
  layout for all variants; see the module docstring for the full algorithm.
- **Load-bearing assumption**: `needed_bias_outputs` alone is always a
  contiguous prefix starting at 1 (`{}`, `{1}`, `{1,2}`, or `{1,2,3,4}` —
  never a gap like `{1,3}`). `prune_bias_generation` is called with
  `final_needed = needed | {tail_rail}`, which can also be `{1,2,3,4,5}`; the
  `max_needed >= 4` early-return covers both cases. If you add a load or
  second-stage variant that could need a non-prefix subset, re-check this
  before relying on pruning.
- `tail_current_needs_bias(variant)` detects (structurally) whether a
  `tail_current` variant needs its own bias voltage.
  `assign_tail_bias_rail(load_needed)` picks the next free rail after
  `load_needed` (or rail 5 if `load_needed == {1,2,3,4}`), in which case
  `extend_bias_generation(variant)` clones the fourth leg onto a new `out5`
  before pruning.
- Invoked once per combination in `enumerate_circuits`, **after**
  `is_combination_valid`, **before** `_build_port_net_map`/`_resolve_devices`.
  The pruned (and possibly extended) variant replaces `variant_map[slot.name]`
  for the `bias_generation` slot, so `SynthesizedCircuit.variant_map` and both
  SPICE serializers reflect the pruned device set.

## Pattern for small internal pure-function modules

`compatibility.py` and `bias_pruning.py` both follow the same template — use
it for future per-combination filters/transforms:

1. Small, dependency-light, pure functions over `ModuleVariant`/
   `TopologyTemplate`/`variant_map`.
2. Docstrings explain the *electrical rationale*, not just the mechanics.
3. Called internally from `synthesizer.py::enumerate_circuits`.
4. **Not** added to `circuitgenome/synthesizer/__init__.py`'s `__all__`
   (internal-only).
5. Documented via a dedicated `docs/api/<name>.rst` (`automodule` directive),
   linked from `docs/index.rst`'s API Reference toctree.

## `enumerate_circuits` pipeline order

1. `itertools.product` over per-slot candidate variants → `variant_map`.
2. `is_combination_valid(variant_map)` — skip on polarity mismatch.
3. `needed_bias_outputs` → (if `tail_current_needs_bias`)
   `assign_tail_bias_rail` → (if rail 5) `extend_bias_generation` →
   `prune_bias_generation`, mutating `variant_map[bias_gen_slot]` in place.
4. For each slot: build `slot_connections` (overriding `tail_current.bias`,
   and `bias_gen.out5` in the rail-5 case, with the assigned `net_bias{N}` —
   `variant_map[tail_slot]` itself is not mutated, only its net-resolution
   connections for this combo), then `_build_port_net_map` +
   `_resolve_devices` → `all_devices`.
5. Yield `SynthesizedCircuit(name, topology, variant_map, external_ports,
   devices)`.

Any new per-combination transform should slot in between steps 2 and 4,
following the same "compute once from `variant_map`, then overwrite the
relevant slot's entry in `variant_map`" pattern.

## Testing conventions (`tests/test_synthesizer.py`)

- Deterministic structural tests build a restricted `modules` dict (exactly
  one variant per category) and call `next(enumerate_circuits(topo,
  simple_modules))` for a fully deterministic single circuit.
- Broad coverage uses `pytest.mark.parametrize` over variant names / expected
  sets.
- Full-enumeration count tests are exact for 1- and 2-stage topologies; the
  3-stage fully-differential topology (~7.1M combos) is only checked via
  `next()` (non-empty) for speed, never materialized in full.
