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
  standard way to derive a modified copy (used by the pruning passes).
- `loader.py` — YAML → dataclasses. `Device.terminals` is built from *all*
  YAML device-entry keys except `ref`/`type` (so `d/g/s/b` for MOSFETs,
  `t1/t2` for resistors, `p/m` for capacitors — whatever the YAML uses).
- `config/opamp_modules.yaml` — module variant definitions, grouped by
  category (input_pair, load, tail_current, cmfb, compensation,
  second_stage). The bias_generation category has **no** variants here —
  the bias generator is constructed per combination (see below).
- `config/bias_legs.yaml` — the typed bias-leg library (multi-reference
  core + one leg template per rail kind) consumed by `bias_construction.py`;
  loaded by `load_bias_legs` into a `BiasLegLibrary`.
- `config/opamp_topologies.yaml` — topology templates: slots (which
  categories are needed, and under what local slot name) + `{slot, port,
  net}` connection rules.
- `synthesizer.py` — `enumerate_circuits`/`synthesize`, the orchestration
  pipeline. **Primary integration point for any new per-combination
  filter/transform.**
- `netlist.py` — `to_flat_spice` / `to_hierarchical_spice` serializers.
- `__init__.py` — public API surface (`__all__`); `polarity_compatibility` and
  `bias_construction` are intentionally **not** exported (see pattern below).

### Pipeline filters & pruning (internal, not in `__all__`)

- `polarity_compatibility.py` — polarity compatibility filter
  (`is_combination_valid`).
- `second_stage_compatibility.py` — stage-interface level compatibility
  filter (`is_second_stage_compatible`).
- `output_compatibility.py` — output-cardinality compatibility filter
  (`is_output_type_compatible`).
- `load_branch_compatibility.py` — untapped-load-branch compatibility filter
  (`is_load_branch_compatible`, helper `untapped_branch_is_dc_defined`).
- `cmfb_compatibility.py` — cmfb-slot compatibility filter and pruning
  (`is_cmfb_compatible`, `prune_cmfb`).
- `tail_current_compatibility.py` — tail_current-slot compatibility filter
  and pruning (`is_tail_current_compatible`, `prune_tail_current`).
- `bias_construction.py` — demand-driven bias construction
  (`required_rail_kinds`, `construct_bias_generation`; helper
  `rail_flavor_from_diode`). Not a filter: it *builds* the bias_generation
  slot's variant from the other slots' demands.
- `net_aliasing.py` — net-merge pass for `load` ports declared `alias_of`
  another `load` port (`compute_alias_net_rename`, `apply_net_rename`).

