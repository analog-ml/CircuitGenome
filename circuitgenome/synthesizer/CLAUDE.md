# circuitgenome/synthesizer — Topology Synthesizer

This package is the **Topology Synthesizer** (Phase 1 of CircuitGenome, the
only currently-implemented module). It enumerates op-amp circuits by combining
**module variants** (concrete circuit implementations of a functional
category) according to **topology templates** (wiring blueprints), and emits
SPICE netlists.

## File map

### Core pipeline & data model

- `models.py` — plain dataclasses, no logic: `Device`, `PortDef`,
  `ModuleVariant`, `Slot`, `Connection`, `TopologyTemplate`,
  `SynthesizedCircuit`. **None are frozen** — `dataclasses.replace()` is the
  standard way to derive a modified copy (used by `bias_pruning.py`).
- `loader.py` — YAML → dataclasses. `Device.terminals` is built from *all*
  YAML device-entry keys except `ref`/`type` (so `d/g/s/b` for MOSFETs,
  `t1/t2` for resistors, `p/m` for capacitors — whatever the YAML uses).
- `config/opamp_modules.yaml` — module variant definitions, grouped by
  category (input_pair, load, tail_current, bias_generation, cmfb,
  compensation, second_stage).
- `config/opamp_topologies.yaml` — topology templates: slots (which
  categories are needed, and under what local slot name) + `{slot, port,
  net}` connection rules.
- `synthesizer.py` — `enumerate_circuits`/`synthesize`, the orchestration
  pipeline. **Primary integration point for any new per-combination
  filter/transform.**
- `netlist.py` — `to_flat_spice` / `to_hierarchical_spice` serializers.
- `__init__.py` — public API surface (`__all__`); `polarity_compatibility` and
  `bias_pruning` are intentionally **not** exported (see pattern below).

### Pipeline filters & pruning (internal, not in `__all__`)

- `polarity_compatibility.py` — polarity compatibility filter
  (`is_combination_valid`).
- `output_compatibility.py` — output-cardinality compatibility filter
  (`is_output_type_compatible`).
- `cmfb_compatibility.py` — cmfb-slot compatibility filter and pruning
  (`is_cmfb_compatible`, `prune_cmfb`).
- `tail_current_compatibility.py` — tail_current-slot compatibility filter
  and pruning (`is_tail_current_compatible`, `prune_tail_current`).
- `bias_pruning.py` — bias-rail pruning (`needed_bias_outputs`,
  `prune_bias_generation`).
- `net_aliasing.py` — net-merge pass for `load` ports declared `alias_of`
  another `load` port (`compute_alias_net_rename`, `apply_net_rename`).

