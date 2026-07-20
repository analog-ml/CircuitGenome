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


def constructed_bias_legs(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Discover the legs of a constructed multi-reference bias generator.

    Hook for the ``constructed_bias`` pattern (``config/opamp_patterns.yaml``).
    The synthesizer constructs its bias generator per combination
    (:func:`circuitgenome.synthesizer.bias_construction.construct_bias_generation`):
    an NMOS master reference on ``ibias`` (the base template's ``mref``),
    NMOS-referenced legs mirroring it, and -- when any rail needs a
    PMOS-referenced level -- a ``pref`` branch whose diode-connected PMOS
    gates further PMOS-referenced legs. This hook discovers all of them:

    - **Pass 1 (NMOS-referenced)**: every nmos with gate on ``mref``'s
      diode node (the ``ibias`` net) and source on ``mref``'s source (gnd)
      mirrors the master. What sits on its drain names the leg:

      - a plain diode-connected pmos (``d == g``, ``s == b``): a vdd-flavored
        leg *or* the ``pref`` branch's diode (structurally identical -- both
        are claimed; which is which doesn't matter for sizing); the
        pmos-diode node is remembered as a potential PMOS-side reference
        gate;
      - a *riding* pmos diode (``d == g`` on the drain, ``s`` on an internal
        node) whose source returns to its bulk supply through an unclaimed
        resistor: a **cascode_vdd leg** (diode + floor resistor,
        ``out = vdd - |V_GSP| - I*R``) -- all three devices are claimed;
      - an nmos **cascode** (source on the drain node) topped by a plain
        pmos diode: the **cascoded pref branch** (the cascode pins the
        mirror's Vds; its gate rides the wide-swing ``ncasc`` level, whose
        generator branch is claimed by pass 2 as an ordinary gnd-flavored
        leg) -- the cascode and diode are claimed, the diode node becomes a
        reference gate;
      - nothing: a bare mirror, claimed alone -- a current-sink leg into a
        consumer's own diode.
    - **Pass 2 (PMOS-referenced)**: every pmos gated on a pass-1 pmos-diode
      node with source on that diode's supply is a leg *only if* its drain
      hosts a diode-connected nmos (a gnd-flavored leg -- including the pref
      branch's ``ncasc`` generator -- a **cascode_gnd leg** when the diode
      rides an unclaimed resistor to gnd, or a current-source leg into a
      mirror tail's own reference diode) or a resistor returning to
      ``mref``'s gnd (a tunable resistor leg). The drain condition keeps
      consumer devices out: a second-stage PMOS gated by a vdd-flavored rail
      has neither on its drain.

    Returns ``None`` when neither pass finds constructed-only evidence (no
    PMOS-referenced leg and no cascode_vdd leg): the structure is then the
    exact shape :func:`diode_connected_mosfet_bias_legs` discovers, and that
    pattern (listed after this one) claims it instead -- keeping legacy
    single-flavor netlists recognized under their historical names.

    :param assignment: Must contain key ``"mref"`` -- the matched
                        diode-connected nmos master reference.
    :param pins: Unused; accepted for signature consistency with other
                  hooks.
    :param netlist: The full parsed netlist to search for legs in.
    :returns: A :class:`HookMatch` with every discovered leg's devices in
              ``extra_devices`` and its output net as ``legN_out`` in
              ``extra_pins`` (1-indexed by discovery order; rail indices are
              not a structural property of a leg), plus ``vdd`` from the
              first pmos diode found. ``None`` if no PMOS-referenced leg
              exists.
    """
    mref = assignment["mref"]
    ibias_net = mref.terminals["g"]
    gnd_net = mref.terminals["s"]

    claimed = {mref.ref}
    extra_devices: list[Device] = []
    extra_pins: dict[str, str] = {}
    leg_count = 0
    constructed_only = 0  # shapes the legacy N-only pattern cannot claim
    pnodes: list[tuple[str, str]] = []  # (diode node, its vdd supply)

    def _resistor_between(net_a: str, net_b: str) -> Device | None:
        return next(
            (
                r for r in netlist.devices
                if r.ref not in claimed
                and r.type == "resistor"
                and {r.terminals["t1"], r.terminals["t2"]} == {net_a, net_b}
            ),
            None,
        )

    # --- Pass 1: NMOS mirrors of the master reference ---
    for nmos_leg in netlist.devices:
        if nmos_leg.ref in claimed or nmos_leg.type != "nmos":
            continue
        if nmos_leg.terminals["g"] != ibias_net or nmos_leg.terminals["s"] != gnd_net:
            continue

        out_net = nmos_leg.terminals["d"]
        pmos_diode = next(
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
        leg_count += 1
        extra_pins[f"leg{leg_count}_out"] = out_net
        claimed.add(nmos_leg.ref)
        extra_devices.append(nmos_leg)
        if pmos_diode is not None:
            claimed.add(pmos_diode.ref)
            extra_devices.append(pmos_diode)
            extra_pins.setdefault("vdd", pmos_diode.terminals["s"])
            pnodes.append((out_net, pmos_diode.terminals["s"]))
            continue

        # cascode_vdd leg: a pmos diode *riding* the drain node (source on an
        # internal "mid" net) whose floor resistor returns to its bulk supply.
        riding_diode = next(
            (
                p for p in netlist.devices
                if p.ref not in claimed
                and p.type == "pmos"
                and p.terminals["d"] == out_net
                and p.terminals["g"] == out_net
                and p.terminals["s"] != p.terminals["b"]
            ),
            None,
        )
        if riding_diode is not None:
            floor_r = _resistor_between(
                riding_diode.terminals["s"], riding_diode.terminals["b"])
            if floor_r is not None:
                claimed.update({riding_diode.ref, floor_r.ref})
                extra_devices.extend([riding_diode, floor_r])
                extra_pins.setdefault("vdd", riding_diode.terminals["b"])
                constructed_only += 1
                continue

        # Cascoded pref branch: an nmos cascode stacked on the mirror's drain,
        # topped by a plain pmos diode (its gate rides the ncasc level, whose
        # generator branch pass 2 claims as an ordinary gnd-flavored leg).
        cascode = next(
            (
                c for c in netlist.devices
                if c.ref not in claimed
                and c.type == "nmos"
                and c.terminals["s"] == out_net
                and c.terminals["g"] != ibias_net
            ),
            None,
        )
        if cascode is not None:
            top_diode = next(
                (
                    p for p in netlist.devices
                    if p.ref not in claimed
                    and p.type == "pmos"
                    and p.terminals["d"] == cascode.terminals["d"]
                    and p.terminals["g"] == cascode.terminals["d"]
                    and p.terminals["s"] == p.terminals["b"]
                ),
                None,
            )
            if top_diode is not None:
                pnode = cascode.terminals["d"]
                claimed.update({cascode.ref, top_diode.ref})
                extra_devices.extend([cascode, top_diode])
                extra_pins[f"leg{leg_count}_out"] = pnode  # relabel: the diode node
                extra_pins.setdefault("vdd", top_diode.terminals["s"])
                pnodes.append((pnode, top_diode.terminals["s"]))
                constructed_only += 1

    # --- Pass 2: PMOS-referenced legs gated on a pass-1 diode node ---
    p_leg_count = 0
    for pnode, vdd_net in pnodes:
        for pmos_leg in netlist.devices:
            if pmos_leg.ref in claimed or pmos_leg.type != "pmos":
                continue
            if pmos_leg.terminals["g"] != pnode or pmos_leg.terminals["s"] != vdd_net:
                continue

            out_net = pmos_leg.terminals["d"]
            nmos_diode = next(
                (
                    n for n in netlist.devices
                    if n.ref not in claimed
                    and n.type == "nmos"
                    and n.terminals["d"] == out_net
                    and n.terminals["g"] == out_net
                ),
                None,
            )
            if nmos_diode is not None:
                leg_count += 1
                p_leg_count += 1
                extra_pins[f"leg{leg_count}_out"] = out_net
                extra_devices.append(pmos_leg)
                claimed.add(pmos_leg.ref)
                # A plain diode (s == b) is the leg's own gnd-referenced
                # output diode -- claim it too. A diode riding a floor
                # resistor to gnd is a cascode_gnd leg -- claim both. Any
                # other stacked diode (s on an internal node: a cascode
                # mirror tail's reference) belongs to its consumer; the
                # pmos is a bare current-source leg.
                if nmos_diode.terminals["s"] == nmos_diode.terminals["b"]:
                    extra_devices.append(nmos_diode)
                    claimed.add(nmos_diode.ref)
                else:
                    floor_r = _resistor_between(nmos_diode.terminals["s"], gnd_net)
                    if floor_r is not None:
                        extra_devices.extend([nmos_diode, floor_r])
                        claimed.update({nmos_diode.ref, floor_r.ref})
                continue

            resistor_leg = next(
                (
                    r for r in netlist.devices
                    if r.ref not in claimed
                    and r.type == "resistor"
                    and out_net in (r.terminals["t1"], r.terminals["t2"])
                    and gnd_net in (r.terminals["t1"], r.terminals["t2"])
                ),
                None,
            )
            if resistor_leg is not None:
                leg_count += 1
                p_leg_count += 1
                extra_pins[f"leg{leg_count}_out"] = out_net
                extra_devices.extend([pmos_leg, resistor_leg])
                claimed.update({pmos_leg.ref, resistor_leg.ref})

    if p_leg_count == 0 and constructed_only == 0:
        return None

    return HookMatch(extra_devices=extra_devices, extra_pins=extra_pins)


def diode_connected_mosfet_bias_legs(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Discover the output "legs" attached to a diode-connected bias reference.

    Hook for the ``diode_connected_mosfet_bias`` pattern
    (``config/opamp_patterns.yaml``). That pattern's base template
    matches only a single diode-connected nmos (template ref ``mref``: ``d
    == g``, tied to ``gnd`` via ``s``/``b``) -- the shared reference of the
    historical ``diode_connected_mosfet_bias`` variant, which the
    ``constructed_bias`` pattern's :func:`constructed_bias_legs` also shares
    (that hook rejects the purely-NMOS-referenced shape so this one can
    claim it under the historical name).

    This hook does the rest: it walks the netlist looking for "legs" --
    self-contained 2-device groups that mirror ``mref`` and deliver one
    output rail. A leg consists of:

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
    order -- which of the eight canonical bias rails (``out1``..``out8``) a
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
              extra_pins={})`` -- a constructed bias generator with no
              consumed rails collapses to exactly this bare ``mref``, with no
              legs to find. A spurious bare-diode-connected-nmos match elsewhere
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


def resistor_load_vdd_check(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Accept only if the load resistors' shared ``t1`` is the global ``vdd!``.

    Hook for the ``resistor_load_vdd`` pattern (``config/opamp_patterns.yaml``).
    The pattern's template is two resistors sharing one net -- without this
    check any resistor pair sharing a node matches, e.g. the resistive-sense
    CMFB's ``r1_cmfb``/``r2_cmfb`` (shared internal sense node), which then
    competes for the ``load`` slot (issue #160).

    :param assignment: Must contain key ``"r1"`` -- one of the two resistors
                       (the pattern's ``same_net`` ties ``r2.t1`` to it).
    :param pins: Unused; accepted for signature consistency with other hooks.
    :param netlist: Unused; accepted for signature consistency with other hooks.
    :returns: ``HookMatch(extra_devices=[], extra_pins={})`` if ``r1.t1 ==
              "vdd!"``, else ``None`` (reject).
    """
    if assignment["r1"].terminals["t1"] != "vdd!":
        return None
    return HookMatch(extra_devices=[], extra_pins={})


def resistor_load_gnd_check(
    assignment: dict[str, Device],
    pins: dict[str, str],
    netlist: ParsedNetlist,
) -> HookMatch | None:
    """Accept only if the load resistors' shared ``t2`` is the global ``gnd!``.

    Mirror of :func:`resistor_load_vdd_check` for the ``resistor_load_gnd``
    pattern.

    :param assignment: Must contain key ``"r1"`` -- one of the two resistors.
    :param pins: Unused; accepted for signature consistency with other hooks.
    :param netlist: Unused; accepted for signature consistency with other hooks.
    :returns: ``HookMatch(extra_devices=[], extra_pins={})`` if ``r1.t2 ==
              "gnd!"``, else ``None`` (reject).
    """
    if assignment["r1"].terminals["t2"] != "gnd!":
        return None
    return HookMatch(extra_devices=[], extra_pins={})
