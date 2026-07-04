"""
Graph data model for visualizing topologies as slot/connection diagrams.

:func:`topology_to_graph` turns a :class:`~circuitgenome.synthesizer.models.TopologyTemplate`
and a chosen ``variant_map`` into a :class:`VizGraph` -- one :class:`VizNode`
per slot, one :class:`VizEdge` per pair of distinct slots sharing a net
(excluding the supply nets ``vdd!``/``gnd!``). This is the data layer for
``circuitgenome.visualizer.app``; it has no Streamlit/pyvis dependency so it
can be used and tested independently of the ``viz`` extra.

:func:`explain_incompatibility` reports which of the
:func:`~circuitgenome.synthesizer.synthesizer.build_circuit` compatibility
filters would reject a given ``variant_map``, for surfacing in the UI when a
combination doesn't assemble into a circuit.
"""
from __future__ import annotations
import itertools
from dataclasses import dataclass, field

from circuitgenome.synthesizer.cmfb_compatibility import is_cmfb_compatible
from circuitgenome.synthesizer.load_branch_compatibility import is_load_branch_compatible
from circuitgenome.synthesizer.models import Connection, ModuleVariant, TopologyTemplate
from circuitgenome.synthesizer.output_compatibility import is_output_type_compatible
from circuitgenome.synthesizer.polarity_compatibility import is_combination_valid
from circuitgenome.synthesizer.second_stage_compatibility import (
    is_second_stage_compatible,
)
from circuitgenome.synthesizer.tail_current_compatibility import (
    is_tail_current_compatible,
)

# Nets that connect to every slot and would otherwise dominate the graph with
# edges unrelated to the topology's signal path.
SUPPLY_NETS = {"vdd!", "gnd!"}

# Variant names produced by prune_cmfb/prune_tail_current for slots that
# contribute no devices to the assembled circuit.
PRUNED_VARIANT_NAMES = {"cmfb_absent", "tail_current_absent"}


@dataclass
class VizNode:
    """One topology slot, rendered as a block-diagram node.

    :param id: Slot name (unique within a topology).
    :param label: Display name of the slot's chosen variant.
    :param category: Module category filling this slot.
    :param variant_name: Name of the chosen variant.
    :param is_pruned: ``True`` if the variant was pruned to an empty
                       placeholder (see :data:`PRUNED_VARIANT_NAMES`).
    """
    id: str
    label: str
    category: str
    variant_name: str
    is_pruned: bool


@dataclass
class VizEdge:
    """A connection between two slots' ports that share a net.

    :param source: Source slot name.
    :param target: Target slot name.
    :param source_port: Port name on *source*.
    :param target_port: Port name on *target*.
    :param net: Shared global net name.
    """
    source: str
    target: str
    source_port: str
    target_port: str
    net: str


@dataclass
class VizGraph:
    """A block diagram: one :class:`VizNode` per slot, one :class:`VizEdge`
    per pair of distinct slots sharing a net."""
    nodes: list[VizNode] = field(default_factory=list)
    edges: list[VizEdge] = field(default_factory=list)


def _build_edges(topology: TopologyTemplate) -> list[VizEdge]:
    """Group *topology*'s connections by net and emit one edge per pair of
    distinct slots sharing that net.

    A net can list more than one port on the same slot (e.g. an
    optional/alias port that mirrors another port's net). Such ports don't
    represent a separate inter-slot connection, so only the first connection
    per slot is kept per net before pairing -- this avoids both self-loops
    and duplicate edges between the same pair of slots.
    """
    nets: dict[str, list[Connection]] = {}
    for conn in topology.connections:
        if conn.net in SUPPLY_NETS:
            continue
        nets.setdefault(conn.net, []).append(conn)

    edges = []
    for net, conns in nets.items():
        by_slot: dict[str, Connection] = {}
        for conn in conns:
            by_slot.setdefault(conn.slot, conn)
        for a, b in itertools.combinations(by_slot.values(), 2):
            edges.append(VizEdge(a.slot, b.slot, a.port, b.port, net))
    return edges


def topology_to_graph(topology: TopologyTemplate, variant_map: dict[str, ModuleVariant]) -> VizGraph:
    """Build a :class:`VizGraph` for *topology* with *variant_map* assigned to its slots.

    Slots absent from *variant_map* (the ``bias_generation`` slot before
    :func:`~circuitgenome.synthesizer.synthesizer.build_circuit` has
    constructed its variant) are rendered as placeholder nodes.
    """
    nodes = []
    for slot in topology.slots:
        variant = variant_map.get(slot.name)
        nodes.append(
            VizNode(
                id=slot.name,
                label=variant.display_name if variant else f"({slot.category})",
                category=slot.category,
                variant_name=variant.name if variant else "",
                is_pruned=bool(variant) and variant.name in PRUNED_VARIANT_NAMES,
            )
        )
    return VizGraph(nodes=nodes, edges=_build_edges(topology))


def explain_incompatibility(topology: TopologyTemplate, variant_map: dict[str, ModuleVariant]) -> list[str]:
    """Return human-readable reasons why
    :func:`~circuitgenome.synthesizer.synthesizer.build_circuit` would return
    ``None`` for *topology* and *variant_map*. Returns ``[]`` if the
    combination is valid."""
    reasons = []
    if not is_combination_valid(variant_map):
        reasons.append(
            "Polarity mismatch between input_pair/load/tail_current (is_combination_valid)."
        )
    if not is_second_stage_compatible(topology, variant_map):
        reasons.append(
            "second_stage signal device needs a gate level outside the input "
            "pair's output window; the stage-interface DC level is unreachable "
            "(is_second_stage_compatible)."
        )
    if not is_output_type_compatible(topology, variant_map):
        reasons.append(
            "load.output_cardinality doesn't match topology.output_type (is_output_type_compatible)."
        )
    if not is_load_branch_compatible(topology, variant_map):
        reasons.append(
            "load leaves the untapped single-ended branch node high-impedance; "
            "no diode/resistor/cascode defines its DC voltage "
            "(is_load_branch_compatible)."
        )
    if not is_cmfb_compatible(variant_map):
        reasons.append(
            "cmfb variant requires a differential load (is_cmfb_compatible)."
        )
    if not is_tail_current_compatible(variant_map):
        reasons.append(
            "input_pair doesn't use a tail current; only the canonical tail_current "
            "variant is allowed (is_tail_current_compatible)."
        )
    return reasons
