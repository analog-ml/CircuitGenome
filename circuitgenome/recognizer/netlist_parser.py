"""
Layer 0 — netlist parsing.

Parses :func:`circuitgenome.synthesizer.netlist.to_flat_spice` output — and,
now, *sized* SPICE netlists produced by real design flows or CircuitGenome's
own sizer — into a :class:`~circuitgenome.recognizer.models.ParsedNetlist`.
The unsized form is the structural inverse of ``to_flat_spice``'s
``_device_line``:

.. code-block:: text

   {ref} {d} {g} {s} {b} {nmos|pmos}    # MOSFET   (6 tokens)
   {ref} {t1} {t2} [1k]                 # resistor (3-4 tokens)
   {ref} {p} {m} [1p]                   # capacitor (3-4 tokens)

wrapped in a ``.subckt <name> <port...>`` / ``.ends`` block (or ``.suckt``
for netlists with that common typo). Net and ref names are treated as
arbitrary strings -- the parser makes no assumptions about ``to_flat_spice``'s
own naming conventions (e.g. ``net_bias{N}`` or ``{ref}_{slot}``).

**Sized dialect.** The parser also accepts the sized dialect that real flows
and the sizer emit:

- MOSFET lines may carry trailing ``key=value`` parameters —
  ``W=0.5u L=0.15u nf=1 m=2`` — with values kept as SI-suffixed SPICE strings.
  Any other ``key=value`` token is tolerated and preserved. They land in
  :attr:`~circuitgenome.synthesizer.models.Device.params` keyed as written.
- The type token may be a real process model name (e.g.
  ``sky130_fd_pr__nfet_01v8``) mapped to ``nmos``/``pmos`` through a
  configurable model-name table (see :data:`DEFAULT_MODEL_MAP`); the bare
  ``nmos``/``pmos`` tokens keep working. Because real flows instantiate those
  models as subcircuits, a MOSFET line may be ``x``-prefixed instead of
  ``m``-prefixed — both are accepted when the model token resolves to a MOSFET.
- Resistor/capacitor value tokens (``1k`` / ``1p``) are preserved on
  :attr:`~circuitgenome.synthesizer.models.Device.params` under ``value``
  rather than ignored.

Sizes ride along on the parsed devices; recognition still matches on topology
(device type + terminal connectivity), so ``recognize()`` behaves identically
on sized and unsized netlists.

Device lines are dispatched by the first character of the ref (SPICE
convention): ``m`` → MOSFET, ``c`` → capacitor, ``r`` → resistor, ``x`` →
subcircuit instance (accepted only when its model token names a MOSFET in the
model map — the real-flow SKY130 device shape).
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from .models import ParsedNetlist

#: Default model-name → device-type table. Maps the bare synthesizer tokens,
#: the CircuitGenome ``device_map`` targets (gf180mcu core), and the common
#: SKY130 process primitives onto ``"nmos"``/``"pmos"``. Lookups are
#: case-insensitive. Callers pass a ``model_map`` to :func:`parse` to extend or
#: override this table (their entries win).
DEFAULT_MODEL_MAP: dict[str, str] = {
    # bare synthesizer tokens (backward compatible)
    "nmos": "nmos",
    "pmos": "pmos",
    # gf180mcu core subcircuits (CircuitGenome sizer's device_map targets)
    "nmos_3p3": "nmos",
    "pmos_3p3": "pmos",
    # SKY130 open-PDK primitives
    "sky130_fd_pr__nfet_01v8": "nmos",
    "sky130_fd_pr__nfet_01v8_lvt": "nmos",
    "sky130_fd_pr__pfet_01v8": "pmos",
    "sky130_fd_pr__pfet_01v8_lvt": "pmos",
    "sky130_fd_pr__pfet_01v8_hvt": "pmos",
}


def _split_positional_params(tokens: list[str], line: str) -> tuple[list[str], dict[str, str]]:
    """Split a device line's trailing tokens into positionals and ``key=value`` params.

    Positional tokens (no ``=``) come first and are returned in order; every
    ``key=value`` token is parsed into the params dict keyed as written. A bare
    token appearing *after* a ``key=value`` token is malformed and raises.
    """
    positionals: list[str] = []
    params: dict[str, str] = {}
    for tok in tokens:
        if "=" in tok:
            key, _, val = tok.partition("=")
            params[key] = val
        elif params:
            raise ValueError(f"Unexpected token {tok!r} after parameters on line: {line!r}")
        else:
            positionals.append(tok)
    return positionals, params


def parse(spice: str, model_map: dict[str, str] | None = None) -> ParsedNetlist:
    """Parse a flat SPICE ``.subckt`` block into a :class:`ParsedNetlist`.

    Accepts both the unsized dialect emitted by
    :func:`~circuitgenome.synthesizer.netlist.to_flat_spice` and the *sized*
    dialect produced by real design flows / CircuitGenome's own sizer (see the
    module docstring): MOSFET lines with trailing ``W=``/``L=``/``nf=``/``m=``
    (and any other ``key=value``) params, real process model names, and
    preserved resistor/capacitor value tokens.

    :param spice: A SPICE netlist string. The header keyword may be ``.subckt``
                   or ``.suckt`` (common typo).
    :param model_map: Optional model-name → ``"nmos"``/``"pmos"`` table,
                       merged over :data:`DEFAULT_MODEL_MAP` (caller entries
                       win) and matched case-insensitively. Use it to teach the
                       parser process-specific model names beyond the built-in
                       SKY130/gf180 set.
    :returns: A :class:`ParsedNetlist` with ``devices`` in the same order as
              the input, and ``internal_nets`` = every net referenced by a
              device terminal that is not in ``external_ports``. Sizing
              parameters ride along on each
              :attr:`~circuitgenome.synthesizer.models.Device.params`.
    :raises ValueError: If a MOSFET line's model token is unknown, a device
                         line has an unexpected token count/shape, or a bare
                         token follows a ``key=value`` parameter.

    Example::

        from circuitgenome.recognizer import parse, recognize

        parsed = parse('''
        .subckt ota in1 in2 out vdd! gnd!
        m1 net1 in1 tail vdd! sky130_fd_pr__pfet_01v8 W=4u L=0.5u nf=2 m=1
        m2 net2 in2 tail vdd! sky130_fd_pr__pfet_01v8 W=4u L=0.5u nf=2 m=1
        .ends''')
        print(parsed.devices[0].type)             # "pmos"
        print(parsed.devices[0].params["W"])      # "4u"
        result = recognize(parsed)                 # topology match, sizes ride along
    """
    models = {k.lower(): v for k, v in DEFAULT_MODEL_MAP.items()}
    if model_map:
        models.update((k.lower(), v) for k, v in model_map.items())

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
            positionals, params = _split_positional_params(tokens[1:], line)
            if first in ("m", "x"):
                # MOSFET: four terminals (d g s b) then a model token.
                if len(positionals) != 5:
                    if first == "x":
                        raise ValueError(f"Unsupported subcircuit line: {line!r}")
                    raise ValueError(f"Unsupported MOSFET line: {line!r}")
                model = positionals[4].lower()
                if model not in models:
                    if first == "x":
                        # An x-instance whose model isn't a known MOSFET is a
                        # hierarchical subcircuit -- out of scope for the flat parser.
                        raise ValueError(f"Unsupported subcircuit line: {line!r}")
                    raise ValueError(
                        f"Unsupported device type/model {positionals[4]!r} on line: {line!r}")
                dev_type = models[model]
                terminals = {
                    "d": positionals[0], "g": positionals[1],
                    "s": positionals[2], "b": positionals[3],
                }
            elif first == "c":
                if len(positionals) not in (2, 3):
                    raise ValueError(f"Unsupported capacitor line: {line!r}")
                terminals = {"p": positionals[0], "m": positionals[1]}
                dev_type = "capacitor"
                if len(positionals) == 3:
                    params = {"value": positionals[2], **params}
            elif first == "r":
                if len(positionals) not in (2, 3):
                    raise ValueError(f"Unsupported resistor line: {line!r}")
                terminals = {"t1": positionals[0], "t2": positionals[1]}
                dev_type = "resistor"
                if len(positionals) == 3:
                    params = {"value": positionals[2], **params}
            else:
                raise ValueError(f"Unsupported device line: {line!r}")
            devices.append(Device(ref=ref, type=dev_type, terminals=terminals, params=params))
            referenced_nets.update(terminals.values())

    internal_nets = referenced_nets - set(external_ports)
    return ParsedNetlist(
        name=name,
        external_ports=external_ports,
        devices=devices,
        internal_nets=internal_nets,
    )