These six modules all follow the same shape (see "Pattern for small internal
pure-function modules" below) and are invoked, in this order, from
`enumerate_circuits` (see "pipeline order" below).

## Module categories & canonical ports

Every variant in a category exposes the same **canonical port signature** —
the topology wires ports to global nets by name only; internal device
structure is invisible to the template. The full per-category port table and
variant list live in `docs/overview.rst` ("Module categories" / "Modular
interface contract") — check there (or the YAML) rather than assuming this
list is current.

## Net-naming & wiring conventions (synthesizer.py)

- `TopologyTemplate.connections` maps `(slot, port) -> global net`.
- `_resolve_devices`: device refs get **suffixed** `{ref}_{slot_name}` (e.g.
  `m1_input_pair`) so the leading character of the global ref still matches
  SPICE's type-inference convention (`m`/`mn`/`mp`→MOSFET, `r`→resistor,
  `c`→capacitor, ...); any device terminal whose local net is *not* a
  connected port gets **prefixed** `{slot_name}_{local_net}` (internal node)
  -- refs and internal-node net names follow different (opposite) ordering.
- A port declared `role: optional` that the topology does **not** wire gets
  `{slot_name}_{port}_nc` (explicit not-connected placeholder) for any device
  terminal that references it.
- `vdd`/`gnd` ports auto-connect to `vdd!`/`gnd!` unless the topology
  overrides them.
- **Bias rails**: `bias_gen` has 7 independent output rails (`out1..out7`),
  statically wired by the topology YAML to one role each: `net_bias1..4`
  connect `out1-4` to `load.bias1/bias2/bias3/bias_cmfb` (same index);
  `net_bias5`/`net_bias6` connect `out5`/`out6` to `second_stage*.bias`/
  `third_stage*.bias` (shared by `_p`/`_n` instances in fully-differential
  topologies); `net_bias7` connects `out7` to `tail_current.bias`. All of
  these connections are static (no per-combination rewiring).
  `resistor_tail_vdd/gnd` declare `bias` as `optional` and are never wired.
  In `fully_differential` topologies, `net_bias4` also feeds the `cmfb`
  slot's `bias` port; `load.bias_cmfb` itself is repointed to
  `net_cmfb_out` (the `cmfb` slot's `out`), not `net_bias4` directly.
  `cmfb.vref` is wired to `vcm_ref`, a new external port present only on
  `fully_differential` topologies. However, `cmfb` is only a real consumer
  of rail 4 when `load.output_cardinality == "differential"` (see "CMFB
  compatibility filter" below) -- otherwise `cmfb` is pruned to an empty
  placeholder, rail 4 is not needed, and `vcm_ref` is left unconnected.
- **`load` in/out nets**: `load.in1`/`in2` (folding nodes fed by
  `input_pair.out1`/`out2`) and `load.out`/`out1`/`out2` (the load's output
  node(s)) are wired to *separate* nets by every topology -- `net_loadout1`/
  `net_loadout2` (FD topologies, sensed by `cmfb`/`second_stage*`/`comp*`) or
  the stage-output net (SE/1-stage topologies, via `load.out`/`out2`); SE
  topologies additionally introduce `net_fold2` for `load.in2`/
  `input_pair.out2`. A net-merge pass (`net_aliasing.py`, run at the end of
  `enumerate_circuits`) then collapses any `load` port declared `alias_of`
  another `load` port (`out1`/`out2` on the 6 resistor/active/current-source
  loads, aliased to `in1`/`in2`) back onto its target's net -- restoring the
  single shared in/out node those variants' devices assume, while leaving the
  6 cascode loads' distinct in/out nets intact.

## Polarity compatibility filter (`polarity_compatibility.py`)

Each `input_pair`/`load`/`tail_current` variant declares
`polarity: pmos_input | nmos_input | None`. `input_pair` is the reference:
`is_combination_valid` rejects a combination if any other tagged variant's
polarity doesn't match `input_pair`'s (untagged variants, e.g.
`inverter_based_input` and all `bias_generation` variants, impose no
constraint). To support a new/edited variant, just add the right `polarity:`
tag in YAML — no code changes needed.

## Output-cardinality compatibility filter (`output_compatibility.py`)

Each `load` variant declares `output_cardinality: "single" | "differential" |
None`. `"single"` (folded-cascode single-output and telescopic-cascode loads)
declares `out` as mandatory, which only a `single_ended` topology wires (to
the stage-output net); `"differential"` (folded-cascode differential-output
loads) declares `out1`/`out2` as mandatory cascode-output nodes, which only a
`fully_differential` topology wires (to `net_loadout1`/`net_loadout2`).
`is_output_type_compatible` rejects a combination if `load`'s
`output_cardinality` (if set) doesn't match the topology's `output_type` --
otherwise the mandatory port(s) would be left floating (unconnected)
(untagged loads, i.e. resistor/active/current-source, impose no constraint --
their `out1`/`out2` are `alias_of in1`/`in2` and merged back by
`net_aliasing.py` regardless of `output_type`). To support a new/edited
`load` variant, just add the right `output_cardinality:` tag in YAML — no
code changes needed.

## CMFB compatibility filter & pruning (`cmfb_compatibility.py`)

Of the 12 `load` variants, only the 2 tagged `output_cardinality:
"differential"` declare `bias_cmfb` as a real `role: input` consumer; the
other 10 declare it `role: optional` and never reference it, so
`cmfb.out -> net_cmfb_out -> load.bias_cmfb` drives nothing.
`is_cmfb_compatible` rejects combinations where `load`'s
`output_cardinality` isn't `"differential"` and `cmfb` isn't
`CANONICAL_CMFB_VARIANT` (`resistive_sense_cmfb`) -- this collapses the
otherwise-duplicate choice between `cmfb` variants for those loads down to
one. `prune_cmfb` then replaces that canonical variant with an empty
placeholder (`name="cmfb_absent"`, no ports, no devices) for the same loads,
so it contributes nothing and `cmfb.bias` is not counted by
`needed_bias_outputs`. To support a new/edited `load` variant as a genuine
`cmfb` consumer, tag it `output_cardinality: "differential"` and give it a
real `bias_cmfb: role: input` -- no code changes needed here.

## Tail-current compatibility filter & pruning (`tail_current_compatibility.py`)

Of the 5 `input_pair` variants, only the 4 `differential_pair_*` variants
reference their `tail` port from a device terminal (`s`/`b: tail` on the
tail transistor, or `t2: tail` on the degenerated variants' tail resistor);
`inverter_based_input` -- two back-to-back CMOS inverters -- is self-biased
and never references `tail`, so `input_pair.tail -> net_tail <-
tail_current.out` drives nothing. `is_tail_current_compatible` rejects
combinations where `input_pair` doesn't reference `tail` and `tail_current`
isn't `CANONICAL_TAIL_CURRENT_VARIANT` (`current_mirror_tail_pmos`) -- this
collapses the otherwise-duplicate choice between the 6 `tail_current`
variants for `inverter_based_input` down to one. `prune_tail_current` then
replaces that canonical variant with an empty placeholder
(`name="tail_current_absent"`, no ports, no devices) for the same
`input_pair`, so it contributes nothing, `net_tail` is no longer floating,
and `tail_current.bias` is not counted by `needed_bias_outputs`. To support
a new/edited `input_pair` variant as a genuine `tail_current` consumer, wire
one of its device terminals to `tail` -- no code changes needed here.

## Bias-rail pruning (`bias_pruning.py`)

- `needed_bias_outputs(topology, variant_map)` does a **structural** check
  (actual device-terminal references, not declared `role`) of which of
  `out1..out7` are consumed by the `load`, `second_stage`, `third_stage`, and
  `tail_current` slots — each role is detected independently via the
  topology's static `net_bias{1-7}` wiring. The result can be any subset of
  `{1..7}`, not necessarily contiguous (e.g. `{1, 5, 7}`).
- `prune_bias_generation(variant, needed)` drops every rail not in `needed`,
  along with the devices that exist only to drive dropped rails — a single
  shared-reference-plus-7-legs layout for all variants; see the module
  docstring for the full algorithm. If `needed` covers all of `{1..7}`,
  `variant` is returned unchanged.
- Invoked once per combination in `enumerate_circuits`, **after**
  `is_combination_valid`, **before** `_build_port_net_map`/`_resolve_devices`.
  The pruned variant replaces `variant_map[slot.name]` for the
  `bias_generation` slot, so `SynthesizedCircuit.variant_map` and both SPICE
  serializers reflect the pruned device set.

## Pattern for small internal pure-function modules

`polarity_compatibility.py`, `bias_pruning.py`, and `net_aliasing.py` all
follow the same template — use it for future per-combination
filters/transforms:

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
3. `is_output_type_compatible(topology, variant_map)` — skip on
   output-cardinality mismatch.
4. `is_cmfb_compatible(variant_map)` — skip if `cmfb`'s variant choice is
   irrelevant for this `load` (see "CMFB compatibility filter" above).
5. `prune_cmfb(variant_map["cmfb"], variant_map["load"])`, replacing
   `variant_map["cmfb"]` (only if the topology has a `cmfb` slot).
6. `is_tail_current_compatible(variant_map)` — skip if `tail_current`'s
   variant choice is irrelevant for this `input_pair` (see "Tail-current
   compatibility filter" above).
7. `prune_tail_current(variant_map["tail_current"], variant_map["input_pair"])`,
   replacing `variant_map["tail_current"]`.
8. `needed_bias_outputs` → `prune_bias_generation`, replacing
   `variant_map[bias_gen_slot]`.
9. For each slot: `slot_connections = topology.slot_connections(slot.name)`,
   then `_build_port_net_map` + `_resolve_devices` → `all_devices`. The
   `load` slot's `port_net_map` is captured separately as
   `load_port_net_map`.
10. `compute_alias_net_rename(variant_map["load"], load_port_net_map,
    topology.external_ports)` → `apply_net_rename(all_devices, rename)` —
    net-merge pass for `load` ports declared `alias_of` another `load` port
    (see "Net-naming & wiring conventions" above).
11. Yield `SynthesizedCircuit(name, topology, variant_map, external_ports,
    devices)`.

Any new per-combination transform should slot in between steps 4 and 9,
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
