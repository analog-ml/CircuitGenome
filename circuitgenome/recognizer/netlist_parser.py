"""
Layer 0 — netlist parsing.

Parses :func:`circuitgenome.synthesizer.netlist.to_flat_spice` output into a
:class:`~circuitgenome.recognizer.models.ParsedNetlist`. This is the
structural inverse of ``to_flat_spice``'s ``_device_line``:

.. code-block:: text

   {ref} {d} {g} {s} {b} {nmos|pmos}    # MOSFET   (6 tokens)
   {ref} {t1} {t2} [1k]                 # resistor (3-4 tokens)
   {ref} {p} {m} [1p]                   # capacitor (3-4 tokens)

wrapped in a ``.subckt <name> <port...>`` / ``.ends`` block (or ``.suckt``
for netlists with that common typo). Net and ref names are treated as
arbitrary strings -- the parser makes no assumptions about ``to_flat_spice``'s
own naming conventions (e.g. ``net_bias{N}`` or ``{ref}_{slot}``).

Device lines are dispatched by the first character of the ref (SPICE
convention): ``m`` → MOSFET (6 tokens), ``c`` → capacitor (3-4 tokens),
``r`` → resistor (3-4 tokens). The value token (e.g. ``1k`` / ``1p``) is
optional and ignored when present.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from .models import ParsedNetlist

_MOSFET_TYPES = {"nmos", "pmos"}


def parse(spice: str) -> ParsedNetlist:
    """Parse a flat SPICE ``.subckt`` block into a :class:`ParsedNetlist`.

    :param spice: A SPICE netlist string, typically produced by
                   :func:`circuitgenome.synthesizer.netlist.to_flat_spice`
                   or an external SPICE tool. The header keyword may be
                   ``.subckt`` or ``.suckt`` (common typo). The value token
                   on capacitor/resistor lines (``1p`` / ``1k``) is optional.
    :returns: A :class:`ParsedNetlist` with ``devices`` in the same order as
              the input, and ``internal_nets`` = every net referenced by a
              device terminal that is not in ``external_ports``.
    :raises ValueError: If a MOSFET line's trailing type token is not
                         ``"nmos"``/``"pmos"``, or a device line has an
                         unexpected token count for its prefix type.

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
        if tokens[0] in (".subckt", ".suckt"):
            name = tokens[1]
            external_ports = tokens[2:]
        elif tokens[0] in (".ends", ".end"):
            continue
        else:
            ref = tokens[0]
            first = ref[0].lower()
            if first == "m":
                if len(tokens) != 6:
                    raise ValueError(f"Unsupported MOSFET line: {line!r}")
                dev_type = tokens[5].lower()
                if dev_type not in _MOSFET_TYPES:
                    raise ValueError(f"Unsupported device type {dev_type!r} on line: {line!r}")
                terminals = {"d": tokens[1], "g": tokens[2], "s": tokens[3], "b": tokens[4]}
            elif first == "c":
                if len(tokens) not in (3, 4):
                    raise ValueError(f"Unsupported capacitor line: {line!r}")
                terminals = {"p": tokens[1], "m": tokens[2]}
                dev_type = "capacitor"
            elif first == "r":
                if len(tokens) not in (3, 4):
                    raise ValueError(f"Unsupported resistor line: {line!r}")
                terminals = {"t1": tokens[1], "t2": tokens[2]}
                dev_type = "resistor"
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
