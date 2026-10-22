"""Quiescent node levels a solved sizing implies (issue #273).

The gm/Id table characterises every device at one bias point, ``Vds = Vdd/2``
and ``Vsb = 0``.  A cascode stack is far from it: the device under a cascode
is held ~0.1 V above its saturation knee, where its ``gds`` is several times
the table's, and the cascode's source rides well above its bulk, which adds a
body transconductance to its boost.  Reading each device at its own bias needs
the DC level of its nets, and :func:`estimate_node_levels` supplies one
without a circuit solve.

It starts from a few pinned nets -- the rails, the input common mode, the
quiescent output -- and propagates through two facts every sized device and
resistor states:

* a MOSFET carries its planned current at its planned ``|Vgs|`` (raised by
  body effect when its source sits off its bulk), so a known gate fixes its
  source (a cascode's source, the pair's tail node, a follower's output) and a
  known source fixes its gate (a diode node, a mirror gate, a common-source
  stage's input pin);
* a resistor drops ``I·R``, with ``I`` the MOSFET current into one of its ends
  when that end has no other resistor (a bias leg's floor resistor, a
  degeneration resistor).

The bias generator builds each cascode rail from a replica of its consumer
(``synthesizer/config/bias_legs.yaml``), so walking it reproduces the stack
floor the sizer planned -- the same ``|Vgs|`` and body shift on both sides
cancel.  Nets nothing reaches stay out of the map; a device on one reads at
the table's own bias, as before.
"""
from __future__ import annotations

from circuitgenome.synthesizer.models import Device

from ..models import TransistorSizing
from .device_model import DeviceModel
from .taxonomy import RAILS

#: Fixed-point passes for a source set by its own body-shifted ``|Vgs|``; the
#: shift grows far slower than ``Vsb``, so a few passes settle it.
_BODY_PASSES = 4


def _vgs_mag(dev: Device, s: TransistorSizing, model: DeviceModel,
             v_src: float | None, levels: dict[str, float]) -> float:
    """Planned ``|Vgs|`` plus the body shift at source level ``v_src``."""
    bulk = dev.terminals.get("b")
    vb = levels.get(bulk)
    if v_src is None or vb is None or bulk == dev.terminals.get("s"):
        return abs(s.vgs_v)
    return abs(s.vgs_v) + model.body_vth_shift(dev.type, abs(v_src - vb))


def _current_into(net: str, mosfets: list[Device],
                  sizing: dict[str, TransistorSizing]) -> float:
    """Net DC current (A) the MOSFETs on ``net`` draw out of it.

    Drain current flows drain→source in an NMOS and source→drain in a PMOS.
    """
    i = 0.0
    for d in mosfets:
        s = sizing.get(d.ref)
        if s is None:
            continue
        sign = 1.0 if d.type == "nmos" else -1.0
        if d.terminals.get("d") == net:
            i += sign * abs(s.ids_a)
        if d.terminals.get("s") == net:
            i -= sign * abs(s.ids_a)
    return i


def estimate_node_levels(
    mosfets: list[Device],
    resistors: list[Device],
    resistor_ohms: dict[str, float],
    sizing: dict[str, TransistorSizing],
    model: DeviceModel,
    pinned: dict[str, float],
) -> dict[str, float]:
    """``{net: V}`` for every net the pinned levels reach (see module docstring).

    :param mosfets: every MOSFET of the circuit.
    :param resistors: every resistor; only those in ``resistor_ohms`` drop.
    :param resistor_ohms: sized resistor values by ref.
    :param sizing: the solved sizing -- each device's ``ids_a`` and ``vgs_v``.
    :param model: supplies the body-effect threshold shift.
    :param pinned: nets whose level is set from outside the device graph
        (the rails at least).  Their values are never overwritten.

    Nets are filled breadth-first from ``pinned``; when two devices reach a
    net in the same pass the first in ``mosfets`` order sets it.
    """
    levels = dict(pinned)
    sized_r = [r for r in resistors if resistor_ohms.get(r.ref)]
    r_count: dict[str, int] = {}
    for r in sized_r:
        for t in ("t1", "t2"):
            r_count[r.terminals.get(t)] = r_count.get(r.terminals.get(t), 0) + 1
    for _ in range(len(mosfets) + len(sized_r)):
        new: dict[str, float] = {}

        def put(net: str | None, v: float) -> None:
            if net and net not in levels and net not in new:
                new[net] = v

        for d in mosfets:
            s = sizing.get(d.ref)
            if s is None or not s.vgs_v:
                continue
            sign = 1.0 if d.type == "nmos" else -1.0
            g, src = d.terminals.get("g"), d.terminals.get("s")
            if g in levels and src not in levels:
                vs = None
                for _p in range(_BODY_PASSES):
                    vs = levels[g] - sign * _vgs_mag(d, s, model, vs, levels)
                put(src, vs)
            elif src in levels and g not in levels:
                put(g, levels[src] + sign * _vgs_mag(d, s, model, levels[src], levels))
        for r in sized_r:
            a, b = r.terminals.get("t1"), r.terminals.get("t2")
            if (a in levels) == (b in levels):
                continue
            known, other = (a, b) if a in levels else (b, a)
            ohms = resistor_ohms[r.ref]
            if r_count.get(other) == 1:      # current read at the open end
                put(other, levels[known] - _current_into(other, mosfets, sizing) * ohms)
            elif r_count.get(known) == 1 and known not in RAILS:
                put(other, levels[known] + _current_into(known, mosfets, sizing) * ohms)
        if not new:
            break
        levels.update(new)
    return levels


def device_bias(dev: Device, levels: dict[str, float]) -> tuple[float | None, float | None]:
    """``(|Vds|, |Vsb|)`` in V from ``levels``, each ``None`` when unknown."""
    t = dev.terminals
    vd, vs, vb = levels.get(t.get("d")), levels.get(t.get("s")), levels.get(t.get("b"))
    vds = abs(vd - vs) if vd is not None and vs is not None else None
    vsb = abs(vs - vb) if vs is not None and vb is not None else None
    return vds, vsb
