"""
Extra-check hooks referenced by :attr:`~circuitgenome.recognizer.models.PatternDef.hook`.

A hook is a ``"module:function"`` path, resolved dynamically by
:func:`circuitgenome.recognizer.subcircuit_recognizer.recognize`. It is
called once per base-template match with ``(assignment, pins, netlist)``:

- ``assignment`` -- ``dict[str, Device]`` mapping each
  :class:`~circuitgenome.recognizer.models.PatternDevice` ref to the actual
  matched device.
- ``pins`` -- the base template's resolved
  :attr:`~circuitgenome.recognizer.models.RecognizedStructure.pins`, before
  any hook extension.
- ``netlist`` -- the full
  :class:`~circuitgenome.recognizer.models.ParsedNetlist` being recognized.

It returns either ``None`` (reject the match) or a
:class:`~circuitgenome.recognizer.models.HookMatch` (accept, optionally
appending devices/pins) -- see :class:`~circuitgenome.recognizer.models.HookMatch`
for the full contract.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from .models import HookMatch, ParsedNetlist


def diode_connected_mosfet_bias_legs(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Discover the output "legs" attached to a diode-connected bias reference.

    Hook for the ``diode_connected_mosfet_bias`` pattern
    (``config/subcircuit_patterns.yaml``). That pattern's base template
    matches only a single diode-connected nmos (template ref ``mref``: ``d
    == g``, tied to ``gnd`` via ``s``/``b``) -- the "shared reference" that
    every ``bias_generation`` variant has, regardless of how many of its
    seven output rails survived
    :func:`circuitgenome.synthesizer.bias_pruning.prune_bias_generation` for
    this particular combination.

    This hook does the rest: it walks the netlist looking for "legs" --
    self-contained 2-device groups that mirror ``mref`` and deliver one
    output rail, mirroring the shared-reference-plus-legs layout described
    in :mod:`circuitgenome.synthesizer.bias_pruning`. A leg consists of:

    - an **nmos** device whose gate ties to ``mref``'s diode-connected node
      (``mref.g`` == ``mref.d``, i.e. the ``ibias`` net) and whose
      source/bulk tie to ``mref.s`` (the ``gnd`` net) -- this mirrors
      ``mref``'s reference current; paired with
    - a **pmos** device that is itself diode-connected (``d == g``) at that
      nmos leg's drain, with ``s == b`` (its own supply net) -- this is the
      leg's output node.

    Each discovered leg's nmos+pmos pair is appended to
    :attr:`HookMatch.extra_devices`, and its output net is recorded as
    ``legN_out`` in :attr:`HookMatch.extra_pins` (1-indexed by discovery
    order -- which of the seven canonical bias rails (``out1``..``out7``) a
    leg corresponds to is not a structural property of the leg itself, and
    is left for FBR/topology context to determine if needed). The first
    discovered leg's supply net is also recorded as ``vdd``.

    :param assignment: Must contain key ``"mref"`` -- the matched
                        diode-connected nmos reference device.
    :param pins: Unused; accepted for signature consistency with other
                  hooks.
    :param netlist: The full parsed netlist to search for legs in.
    :returns: A :class:`HookMatch` with one ``(nmos, pmos)`` pair per
              discovered leg appended to ``extra_devices``, and
              ``legN_out``/``vdd`` entries in ``extra_pins``. Returns
              ``None`` (rejecting the match) if zero legs are found -- a
              bare diode-connected nmos with no legs isn't a bias generator,
              it's just the diode-connected half of some other 2-device
              structure (e.g. ``current_mirror_tail_nmos``'s ``m1``), which
              that pattern already claims.
    """
    mref = assignment["mref"]
    ibias_net = mref.terminals["g"]
    gnd_net = mref.terminals["s"]

    claimed = {mref.ref}
    extra_devices: list[Device] = []
    extra_pins: dict[str, str] = {}
    leg_count = 0

    for nmos_leg in netlist.devices:
        if nmos_leg.ref in claimed or nmos_leg.type != "nmos":
            continue
        if nmos_leg.terminals["g"] != ibias_net or nmos_leg.terminals["s"] != gnd_net:
            continue

        out_net = nmos_leg.terminals["d"]
        pmos_leg = next(
            (
                p for p in netlist.devices
                if p.ref not in claimed
                and p.type == "pmos"
                and p.terminals["d"] == out_net
                and p.terminals["g"] == out_net
                and p.terminals["s"] == p.terminals["b"]
            ),
            None,
        )
        if pmos_leg is None:
            continue

        leg_count += 1
        extra_devices.extend([nmos_leg, pmos_leg])
        extra_pins[f"leg{leg_count}_out"] = out_net
        extra_pins.setdefault("vdd", pmos_leg.terminals["s"])
        claimed.add(nmos_leg.ref)
        claimed.add(pmos_leg.ref)

    if leg_count == 0:
        return None

    return HookMatch(extra_devices=extra_devices, extra_pins=extra_pins)
