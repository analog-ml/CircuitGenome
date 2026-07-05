"""
Core data models for the topology synthesizer.

All structures are plain dataclasses — they carry no logic and can be freely
inspected, serialized, or passed between pipeline stages.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Device:
    """A single primitive device (MOSFET, resistor, or capacitor).

    :param ref: Reference designator within the module (e.g. ``m1``, ``r2``).
    :param type: Device type: ``nmos``, ``pmos``, ``resistor``, or ``capacitor``.
    :param terminals: Maps terminal names to local net names.
                      MOSFETs use ``d/g/s/b``; resistors use ``t1/t2``;
                      capacitors use ``p/m``.
    """
    ref: str
    type: str
    terminals: dict[str, str]


@dataclass
class PortDef:
    """A port on a module variant.

    :param name: Port name (must match the canonical interface for its category).
    :param role: One of ``input``, ``output``, ``supply``, ``supply_in``,
                 or ``optional``.  Optional ports are skipped when not wired
                 in a topology.
    :param alias_of: If set, this port is electrically the same node as the
                      named port on the same variant (e.g. a non-cascode load's
                      ``out1`` aliases its ``in1``). Used to recover the global
                      net for ports that no device terminal references directly.
    """
    name: str
    role: str
    alias_of: str | None = None


@dataclass
class ModuleVariant:
    """One concrete implementation of a module category.

    Every variant in the same category exposes the same canonical port
    signature; only the internal devices differ.

    :param name: Unique snake_case identifier (e.g. ``differential_pair_pmos``).
    :param category: Module category (e.g. ``input_pair``, ``load``).
    :param display_name: Human-readable name shown in listings.
    :param ports: Ordered list of port definitions.
    :param devices: Ordered list of primitive devices.
    :param polarity: Electrical compatibility tag, either ``"pmos_input"``,
                     ``"nmos_input"``, or ``None``. Variants that share a
                     current-flow direction with a given ``input_pair``
                     polarity declare the matching tag; ``None`` means the
                     variant is compatible with either polarity. Used by
                     :func:`~circuitgenome.synthesizer.compatibility.is_combination_valid`
                     to filter out combinations with no DC current path.
    :param output_cardinality: ``load``-only tag, either ``"single"``,
                     ``"differential"``, or ``None``. Declares which topology
                     ``output_type`` the variant is structurally usable with:
                     for the cascode loads, which ``output_type`` defines a
                     net for the variant's mandatory output port(s) (``out``
                     for ``"single"``, ``out1``/``out2`` for
                     ``"differential"``); for ``current_source_load_*``,
                     ``"differential"`` because their ``bias_cmfb``-gated
                     branch devices need the CMFB loop that only
                     ``fully_differential`` topologies wire (issue #112).
                     ``None`` means the variant imposes no constraint and is
                     compatible with either output type. Used by
                     :func:`~circuitgenome.synthesizer.compatibility.output.is_output_type_compatible`.
    :param unsupported: ``None`` for enumerable variants. A non-``None``
                     reason string parks the variant: it stays loadable
                     (recognizer patterns and hand-built variant maps keep
                     working) but
                     :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`
                     skips it unless ``config={"include_unsupported": True}``.
    :param bias_infeasible: ``None`` for variants whose bias/headroom is
                     expected to close on typical (low-voltage) specs. A
                     non-``None`` reason string marks a variant whose wiring
                     is *functionally correct* but whose DC bias is
                     infeasible under normal supply/Vcm headroom (e.g. the
                     stacked-diode cascode tails, which need ``|Vgs|+Vdsat``
                     of tail compliance — issue #111). Unlike ``unsupported``
                     the circuit is fully buildable and would size into a
                     complete netlist; it is simply predicted to fail the DC
                     bias gate (the ``"bias_infeasible"`` designer outcome)
                     on the default spec class.
                     :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`
                     skips it unless ``config={"include_infeasible": True}``,
                     which design-space exploration sets to keep these
                     correct-but-infeasible circuits as mutation seeds.
    """
    name: str
    category: str
    display_name: str
    ports: list[PortDef]
    devices: list[Device]
    polarity: str | None = None
    output_cardinality: str | None = None
    unsupported: str | None = None
    bias_infeasible: str | None = None

    def port_names(self) -> set[str]:
        """Return the set of all port names on this variant."""
        return {p.name for p in self.ports}


@dataclass
class BiasLegLibrary:
    """The typed leg templates that demand-driven bias construction assembles.

    Loaded from ``config/bias_legs.yaml`` by
    :func:`~circuitgenome.synthesizer.loader.load_bias_legs`; consumed by
    :func:`~circuitgenome.synthesizer.bias_construction.construct_bias_generation`.
    Device terminals use the template-local net names ``ibias``, ``pref``,
    ``out``, ``vdd``, ``gnd`` (see the YAML header for the contract).

    :param reference: The master reference devices (always emitted).
    :param pref_branch: Devices deriving the PMOS-side reference gate
                        (emitted only when an instantiated leg references
                        ``pref``).
    :param legs: Maps each rail *kind* (``gate_vdd``, ``gate_gnd``,
                 ``current_source``, ``current_sink``, ``tunable``) to its
                 leg's device templates.
    """
    reference: list[Device]
    pref_branch: list[Device]
    legs: dict[str, list[Device]]


@dataclass
class Slot:
    """A named placeholder for one module category in a topology template.

    :param name: Slot identifier used in connection rules (e.g. ``input_pair``).
    :param category: Module category that fills this slot.
    """
    name: str
    category: str


@dataclass
class Connection:
    """Wires one port of a module slot to a global net in the assembled circuit.

    :param slot: Slot name this connection applies to.
    :param port: Port name on the module in that slot.
    :param net: Global net name in the assembled circuit.
    """
    slot: str
    port: str
    net: str


@dataclass
class TopologyTemplate:
    """A wiring blueprint for a complete op-amp topology.

    Defines which module slots are required and how their ports connect to
    global nets and to each other.

    :param name: Unique identifier (e.g. ``two_stage_opamp_single_ended``).
    :param config: Metadata dict — ``stages`` (int) and ``output_type`` (str).
    :param external_ports: Ordered list of top-level subcircuit port names.
    :param slots: All module slots in this topology.
    :param connections: Complete set of port-to-net wiring rules.
    """
    name: str
    config: dict
    external_ports: list[str]
    slots: list[Slot]
    connections: list[Connection]

    def slot_connections(self, slot_name: str) -> dict[str, str]:
        """Return ``{port: global_net}`` for *slot_name*."""
        return {c.port: c.net for c in self.connections if c.slot == slot_name}


@dataclass
class SynthesizedCircuit:
    """A fully instantiated circuit produced by the synthesizer.

    :param name: Auto-generated name encoding the topology and variant combo.
    :param topology: Name of the :class:`TopologyTemplate` used.
    :param variant_map: Maps each slot name to the chosen :class:`ModuleVariant`.
    :param external_ports: Top-level subcircuit ports (inherited from the template).
    :param devices: Flat list of ``(global_ref, Device)`` pairs after net
                    substitution.  Internal nets are prefixed with the slot name
                    to avoid collisions.
    """
    name: str
    topology: str
    variant_map: dict[str, ModuleVariant]
    external_ports: list[str]
    devices: list[tuple[str, Device]] = field(default_factory=list)
