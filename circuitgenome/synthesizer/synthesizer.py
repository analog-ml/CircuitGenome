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

from .compatibility import is_combination_valid
from .loader import load_modules, load_topologies
from .models import Device, ModuleVariant, SynthesizedCircuit, TopologyTemplate


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


def enumerate_circuits(
    topology: TopologyTemplate,
    modules: dict[str, list[ModuleVariant]],
    config: dict | None = None,
) -> Iterator[SynthesizedCircuit]:
    """Yield one :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`
    for every valid combination of module variants in *topology*.

    Combinations that mix incompatible ``polarity`` tags (see
    :func:`~circuitgenome.synthesizer.compatibility.is_combination_valid`) are
    skipped -- these would leave a shared node with no DC current path.

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
        if not is_combination_valid(variant_map):
            continue

        all_devices: list[tuple[str, Device]] = []
        for slot in topology.slots:
            variant = variant_map[slot.name]
            slot_connections = topology.slot_connections(slot.name)
            port_net_map = _build_port_net_map(slot.name, variant, slot_connections)
            all_devices.extend(_resolve_devices(slot.name, variant, port_net_map))

        name = _circuit_name(topology, variant_map)
        yield SynthesizedCircuit(
            name=name,
            topology=topology.name,
            variant_map=variant_map,
            external_ports=topology.external_ports,
            devices=all_devices,
        )


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