These eight modules all follow the same shape (see "Pattern for small internal
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
- **Bias rails**: the topology YAML statically wires `bias_gen`'s 8
  output rails (`out1..out8`) to one role each (the constructed variant
  only declares ports for consumed rails, so unconsumed connections are
  simply unused): `net_bias1..4`
  connect `out1-4` to `load.bias1/bias2/bias3/bias_cmfb` (same index);
  `net_bias5`/`net_bias6` connect `out5`/`out6` to `second_stage*.bias`/
  `third_stage*.bias` (shared by `_p`/`_n` instances in fully-differential
  topologies); `net_bias7` connects `out7` to `tail_current.bias`;
  `net_bias8` connects `out8` to `tail_current.bias_casc` (the cascode
  tails' wide-swing cascode-gate level, issue #111). All of
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

## Stage-interface compatibility filter (`second_stage_compatibility.py`)

A `second_stage` variant is structurally unbiasable against the first stage
when the gate level its *signal device* (the transistor whose gate is the
`in` port) requires falls outside the input pair's reachable output window
(issue #109; follower classification: issue #110): an NMOS pair's window is
confined high (its floor is the tail node), a PMOS pair's low — when the
required level and the window are disjoint, no sizing can bias the
interface (mirror-type loads pin the pair in triode, range-limited loads
rail). The required level follows from the signal device's *source
terminal*: common-source stages (source on a supply) put the gate one
`Vgs` from that supply and suit the **opposite**-polarity pair; followers
(source on the output node) put the gate one `Vgs` beyond the output,
toward the device's back rail, and suit the **same**-polarity pair
(`required_pair_type`). `is_second_stage_compatible` detects all of this
structurally (no YAML tags) and only constrains `second_stage`-category
slots whose `in` net is one of the load's output nets
(`load.out`/`out1`/`out2`) — the 3-stage topologies' `third_stage` slot
senses the second stage's wide-swing output instead and is deliberately
unconstrained. Untagged input pairs (`inverter_based_input`) impose no
constraint. New `second_stage` variants are classified automatically by
whichever device gates `in` and where its source sits.

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

## Untapped-load-branch compatibility filter (`load_branch_compatibility.py`)

In every `single_ended` topology only one first-stage branch node is tapped
(`load.out`/`out2` → the stage-output net); `load.in1`/`out1` (`net_diff1`)
is untapped, so its DC voltage must be defined by the load itself. A plain
rail-referenced current-source branch (`current_source_load_*`: gate on a
bias rail, no diode connection) leaves that node high-impedance between two
series current sources — no sizing can absorb the load-vs-tail current
mismatch, and one device always leaves saturation (issue #112).
`is_load_branch_compatible` detects this structurally (no YAML tags, like
`second_stage_compatibility`): the `in1` node counts as DC-defined when the
load has a diode-connected MOSFET on it (`active_load_*`), a resistor
touching it (`resistor_load_*`), or a MOSFET source terminal on it (the
cascode loads' folding/cascode devices); loads that never put a MOSFET drain
on `in1` are unconstrained. `fully_differential` topologies tap both
branches and are out of scope (CM definition there is the CMFB loop's job).
New `load` variants are classified automatically by what their devices
connect to `in1`.

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

## Demand-driven bias construction (`bias_construction.py`)

The bias generator is **constructed**, not enumerated: `bias_generation` is
excluded from the slot product, and `build_circuit` calls
`construct_bias_generation` after `prune_cmfb`/`prune_tail_current` (so
emptied placeholders demand nothing). `required_rail_kinds` classifies every
*consumed* rail structurally (actual device references, no YAML tags):

- a consumer gate whose source sits on a supply → `gate_vdd`/`gate_gnd`
  (the leg's diode-connected device is the mirror **master** of its
  consumers — sizing by W/L ratio, not voltage matching);
- a diode-connected consumer on the rail (mirror tails, incl. the cascode
  tails' stacked diode) → `current_source`/`current_sink` (the rail is a
  current interface; the leg is a **bare** mirror — no bias-side diode to
  duplicate or fight the tail's reference, issue #99's rail-7 classes);
- a consumer gate whose source is an **internal** node (cascode gates) →
  `cascode_gnd`/`cascode_vdd` (a level leg: diode-connected device riding a
  small floor resistor to the back supply, `out = V_GS + I·R`; the diode
  covers the Vth-tracking `V_GS` part, the resistor only the small Vdsat
  floor — both sized per rail by the sizer's `bias_levels` pass from the
  consumer stack, issue #99's formerly-parked cascode class);
- conflicting votes → `tunable` (mirror into a resistor, per-rail sizable).

`construct_bias_generation` assembles the variant (`constructed_bias`) from
`config/bias_legs.yaml`: an NMOS master diode on `ibias` (always), a `pref`
branch deriving the PMOS-side reference (only when a leg references `pref`;
`pref` is not a port, so it resolves to a slot-internal net), and one leg
per consumed rail (per-leg nets `out`/`mid` → `out{i}`/`mid{i}`, refs
suffixed with the rail index). The pref branch is **cascoded**: a wide-swing
`ncasc` level branch (PMOS mirror into a narrow diode) gates a cascode that
pins the branch mirror's Vds near the master's, closing most of the
extra-mirror-hop λ error (#103's A/B vs `magic_battery_bias`). The `ncasc`
branch mirrors `feed_pref` — a small uncascoded feeder copy of the pref
derivation — NOT `pref` itself: gating it from `pref` closes a loop with a
degenerate all-off operating point that ngspice measurably converges to on
some circuits (the classic wide-swing startup hazard). The
`feed_pref`/`prefsrc`/`ncasc` nets are slot-internal like `pref`, and gates
on `*_pref`/`*_ncasc` are excluded from `is_signal_device` (sizer taxonomy;
`feed_pref` is named so its prefixed form ends in `_pref`).
Only consumed rails get ports/legs, so the old flavor filter
(`is_bias_flavor_compatible`), rail pruning (`prune_bias_generation`), and
redundant-diode prune (`prune_redundant_tail_diode`) are all subsumed —
mismatches are unconstructable rather than filtered (decision record for the
retired filter: issue #102; redesign: the issue #99 follow-up discussion).

Recognizer coupling: the `constructed_bias` pattern + `constructed_bias_legs`
hook (recognizer) discover the constructed shape per leg, including the
cascode legs (diode + floor resistor) and the cascoded pref chain; purely
NMOS-referenced diode/mirror shapes still resolve to the historical
`diode_connected_mosfet_bias` pattern (its hook finds the identical device
set — a cascode leg or pref cascode counts as constructed-only evidence),
and the legacy monolith patterns remain for external netlists.

## Pattern for small internal pure-function modules

`polarity_compatibility.py`, `bias_construction.py`, and `net_aliasing.py` all
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

1. `itertools.product` over per-slot candidate variants → `variant_map`
   (the `bias_generation` slot is excluded from the product — its variant
   is constructed in step 10).
2. `is_combination_valid(variant_map)` — skip on polarity mismatch.
3. `is_second_stage_compatible(topology, variant_map)` — skip on
   stage-interface level mismatch (see "Stage-interface compatibility
   filter" above).
4. `is_output_type_compatible(topology, variant_map)` — skip on
   output-cardinality mismatch.
5. `is_load_branch_compatible(topology, variant_map)` — skip if a
   `single_ended` topology's untapped branch node would be left
   high-impedance by the load (see "Untapped-load-branch compatibility
   filter" above).
6. `is_cmfb_compatible(variant_map)` — skip if `cmfb`'s variant choice is
   irrelevant for this `load` (see "CMFB compatibility filter" above).
7. `prune_cmfb(variant_map["cmfb"], variant_map["load"])`, replacing
   `variant_map["cmfb"]` (only if the topology has a `cmfb` slot).
8. `is_tail_current_compatible(variant_map)` — skip if `tail_current`'s
   variant choice is irrelevant for this `input_pair` (see "Tail-current
   compatibility filter" above).
9. `prune_tail_current(variant_map["tail_current"], variant_map["input_pair"])`,
   replacing `variant_map["tail_current"]`.
10. `construct_bias_generation(topology, variant_map, bias_legs)` →
    `variant_map[bias_gen_slot]` (see "Demand-driven bias construction"
    above; must run after the cmfb/tail_current prunes so placeholders
    demand nothing).
11. For each slot: `slot_connections = topology.slot_connections(slot.name)`,
    then `_build_port_net_map` + `_resolve_devices` → `all_devices`. The
    `load` slot's `port_net_map` is captured separately as
    `load_port_net_map`.
12. `compute_alias_net_rename(variant_map["load"], load_port_net_map,
    topology.external_ports)` → `apply_net_rename(all_devices, rename)` —
    net-merge pass for `load` ports declared `alias_of` another `load` port
    (see "Net-naming & wiring conventions" above).
13. Yield `SynthesizedCircuit(name, topology, variant_map, external_ports,
    devices)`.

Any new per-combination transform should slot in between steps 6 and 11,
following the same "compute once from `variant_map`, then overwrite the
relevant slot's entry in `variant_map`" pattern.

## Testing conventions (`tests/test_synthesizer.py`)

- Deterministic structural tests build a restricted `modules` dict (exactly
  one variant per category) and call `next(enumerate_circuits(topo,
  simple_modules))` for a fully deterministic single circuit.
- Broad coverage uses `pytest.mark.parametrize` over variant names / expected
  sets.
- Full-enumeration count tests are exact for 1- and 2-stage topologies; the
  3-stage fully-differential topology (~0.46M combos) is only checked via
  `next()` (non-empty) for speed, never materialized in full.
