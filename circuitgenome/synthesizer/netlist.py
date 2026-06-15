"""
SPICE netlist serializers.

Two output formats are supported:

- **Flat** (:func:`to_flat_spice`) — all devices inlined into a single
  ``.subckt`` block.  Maximally portable; every SPICE simulator can read it.
- **Hierarchical** (:func:`to_hierarchical_spice`) — one ``.subckt`` definition
  per module variant, instantiated via ``X`` calls in the top-level block.
  Easier to read and avoids repeating shared subcircuits across many circuits.
"""
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
    terms = " ".join(dev.terminals.values())
    return f"{ref} {terms} {t}"


def to_flat_spice(circuit: SynthesizedCircuit, name: str | None = None) -> str:
    """Serialize *circuit* as a flat SPICE subcircuit.

    All devices from every module slot are inlined into a single ``.subckt``
    block.  Device reference designators are suffixed with the slot name
    (e.g. ``m1_input_pair``) so the leading character still identifies the
    SPICE primitive type; internal nets are prefixed with the slot name
    (e.g. ``load_internal_0``).

    :param circuit: A :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`
                    returned by :func:`~circuitgenome.synthesizer.synthesizer.synthesize`
                    or :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.
    :param name: Override the subcircuit name.  Defaults to ``circuit.name``.
    :returns: A SPICE netlist string starting with ``.subckt`` and ending with
              ``.ends``.

    Example::

        from circuitgenome import synthesize
        from circuitgenome.synthesizer import to_flat_spice

        circuit = synthesize({"topology": "one_stage_opamp"})[0]
        print(to_flat_spice(circuit, name="my_ota"))
    """
    subckt_name = name or circuit.name
    ports = " ".join(circuit.external_ports)
    lines = [f".subckt {subckt_name} {ports}"]
    for ref, dev in circuit.devices:
        lines.append(_device_line(ref, dev))
    lines.append(".ends")
    return "\n".join(lines)


def to_hierarchical_spice(circuit: SynthesizedCircuit, name: str | None = None) -> str:
    """Serialize *circuit* as a hierarchical SPICE netlist.

    Emits one ``.subckt`` definition per distinct module variant used, followed
    by a top-level ``.subckt`` that instantiates them with ``X`` calls.
    Duplicate module variants (same name) are defined only once.

    :param circuit: A :class:`~circuitgenome.synthesizer.models.SynthesizedCircuit`.
    :param name: Override the top-level subcircuit name.
    :returns: A multi-block SPICE string.

    Example::

        from circuitgenome import synthesize
        from circuitgenome.synthesizer import to_hierarchical_spice

        circuit = synthesize({"topology": "one_stage_opamp"})[0]
        print(to_hierarchical_spice(circuit, name="my_ota_hier"))
    """
    lines: list[str] = []
    seen_variants: set[str] = set()

    for slot_name, variant in circuit.variant_map.items():
        if variant.name in seen_variants:
            continue
        seen_variants.add(variant.name)

        port_names = [p.name for p in variant.ports if p.role != "optional"]
        ports_str = " ".join(port_names)
        lines.append(f".subckt {variant.name} {ports_str}")
        for dev in variant.devices:
            lines.append(_device_line(dev.ref, dev))
        lines.append(".ends")
        lines.append("")

    top_name = name or circuit.name
    ext_ports = " ".join(circuit.external_ports)
    lines.append(f".subckt {top_name} {ext_ports}")

    for slot_name, variant in circuit.variant_map.items():
        non_optional_ports = [p.name for p in variant.ports if p.role != "optional"]
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
    slot_suffix = f"_{slot_name}"
    port_to_global: dict[str, str] = {}

    for ref, dev in circuit.devices:
        if not ref.endswith(slot_suffix):
            continue
        local_ref = ref[: -len(slot_suffix)]
        orig_dev = next((d for d in variant.devices if d.ref == local_ref), None)
        if orig_dev is None:
            continue
        for term, local_net in orig_dev.terminals.items():
            if local_net in port_names:
                global_net = dev.terminals.get(term)
                if global_net:
                    port_to_global[local_net] = global_net

    for p in variant.ports:
        if p.name not in port_to_global:
            if p.name == "vdd":
                port_to_global["vdd"] = "vdd!"
            elif p.name == "gnd":
                port_to_global["gnd"] = "gnd!"

    # Ports that alias another port (e.g. a non-cascode load's out1/out2
    # aliasing in1/in2) are never referenced directly by a device terminal,
    # so recover their net from the aliased port instead.
    for p in variant.ports:
        if p.name not in port_to_global and p.alias_of and p.alias_of in port_to_global:
            port_to_global[p.name] = port_to_global[p.alias_of]

    return port_to_global
