"""
Topology synthesis engine.

Enumerates every valid combination of module variants for a given topology
template and assembles each combination into a
:class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`.

The main entry points are:

- :func:`synthesize` — high-level API that loads YAML configs and returns a
  list of circuits.
- :func:`enumerate_circuits` — lower-level iterator over circuits for a single
  topology, useful when you want to stream results or supply custom configs.
"""
from __future__ import annotations
import itertools
from pathlib import Path
from typing import Iterator

from .bias_pruning import needed_bias_outputs, prune_bias_generation
from .cmfb_compatibility import is_cmfb_compatible, prune_cmfb
from .polarity_compatibility import is_combination_valid
from .loader import load_modules, load_topologies
from .models import Device, ModuleVariant, SynthesizedCircuit, TopologyTemplate
from .net_aliasing import apply_net_rename, compute_alias_net_rename
from .output_compatibility import is_output_type_compatible
from .tail_current_compatibility import is_tail_current_compatible, prune_tail_current


def _resolve_devices(
    slot_name: str,
    variant: ModuleVariant,
    port_net_map: dict[str, str],
) -> list[tuple[str, Device]]:
    """
    Expand a module's devices into global (ref, Device) pairs by substituting
    local port names with the global net names assigned in the topology.

    Internal nets (not in port_net_map) are prefixed with the slot name to
    avoid collisions across modules.
    """
    port_names = {p.name for p in variant.ports}
    optional_ports = {p.name for p in variant.ports if p.role == "optional"}
    result = []
    for dev in variant.devices:
        resolved_terminals: dict[str, str] = {}
        for term, local_net in dev.terminals.items():
            if local_net in port_net_map:
                resolved_terminals[term] = port_net_map[local_net]
            elif local_net in port_names and local_net in optional_ports:
                resolved_terminals[term] = f"{slot_name}_{local_net}_nc"
            else:
                resolved_terminals[term] = f"{slot_name}_{local_net}"
        global_ref = f"{slot_name}_{dev.ref}"
        result.append((global_ref, Device(ref=global_ref, type=dev.type, terminals=resolved_terminals)))
    return result


def _build_port_net_map(
    slot_name: str,
    variant: ModuleVariant,
    slot_connections: dict[str, str],
) -> dict[str, str]:
    """
    Build a mapping from local port name → global net for a slot.
    Supply ports (vdd, gnd) are connected to vdd!/gnd! by convention.
    """
    port_net_map: dict[str, str] = {}
    for port_def in variant.ports:
        pname = port_def.name
        if pname in slot_connections:
            port_net_map[pname] = slot_connections[pname]
        elif pname == "vdd":
            port_net_map[pname] = "vdd!"
        elif pname == "gnd":
            port_net_map[pname] = "gnd!"
    return port_net_map


def _circuit_name(topology: TopologyTemplate, variant_map: dict[str, ModuleVariant]) -> str:
    parts = [topology.name]
    for slot in topology.slots:
        parts.append(variant_map[slot.name].name)
    return "__".join(parts)


