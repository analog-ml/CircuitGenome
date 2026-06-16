"""
Layer 0 — netlist parsing.

Parses :func:`circuitgenome.synthesizer.netlist.to_flat_spice` output into a
:class:`~circuitgenome.recognizer.models.ParsedNetlist`. This is the
structural inverse of ``to_flat_spice``'s ``_device_line``:

.. code-block:: text

   {ref} {d} {g} {s} {b} {nmos|pmos}    # MOSFET   (6 tokens)
   {ref} {t1} {t2} 1k                  # resistor (4 tokens)
   {ref} {p} {m} 1p                    # capacitor (4 tokens)

wrapped in a ``.subckt <name> <port...>`` / ``.ends`` block. Net and ref
names are treated as arbitrary strings -- the parser makes no assumptions
about ``to_flat_spice``'s own naming conventions (e.g. ``net_bias{N}`` or
``{ref}_{slot}``).

Device lines are dispatched by token count: 6 tokens is a MOSFET (trailing
``nmos``/``pmos`` model name), 4 tokens is a resistor (trailing ``1k``) or
capacitor (trailing ``1p``).
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from .models import ParsedNetlist

_MOSFET_TYPES = {"nmos", "pmos"}
_PASSIVE_TYPES = {"1k": "resistor", "1p": "capacitor"}


def parse(spice: str) -> ParsedNetlist:
    """Parse a flat SPICE ``.subckt`` block into a :class:`ParsedNetlist`.

    :param spice: A SPICE netlist string as produced by
                   :func:`circuitgenome.synthesizer.netlist.to_flat_spice`:
                   a ``.subckt <name> <port...>`` header line, one device
                   line per device (MOSFET: ``{ref} {d} {g} {s} {b}
                   {nmos|pmos}``; resistor: ``{ref} {t1} {t2} 1k``;
                   capacitor: ``{ref} {p} {m} 1p``), and a trailing ``.ends``
                   line. Blank lines are ignored.
    :returns: A :class:`ParsedNetlist` with ``devices`` in the same order as
              the input, and ``internal_nets`` = every net referenced by a
              device terminal that is not in ``external_ports``.
    :raises ValueError: If a 6-token device line's trailing type token is not
                         ``"nmos"``/``"pmos"``, or a 4-token device line's
                         trailing value token is not ``"1k"``/``"1p"``, or a
                         device line has neither 4 nor 6 tokens.

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
            if len(tokens) == 6:
                dev_type = tokens[-1].lower()
                if dev_type not in _MOSFET_TYPES:
                    raise ValueError(f"Unsupported device type {dev_type!r} on line: {line!r}")
                d, g, s, b = tokens[1:5]
                terminals = {"d": d, "g": g, "s": s, "b": b}
            elif len(tokens) == 4:
                value = tokens[-1].lower()
                dev_type = _PASSIVE_TYPES.get(value)
                if dev_type is None:
                    raise ValueError(f"Unsupported device value {tokens[-1]!r} on line: {line!r}")
                if dev_type == "resistor":
                    terminals = {"t1": tokens[1], "t2": tokens[2]}
                else:
                    terminals = {"p": tokens[1], "m": tokens[2]}
            else:
                raise ValueError(f"Unsupported device line: {line!r}")
            devices.append(Device(ref=ref, type=dev_type, terminals=terminals))
            referenced_nets.update(terminals.values())

    internal_nets = referenced_nets - set(external_ports)
    return ParsedNetlist(
        name=name,
        external_ports=external_ports,
        devices=devices,
        internal_nets=internal_nets,
    )
