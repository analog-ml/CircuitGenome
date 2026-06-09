from __future__ import annotations
from .models import Device, SynthesizedCircuit


def _device_line(ref: str, dev: Device) -> str:
    t = dev.type.lower()
    if t in ("nmos", "pmos"):
        d = dev.terminals.get("d", "?")
        g = dev.terminals.get("g", "?")
        s = dev.terminals.get("s", "?")
        b = dev.terminals.get("b", "?")
        return f"{ref} {d} {g} {s} {b} {t}"
    if t == "resistor":
        t1 = dev.terminals.get("t1", "?")
        t2 = dev.terminals.get("t2", "?")
        return f"{ref} {t1} {t2} 1k"
    if t == "capacitor":
        p = dev.terminals.get("p", "?")
        m = dev.terminals.get("m", "?")
        return f"{ref} {p} {m} 1p"
    # Fallback
    terms = " ".join(dev.terminals.values())
    return f"{ref} {terms} {t}"


def to_flat_spice(circuit: SynthesizedCircuit, name: str | None = None) -> str:
    subckt_name = name or circuit.name
    ports = " ".join(circuit.external_ports)
    lines = [f".subckt {subckt_name} {ports}"]
    for ref, dev in circuit.devices:
        lines.append(_device_line(ref, dev))
    lines.append(".ends")
    return "\n".join(lines)


def to_hierarchical_spice(circuit: SynthesizedCircuit, name: str | None = None) -> str:
    """
    Emit one .subckt definition per module variant used, then a top-level
    .subckt that instantiates them with X calls.
    """
    lines: list[str] = []
    seen_variants: set[str] = set()

    # Collect module subcircuit definitions
    for slot_name, variant in circuit.variant_map.items():
        if variant.name in seen_variants:
            continue
        seen_variants.add(variant.name)

        port_names = [p.name for p in variant.ports if p.role != "optional"]
        ports_str = " ".join(port_names)
        lines.append(f".subckt {variant.name} {ports_str}")
        for dev in variant.devices:
            # Terminals here are still local names
            lines.append(_device_line(dev.ref, dev))
        lines.append(".ends")
        lines.append("")

    # Top-level subcircuit
    top_name = name or circuit.name
    ext_ports = " ".join(circuit.external_ports)
    lines.append(f".subckt {top_name} {ext_ports}")

    # Build per-slot port→net maps from the flat device list to reconstruct
    # the X-instance calls. We re-derive the port→net mapping from the topology
    # slot connections stored implicitly in the SynthesizedCircuit.
    # Since we don't re-store the raw connection map in SynthesizedCircuit,
    # we recover it by inspecting the first device of each module variant.
    # Simpler: just instantiate each module with a comment about its ports.
    for slot_name, variant in circuit.variant_map.items():
        non_optional_ports = [p.name for p in variant.ports if p.role != "optional"]
        # Gather the resolved nets for this slot from the flat device list.
        # We map local terminal values back: find devices prefixed with slot_name_
        slot_prefix = f"{slot_name}_"
        local_to_global: dict[str, str] = {}
        for ref, dev in circuit.devices:
            if ref.startswith(slot_prefix):
                for term, gnet in dev.terminals.items():
                    # Reverse-map: terminal → global net. We need port→global net.
                    # Terminals == local port names for devices whose terminals ARE ports.
                    pass

        # Reconstruct the port→global_net mapping via a simpler approach:
        # Re-run _build_port_net_map logic by scanning devices for known patterns.
        # Since this is complex to recover fully, emit an X-call with a comment.
        nets_for_slot = _recover_port_nets(slot_name, variant, circuit)
        net_vals = [nets_for_slot.get(p, f"<{p}>") for p in non_optional_ports]
        net_str = " ".join(net_vals)
        lines.append(f"X{slot_name} {net_str} {variant.name}")

    lines.append(".ends")
    return "\n".join(lines)


def _recover_port_nets(
    slot_name: str,
    variant,
    circuit: SynthesizedCircuit,
) -> dict[str, str]:
    """
    Recover the port→global_net mapping for a slot by scanning the flat
    device list and reversing the terminal substitution applied during synthesis.
    """
    port_names = {p.name for p in variant.ports}
    slot_prefix = f"{slot_name}_"
    port_to_global: dict[str, str] = {}

    for ref, dev in circuit.devices:
        if not ref.startswith(slot_prefix):
            continue
        # Find the original device in the variant
        local_ref = ref[len(slot_prefix):]
        orig_dev = next((d for d in variant.devices if d.ref == local_ref), None)
        if orig_dev is None:
            continue
        for term, local_net in orig_dev.terminals.items():
            if local_net in port_names:
                global_net = dev.terminals.get(term)
                if global_net:
                    port_to_global[local_net] = global_net

    # Supply ports default
    for p in variant.ports:
        if p.name not in port_to_global:
            if p.name == "vdd":
                port_to_global["vdd"] = "vdd!"
            elif p.name == "gnd":
                port_to_global["gnd"] = "gnd!"

    return port_to_global
