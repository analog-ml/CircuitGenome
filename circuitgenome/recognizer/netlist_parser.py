"""
Layer 0 — netlist parsing.

Parses :func:`circuitgenome.synthesizer.netlist.to_flat_spice` output into a
:class:`~circuitgenome.recognizer.models.ParsedNetlist`. This is the
structural inverse of ``to_flat_spice``'s ``_device_line`` for MOSFETs:

.. code-block:: text

   {ref} {d} {g} {s} {b} {nmos|pmos}

wrapped in a ``.subckt <name> <port...>`` / ``.ends`` block. Net and ref
names are treated as arbitrary strings -- the parser makes no assumptions
about ``to_flat_spice``'s own naming conventions (e.g. ``net_bias{N}`` or
``{ref}_{slot}``).

Resistor (``{ref} {t1} {t2} 1k``) and capacitor (``{ref} {p} {m} 1p``) device
lines are not yet supported and raise :class:`ValueError`; support is
deferred to a later slice.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from .models import ParsedNetlist

_MOSFET_TYPES = {"nmos", "pmos"}


def parse(spice: str) -> ParsedNetlist:
    """Parse a flat SPICE ``.subckt`` block into a :class:`ParsedNetlist`.

    :param spice: A SPICE netlist string as produced by
                   :func:`circuitgenome.synthesizer.netlist.to_flat_spice`:
                   a ``.subckt <name> <port...>`` header line, one MOSFET
                   device line per device (``{ref} {d} {g} {s} {b}
                   {nmos|pmos}``), and a trailing ``.ends`` line. Blank lines
                   are ignored.
    :returns: A :class:`ParsedNetlist` with ``devices`` in the same order as
              the input, and ``internal_nets`` = every net referenced by a
              device terminal that is not in ``external_ports``.
    :raises ValueError: If a device line's trailing type token is not
                         ``"nmos"`` or ``"pmos"`` (resistor/capacitor lines
                         are not yet supported).

    Example::

        from circuitgenome import synthesize
        from circuitgenome.synthesizer import to_flat_spice
        from circuitgenome.recognizer import parse

        circuit = synthesize({"topology": "one_stage_opamp"})[0]
        parsed = parse(to_flat_spice(circuit))
        print(parsed.external_ports)   # ["ibias", "in1", "in2", "out", "vdd!", "gnd!"]
        print(len(parsed.devices))
    """
    name = ""
    external_ports: list[str] = []
    devices: list[Device] = []
    referenced_nets: set[str] = set()

    for line in spice.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0] == ".subckt":
            name = tokens[1]
            external_ports = tokens[2:]
        elif tokens[0] == ".ends":
            continue
        else:
            ref = tokens[0]
            dev_type = tokens[-1].lower()
            if dev_type not in _MOSFET_TYPES:
                raise ValueError(f"Unsupported device type {dev_type!r} on line: {line!r}")
            d, g, s, b = tokens[1:5]
            terminals = {"d": d, "g": g, "s": s, "b": b}
            devices.append(Device(ref=ref, type=dev_type, terminals=terminals))
            referenced_nets.update(terminals.values())

    internal_nets = referenced_nets - set(external_ports)
    return ParsedNetlist(
        name=name,
        external_ports=external_ports,
        devices=devices,
        internal_nets=internal_nets,
    )
