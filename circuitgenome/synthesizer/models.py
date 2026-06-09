from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Device:
    ref: str
    type: str  # nmos, pmos, resistor, capacitor
    terminals: dict[str, str]  # terminal_name → net_name (local to module)


@dataclass
class PortDef:
    name: str
    role: str  # input, output, supply, supply_in, optional


@dataclass
class ModuleVariant:
    name: str
    category: str
    display_name: str
    ports: list[PortDef]
    devices: list[Device]

    def port_names(self) -> set[str]:
        return {p.name for p in self.ports}


@dataclass
class Slot:
    name: str
    category: str


@dataclass
class Connection:
    slot: str
    port: str
    net: str  # global net name in the assembled circuit


@dataclass
class TopologyTemplate:
    name: str
    config: dict
    external_ports: list[str]
    slots: list[Slot]
    connections: list[Connection]

    def slot_connections(self, slot_name: str) -> dict[str, str]:
        """Returns {port: global_net} for a given slot."""
        return {c.port: c.net for c in self.connections if c.slot == slot_name}


@dataclass
class SynthesizedCircuit:
    name: str
    topology: str
    variant_map: dict[str, ModuleVariant]  # slot_name → variant
    external_ports: list[str]
    # Flat list of (global_ref, device) after net substitution
    devices: list[tuple[str, Device]] = field(default_factory=list)
