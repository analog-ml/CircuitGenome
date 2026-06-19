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
    (``config/opamp_patterns.yaml``). That pattern's base template
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
              ``legN_out``/``vdd`` entries in ``extra_pins``. If zero legs are
              found, still returns ``HookMatch(extra_devices=[],
              extra_pins={})`` -- a 0-rail
              :func:`~circuitgenome.synthesizer.bias_pruning.prune_bias_generation`
              result collapses to exactly this bare ``mref``, with no legs to
              find. A spurious bare-diode-connected-nmos match elsewhere
              (e.g. ``current_mirror_tail_nmos``'s ``m1``) is harmless: its
              ``ibias`` pin won't equal the real ``ibias`` net, so
              :func:`~circuitgenome.recognizer.functional_block_recognizer._connectivity_score`
              ranks it below the genuine bias-generation candidate.
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

    return HookMatch(extra_devices=extra_devices, extra_pins=extra_pins)


def magic_battery_bias_legs(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Discover the output "legs" attached to a magic-battery bias reference.

    Hook for the ``magic_battery_bias`` pattern
    (``config/opamp_patterns.yaml``). That pattern's base template
    matches only a single diode-connected pmos (template ref ``mref``: ``d
    == g``, tied to ``vdd`` via ``s``/``b``). This mirrors
    :func:`diode_connected_mosfet_bias_legs` with polarities flipped: a leg
    here is a ``(pmos, nmos)`` pair, where the pmos mirrors ``mref`` from the
    ``vdd`` rail and the nmos is diode-connected at the leg's output node.

    A leg consists of:

    - a **pmos** device whose gate ties to ``mref``'s diode-connected node
      (``mref.g`` == ``mref.d``, i.e. the ``ibias`` net) and whose
      source/bulk tie to ``mref.s`` (the ``vdd`` net) -- this mirrors
      ``mref``'s reference current; paired with
    - an **nmos** device that is itself diode-connected (``d == g``) at that
      pmos leg's drain, with ``s == b`` (its own reference net) -- this is
      the leg's output node.

    Each discovered leg's pmos+nmos pair is appended to
    :attr:`HookMatch.extra_devices`, and its output net is recorded as
    ``legN_out`` in :attr:`HookMatch.extra_pins` (1-indexed by discovery
    order). The first discovered leg's nmos-side reference net is also
    recorded as ``gnd``.

    :param assignment: Must contain key ``"mref"`` -- the matched
                        diode-connected pmos reference device.
    :param pins: Unused; accepted for signature consistency with other
                  hooks.
    :param netlist: The full parsed netlist to search for legs in.
    :returns: A :class:`HookMatch` with one ``(pmos, nmos)`` pair per
              discovered leg appended to ``extra_devices``, and
              ``legN_out``/``gnd`` entries in ``extra_pins``. Returns
              ``None`` (rejecting the match) if zero legs are found -- a bare
              diode-connected pmos with no legs is structurally identical to
              ``resistor_bias``'s ``mref`` in the same 0-rail case, and
              ``diode_connected_mosfet_bias`` already claims it then.
    """
    mref = assignment["mref"]
    ibias_net = mref.terminals["g"]
    vdd_net = mref.terminals["s"]

    claimed = {mref.ref}
    extra_devices: list[Device] = []
    extra_pins: dict[str, str] = {}
    leg_count = 0

    for pmos_leg in netlist.devices:
        if pmos_leg.ref in claimed or pmos_leg.type != "pmos":
            continue
        if pmos_leg.terminals["g"] != ibias_net or pmos_leg.terminals["s"] != vdd_net:
            continue

        out_net = pmos_leg.terminals["d"]
        nmos_leg = next(
            (
                n for n in netlist.devices
                if n.ref not in claimed
                and n.type == "nmos"
                and n.terminals["d"] == out_net
                and n.terminals["g"] == out_net
                and n.terminals["s"] == n.terminals["b"]
            ),
            None,
        )
        if nmos_leg is None:
            continue

        leg_count += 1
        extra_devices.extend([pmos_leg, nmos_leg])
        extra_pins[f"leg{leg_count}_out"] = out_net
        extra_pins.setdefault("gnd", nmos_leg.terminals["s"])
        claimed.add(pmos_leg.ref)
        claimed.add(nmos_leg.ref)

    if leg_count == 0:
        return None

    return HookMatch(extra_devices=extra_devices, extra_pins=extra_pins)


def resistor_bias_legs(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Discover the output "legs" attached to a resistor-bias reference.

    Hook for the ``resistor_bias`` pattern
    (``config/opamp_patterns.yaml``). Like
    :func:`magic_battery_bias_legs`, the base template matches only a single
    diode-connected pmos (``mref``: ``d == g``, tied to ``vdd`` via
    ``s``/``b``). Here, each leg is a ``(pmos, resistor)`` pair: a pmos
    mirrors ``mref`` from the ``vdd`` rail, and a resistor drops the leg's
    output node down to its ``gnd``-referenced terminal.

    A leg consists of:

    - a **pmos** device whose gate ties to ``mref``'s diode-connected node
      (``mref.g`` == ``mref.d``, i.e. the ``ibias`` net) and whose
      source/bulk tie to ``mref.s`` (the ``vdd`` net) -- this mirrors
      ``mref``'s reference current; paired with
    - a **resistor** device with one terminal at that pmos leg's drain --
      this is the leg's output node, and the resistor's other terminal is
      the leg's ``gnd`` reference.

    Each discovered leg's pmos+resistor pair is appended to
    :attr:`HookMatch.extra_devices`, and its output net is recorded as
    ``legN_out`` in :attr:`HookMatch.extra_pins` (1-indexed by discovery
    order). The first discovered leg's resistor-side reference net is also
    recorded as ``gnd``.

    :param assignment: Must contain key ``"mref"`` -- the matched
                        diode-connected pmos reference device.
    :param pins: Unused; accepted for signature consistency with other
                  hooks.
    :param netlist: The full parsed netlist to search for legs in.
    :returns: A :class:`HookMatch` with one ``(pmos, resistor)`` pair per
              discovered leg appended to ``extra_devices``, and
              ``legN_out``/``gnd`` entries in ``extra_pins``. Returns
              ``None`` (rejecting the match) if zero legs are found -- a bare
              diode-connected pmos with no legs is structurally identical to
              ``magic_battery_bias``'s ``mref`` in the same 0-rail case, and
              ``diode_connected_mosfet_bias`` already claims it then.
    """
    mref = assignment["mref"]
    ibias_net = mref.terminals["g"]
    vdd_net = mref.terminals["s"]

    claimed = {mref.ref}
    extra_devices: list[Device] = []
    extra_pins: dict[str, str] = {}
    leg_count = 0

    for pmos_leg in netlist.devices:
        if pmos_leg.ref in claimed or pmos_leg.type != "pmos":
            continue
        if pmos_leg.terminals["g"] != ibias_net or pmos_leg.terminals["s"] != vdd_net:
            continue

        out_net = pmos_leg.terminals["d"]
        resistor_leg = next(
            (
                r for r in netlist.devices
                if r.ref not in claimed
                and r.type == "resistor"
                and out_net in (r.terminals["t1"], r.terminals["t2"])
            ),
            None,
        )
        if resistor_leg is None:
            continue

        leg_count += 1
        extra_devices.extend([pmos_leg, resistor_leg])
        extra_pins[f"leg{leg_count}_out"] = out_net
        gnd_net = (
            resistor_leg.terminals["t2"]
            if resistor_leg.terminals["t1"] == out_net
            else resistor_leg.terminals["t1"]
        )
        extra_pins.setdefault("gnd", gnd_net)
        claimed.add(pmos_leg.ref)
        claimed.add(resistor_leg.ref)

    if leg_count == 0:
        return None

    return HookMatch(extra_devices=extra_devices, extra_pins=extra_pins)


def resistor_tail_vdd_check(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Accept only if the matched resistor's ``t1`` is the global ``vdd!`` rail.

    Hook for the ``resistor_tail_vdd`` pattern (``config/opamp_patterns.yaml``).
    That pattern's template is a single, unconstrained resistor -- without this
    check it would match *every* resistor in the netlist (e.g. a
    ``resistor_load_*``'s, a ``resistor_bias``'s, or a degenerated input pair's
    degeneration resistor), most of which have nothing to do with the tail
    current source.

    :param assignment: Must contain key ``"r1"`` -- the matched resistor.
    :param pins: Unused; accepted for signature consistency with other hooks.
    :param netlist: Unused; accepted for signature consistency with other hooks.
    :returns: ``HookMatch(extra_devices=[], extra_pins={})`` if ``r1.t1 ==
              "vdd!"``, else ``None`` (reject).
    """
    if assignment["r1"].terminals["t1"] != "vdd!":
        return None
    return HookMatch(extra_devices=[], extra_pins={})


def resistor_tail_gnd_check(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Accept only if the matched resistor's ``t2`` is the global ``gnd!`` rail.

    Mirror of :func:`resistor_tail_vdd_check` for the ``resistor_tail_gnd``
    pattern.

    :param assignment: Must contain key ``"r1"`` -- the matched resistor.
    :param pins: Unused; accepted for signature consistency with other hooks.
    :param netlist: Unused; accepted for signature consistency with other hooks.
    :returns: ``HookMatch(extra_devices=[], extra_pins={})`` if ``r1.t2 ==
              "gnd!"``, else ``None`` (reject).
    """
    if assignment["r1"].terminals["t2"] != "gnd!":
        return None
    return HookMatch(extra_devices=[], extra_pins={})