def build_circuit(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> SynthesizedCircuit | None:
    """Assemble a single :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`
    from *variant_map*, applying all cross-slot compatibility filters and
    pruning passes described in :func:`enumerate_circuits`.

    Returns ``None`` if *variant_map* is rejected by
    :func:`~circuitgenome.synthesizer.polarity_compatibility.is_combination_valid`,
    :func:`~circuitgenome.synthesizer.output_compatibility.is_output_type_compatible`,
    :func:`~circuitgenome.synthesizer.cmfb_compatibility.is_cmfb_compatible`, or
    :func:`~circuitgenome.synthesizer.tail_current_compatibility.is_tail_current_compatible`.

    :param topology: The wiring template that defines slots and net connections.
    :param variant_map: One :class:`~circuitgenome.synthesizer.models.ModuleVariant`
                         per slot, keyed by slot name.
    """
    variant_map = dict(variant_map)  # don't mutate caller's dict

    if not is_combination_valid(variant_map):
        return None
    if not is_output_type_compatible(topology, variant_map):
        return None
    if not is_cmfb_compatible(variant_map):
        return None
    if "cmfb" in variant_map:
        variant_map["cmfb"] = prune_cmfb(variant_map["cmfb"], variant_map["load"])

    if not is_tail_current_compatible(variant_map):
        return None
    variant_map["tail_current"] = prune_tail_current(
        variant_map["tail_current"], variant_map["input_pair"]
    )

    needed = needed_bias_outputs(topology, variant_map)
    bias_slot = next(s for s in topology.slots if s.category == "bias_generation")
    variant_map[bias_slot.name] = prune_bias_generation(variant_map[bias_slot.name], needed)

    all_devices: list[tuple[str, Device]] = []
    load_port_net_map: dict[str, str] = {}
    for slot in topology.slots:
        variant = variant_map[slot.name]
        slot_connections = topology.slot_connections(slot.name)
        port_net_map = _build_port_net_map(slot.name, variant, slot_connections)
        if slot.category == "load":
            load_port_net_map = port_net_map
        all_devices.extend(_resolve_devices(slot.name, variant, port_net_map))

    rename = compute_alias_net_rename(variant_map["load"], load_port_net_map, topology.external_ports)
    all_devices = apply_net_rename(all_devices, rename)

    name = _circuit_name(topology, variant_map)
    return SynthesizedCircuit(
        name=name,
        topology=topology.name,
        variant_map=variant_map,
        external_ports=topology.external_ports,
        devices=all_devices,
    )


def enumerate_circuits(
    topology: TopologyTemplate,
    modules: dict[str, list[ModuleVariant]],
    config: dict | None = None,
) -> Iterator[SynthesizedCircuit]:
    """Yield one :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`
    for every valid combination of module variants in *topology*.

    Combinations that mix incompatible ``polarity`` tags (see
    :func:`~circuitgenome.synthesizer.polarity_compatibility.is_combination_valid`) are
    skipped -- these would leave a shared node with no DC current path.

    Combinations where ``load``'s ``output_cardinality`` tag (if set) doesn't
    match *topology*'s ``output_type`` (see
    :func:`~circuitgenome.synthesizer.output_compatibility.is_output_type_compatible`)
    are also skipped -- these would leave the load's mandatory output port(s)
    (``out`` for ``"single"``, ``out1``/``out2`` for ``"differential"``)
    unconnected: only ``single_ended`` topologies define a net for
    ``load.out``, and only ``fully_differential`` topologies define
    ``net_loadout1``/``net_loadout2`` for ``load.out1``/``out2``.

    After each slot's ports are wired, a net-merge pass (see
    :func:`~circuitgenome.synthesizer.net_aliasing.compute_alias_net_rename`/
    :func:`~circuitgenome.synthesizer.net_aliasing.apply_net_rename`) collapses
    ``load`` ports declared ``alias_of`` another ``load`` port (``out1``/
    ``out2`` on the 6 resistor/active/current-source loads) onto their target
    port's net, restoring the shared in/out node those variants' devices
    assume.

    For topologies with a ``cmfb`` slot, combinations where ``load``'s
    ``output_cardinality`` isn't ``"differential"`` (i.e. ``cmfb.out`` would
    drive nothing) are restricted to the canonical ``cmfb`` variant (see
    :func:`~circuitgenome.synthesizer.cmfb_compatibility.is_cmfb_compatible`),
    and that variant is then pruned to an empty placeholder (see
    :func:`~circuitgenome.synthesizer.cmfb_compatibility.prune_cmfb`) so it
    contributes no devices and ``cmfb.bias`` is not counted as a needed bias
    rail.

    Likewise, combinations where ``input_pair`` doesn't reference its
    ``tail`` port (currently only ``inverter_based_input``, which is
    self-biased and would otherwise leave ``net_tail`` floating) are
    restricted to the canonical ``tail_current`` variant (see
    :func:`~circuitgenome.synthesizer.tail_current_compatibility.is_tail_current_compatible`),
    and that variant is then pruned to an empty placeholder (see
    :func:`~circuitgenome.synthesizer.tail_current_compatibility.prune_tail_current`)
    so it contributes no devices and ``tail_current.bias`` is not counted as
    a needed bias rail.

    The ``bias_generation`` variant in each combination is pruned to only the
    ``out1``..``out7`` rails actually consumed by the other slots (see
    :func:`~circuitgenome.synthesizer.bias_pruning.prune_bias_generation`),
    removing unused output ports and their dedicated devices. ``out1``..
    ``out4`` feed ``load``'s cascode bias inputs; ``out5``/``out6`` feed
    ``second_stage``/``third_stage`` (shared across ``_p``/``_n`` instances in
    fully-differential topologies via the topology's static wiring); ``out7``
    feeds ``tail_current`` (current-mirror / cascode-current-mirror variants
    only -- resistor-tail variants declare ``bias`` as ``optional`` and need
    no rail). Each role's rail is independent of the others, so
    ``load``/``second_stage``/``third_stage``/``tail_current`` never share a
    bias voltage.

    :param topology: The wiring template that defines slots and net connections.
    :param modules: Module variant pool, keyed by category name.  Typically the
                    return value of :func:`~circuitgenome.synthesizer.loader.load_modules`.
    :param config: Reserved for future per-enumeration filters (currently unused).
    :raises ValueError: If a required module category has no available variants.

    Example::

        from circuitgenome.synthesizer.loader import load_modules, load_topologies
        from circuitgenome.synthesizer.synthesizer import enumerate_circuits
        from circuitgenome.synthesizer.netlist import to_flat_spice

        modules = load_modules()
        topology = next(t for t in load_topologies() if t.name == "one_stage_opamp")

        for circuit in enumerate_circuits(topology, modules):
            print(to_flat_spice(circuit))
    """
    per_slot: list[list[ModuleVariant]] = []
    for slot in topology.slots:
        candidates = modules.get(slot.category, [])
        if not candidates:
            raise ValueError(f"No module variants found for category '{slot.category}'")
        per_slot.append(candidates)

    for combo in itertools.product(*per_slot):
        variant_map: dict[str, ModuleVariant] = {
            slot.name: variant
            for slot, variant in zip(topology.slots, combo)
        }
        circuit = build_circuit(topology, variant_map)
        if circuit is not None:
            yield circuit


def synthesize(
    config: dict | None = None,
    modules_path: str | Path | None = None,
    topologies_path: str | Path | None = None,
) -> list[SynthesizedCircuit]:
    """Generate all op-amp circuits matching the given configuration.

    Loads YAML definitions, applies filters, and returns a flat list of
    :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit` objects.

    :param config: Optional filter dictionary.  Supported keys:

                   - ``topology`` *(str)* — exact topology name.
                   - ``stages`` *(int)* — ``1``, ``2``, or ``3``.
                   - ``output_type`` *(str)* — ``"single_ended"`` or
                     ``"fully_differential"``.
                   - ``compensation_scheme`` *(str)* — for 3-stage
                     topologies, ``"nested_miller"`` or
                     ``"reversed_nested_miller"``.

    :param modules_path: Path to a custom modules YAML file.  Uses the
                         built-in definitions when omitted.
    :param topologies_path: Path to a custom topologies YAML file.  Uses the
                            built-in definitions when omitted.
    :returns: All synthesized circuits across every matching topology.

    Example::

        from circuitgenome import synthesize
        from circuitgenome.synthesizer import to_flat_spice

        circuits = synthesize({"stages": 2, "output_type": "single_ended"})
        print(f"{len(circuits)} circuits generated")
        print(to_flat_spice(circuits[0]))
    """
    modules = load_modules(modules_path)
    topologies = load_topologies(topologies_path)

    cfg = config or {}
    if "topology" in cfg:
        topologies = [t for t in topologies if t.name == cfg["topology"]]
    if "stages" in cfg:
        topologies = [t for t in topologies if t.config.get("stages") == cfg["stages"]]
    if "output_type" in cfg:
        topologies = [t for t in topologies if t.config.get("output_type") == cfg["output_type"]]
    if "compensation_scheme" in cfg:
        topologies = [t for t in topologies if t.config.get("compensation_scheme") == cfg["compensation_scheme"]]

    circuits: list[SynthesizedCircuit] = []
    for topology in topologies:
        circuits.extend(enumerate_circuits(topology, modules))
    return circuits
