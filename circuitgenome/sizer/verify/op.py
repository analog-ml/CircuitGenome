"""DC operating-point reading and the bias-soundness verdict (SE and FD)."""
from __future__ import annotations

import re

import numpy as np

from ..models import SizingResult, SizingSpec, TechParams
from .deck import (
    _MOS_MODELS,
    _dev_prefix,
    _dut,
    _inject_sizes,
    _parse_subckt,
    _run,
    _run_capture,
    ngspice_available,
)
from .rig import _Topo, _iref_sink, _rig, _xline


def read_op_operating_point(
    netlist_text: str, result: SizingResult, tech: TechParams, spec: SizingSpec,
) -> dict[str, dict[str, float]] | None:
    """Return ``{ref: {'id','vds','vdsat'}}`` from a DC ``.op``.

    Single-ended: biases the sized circuit in unity feedback (``Lfb``/``Cfb``
    rig) at both input polarities and keeps the one whose output settles
    closest to Vcm — the wrong polarity is positive feedback and can latch at
    an in-window level behind a source follower (issue #242).  Fully differential
    (issue #162): both inputs and ``vcm_ref`` sit at Vcm with no feedback loop
    — the CMFB / loads own the output CM, which is exactly the DC state the
    metric benches run at.  Either way each MOSFET's actual operating point is
    read through ``@m.xdut.<ref>[...]``; returns ``None`` when ngspice is
    unavailable or the bias doesn't settle.
    """
    op, _fail = _read_op(netlist_text, result, tech, spec)
    return op


def _read_op(
    netlist_text: str, result: SizingResult, tech: TechParams, spec: SizingSpec,
) -> tuple[dict[str, dict[str, float]] | None, str | None]:
    """:func:`read_op_operating_point` plus the failure kind when it is ``None``.

    The failure kind separates "the simulation ran and the output **railed**"
    (``"railed"`` — SE: at both feedback polarities; FD: an output at a rail
    or a split output CM) from "ngspice never produced a usable run"
    (``"sim-failed"`` — crash, non-convergence, or nothing probed); it is
    ``None`` when an operating point is returned.
    """
    if not ngspice_available():
        return None, "sim-failed"
    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd:
        return _read_op_fd(name, ports, body, topo, result, tech, spec)
    body_dut = _dut(tech, name, _inject_sizes(body, result))
    refs = list(result.transistors)
    if not refs:
        return None, "sim-failed"
    sink = _iref_sink(body)
    vdd, ibias = spec.vdd, spec.ibias
    vcm = (spec.vdd + spec.vss) / 2.0
    # Generic device type per ref (nmos/pmos) — the OP handle can depend on it.
    models = {tok[0]: tok[5].lower() for line in body
              if len(tok := line.split()) >= 6 and tok[5].lower() in _MOS_MODELS}
    prefixes = {r: _dev_prefix(tech, r, models.get(r, "nmos")) for r in refs}
    probe = "".join(
        f"print {pre}[id]\nprint {pre}[vds]\nprint {pre}[vdsat]\n"
        for pre in prefixes.values()
    )
    ran = False
    best: tuple[float, dict[str, dict[str, float]]] | None = None
    for inp, inn in (("in1", "in2"), ("in2", "in1")):
        netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
                  inp: "inp", inn: "inn", "out": "out"}
        fb = (f"Vcm cm 0 {vcm}\nLfb out inn 1e12\nCfb inn cm 1e3\n"
              f"Vid inp cm dc 0\n")
        deck = (body_dut.replace("__PORTS__", " ".join(ports))
                + _rig(vdd, ibias, sink=sink)
                + fb + _xline(name, ports, netmap) + "\n"
                + ".control\nop\nprint v(out)\n" + probe + ".endc\n.end\n")
        txt = _run_capture(deck)
        if txt is None:
            continue
        mo = re.search(r"v\(out\)\s*=\s*([-\d.eE+]+)", txt)
        if not mo:
            continue
        ran = True
        vout = float(mo.group(1))
        if not (0.1 * vdd < vout < 0.9 * vdd):
            continue  # wrong polarity → output railed
        op = _parse_probes(prefixes, txt)
        # Unity feedback holds v(out) − Vcm across the inputs: ~0 once the loop
        # closes, large when the wrong polarity latched — which a source
        # follower's Vgs shift can land inside the window (issue #242).
        if op and (best is None or abs(vout - vcm) < best[0]):
            best = (abs(vout - vcm), op)
    if best:
        return best[1], None
    return None, ("railed" if ran else "sim-failed")


def _parse_probes(prefixes: dict[str, str], txt: str) -> dict[str, dict[str, float]]:
    """Collect ``{ref: {param: value}}`` from printed ``@m...[param]`` probes."""
    op: dict[str, dict[str, float]] = {}
    for r, pre in prefixes.items():
        for m in re.finditer(re.escape(pre) + r"\[(\w+)\]\s*=\s*([-\d.eE+]+)", txt):
            op.setdefault(r, {})[m.group(1)] = float(m.group(2))
    return op


#: Largest ``|V(outp) − V(outn)|`` (fraction of the supply) an FD ``.op`` may
#: show at zero differential input before the verdict is "railed": a split
#: output CM is the signature of an unregulated output stage latching apart.
_FD_SPLIT_FRAC = 0.2


def _read_op_fd(name, ports, body, topo: _Topo, result: SizingResult,
                tech: TechParams, spec: SizingSpec):
    """FD ``.op`` in the metric benches' DC state (#162, rig per #165).

    Inputs and ``vcm_ref`` sit at Vcm with **no feedback ties** — post-#165
    the CMFB owns the output CM, and this is exactly the DC state the FD
    benches measure in (``_loop_fb``'s FD branch anchors the inputs the same
    way).  The old bench-tie network (``outp``→``inn``/``outn``→``inp``) is
    bistable against a real CM loop and converges to its degenerate all-off
    solution at tight headroom, falsely condemning healthy circuits.
    Verdict ``"railed"`` when either output leaves the ``0.1–0.9·Vdd``
    window or the outputs split apart at zero differential input; otherwise
    the per-device operating points feed the usual starved/triode checks.
    """
    body_dut = _dut(tech, name, _inject_sizes(body, result))
    refs = list(result.transistors)
    if not refs:
        return None, "sim-failed"
    vdd = spec.vdd
    vcm = (spec.vdd + spec.vss) / 2.0
    # Generic device type per ref (nmos/pmos) — the OP handle can depend on it.
    models = {tok[0]: tok[5].lower() for line in body
              if len(tok := line.split()) >= 6 and tok[5].lower() in _MOS_MODELS}
    prefixes = {r: _dev_prefix(tech, r, models.get(r, "nmos")) for r in refs}
    probe = "".join(
        f"print {pre}[id]\nprint {pre}[vds]\nprint {pre}[vdsat]\n"
        for pre in prefixes.values()
    )
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
              "in1": "inp", "in2": "inn", "outp": "outp", "outn": "outn"}
    fb = f"Vip inp 0 {vcm}\nVin inn 0 {vcm}\n"
    if topo.has_vcm:
        netmap["vcm_ref"] = "ocm"
        fb += f"Vocm ocm 0 {vcm}\n"
    deck = (body_dut.replace("__PORTS__", " ".join(ports))
            + _rig(vdd, spec.ibias, sink=_iref_sink(body))
            + fb + _xline(name, ports, netmap) + "\n"
            + ".control\nop\nprint v(outp)\nprint v(outn)\n"
            + probe + ".endc\n.end\n")
    txt = _run_capture(deck)
    if txt is None:
        return None, "sim-failed"
    vouts = [float(m.group(2)) for m in
             re.finditer(r"v\((outp|outn)\)\s*=\s*([-\d.eE+]+)", txt)]
    if len(vouts) != 2:
        return None, "sim-failed"
    if (not all(0.1 * vdd < v < 0.9 * vdd for v in vouts)
            or abs(vouts[0] - vouts[1]) > _FD_SPLIT_FRAC * vdd):
        return None, "railed"
    op = _parse_probes(prefixes, txt)
    return (op, None) if op else (None, "sim-failed")


#: Output excursion (fraction of Vdd) the settling check kicks ``outp`` by.
_KICK_FRAC = 0.05
#: Largest peak-to-peak (fraction of Vdd) the output CM or differential may
#: still show over the last quarter of the window: a settled amplifier is
#: flat there, an unstable one keeps ringing.
_SETTLED_FRAC = 0.01


def _fd_ringing(netlist_text: str, result: SizingResult, tech: TechParams,
                spec: SizingSpec) -> tuple[float, float] | None:
    """Residual ``(cm_p2p, dm_p2p)`` (V) an FD amplifier still shows after a kick.

    ``.op`` also converges on an *unstable* equilibrium, and the AC bench
    only sees the global differential loop, so two kinds of oscillator are
    otherwise invisible (issue #208): a CMFB loop that rings in common mode
    (27/28 sampled gf180 FD circuits with the old high-gain CMFB), and a
    *local* loop — e.g. an NMC inner Miller loop — ringing well above the
    measured GBW while the open-loop PM reads a healthy 88°.  In the ``.op``
    DC state (inputs and ``vcm_ref`` at Vcm) a current pulse into ``outp``
    alone moves it by ``_KICK_FRAC``·Vdd, exciting both modes; the residual
    is read over the last quarter of a window of ~40 GBW periods (the CM loop
    crosses near GBW).  ``None`` when the transient cannot run — including a
    timeout, which a rail-to-rail oscillator can cause by stalling ngspice
    (a sized RNMC FD with RHP poles took 374 s); ``None`` is no evidence, so
    such a circuit passes here and is caught by the AC bench's RHP check
    instead.  A time budget cannot separate the two: under heavy machine
    load a healthy design's transient took ~500 CPU-seconds too.
    """
    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    body_dut = _dut(tech, name, _inject_sizes(body, result))
    vdd, ibias = spec.vdd, spec.ibias
    vcm = (spec.vdd + spec.vss) / 2.0
    t_kick = _KICK_FRAC * vdd * spec.cl / ibias
    gbw = result.metrics.get("gbw_hz") or 1e6
    t_end = max(40.0 / gbw, 20.0 * t_kick)
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
              "in1": "inp", "in2": "inn", "outp": "outp", "outn": "outn"}
    fb = (f"Vip inp 0 {vcm}\nVin inn 0 {vcm}\n"
          f"Ik 0 outp pulse(0 {ibias} {t_end / 100} 1n 1n {t_kick} 1)\n")
    if topo.has_vcm:
        netmap["vcm_ref"] = "ocm"
        fb += f"Vocm ocm 0 {vcm}\n"
    deck = (body_dut.replace("__PORTS__", " ".join(ports))
            + _rig(vdd, ibias, sink=_iref_sink(body))
            + f"Cl1 outp 0 {spec.cl}\nCl2 outn 0 {spec.cl}\n"
            + fb + _xline(name, ports, netmap) + "\n"
            + f".control\ntran {t_end / 4000} {t_end}\n"
            + "wrdata __OUT__ v(outp) v(outn)\n.endc\n.end\n")
    a = _run(deck, ["v(outp)", "v(outn)"])
    if a is None or a.shape[0] < 20 or a.shape[1] < 4:
        return None
    late = a[:, 0] >= 0.75 * a[-1, 0]
    vp, vn = a[late, 1], a[late, 3]
    return float(np.ptp((vp + vn) / 2.0)), float(np.ptp(vp - vn))


def _op_bias_problems(op: dict[str, dict[str, float]]) -> tuple[list[str], list[str]]:
    """Return ``(triode_refs, starved_refs)`` from an operating-point dict.

    Starved: drain current below 0.1 µA (device effectively off). Triode:
    ``|Vds| < |Vdsat|`` (a current source/amplifier device pushed out of
    saturation).
    """
    triode, starved = [], []
    for ref, d in op.items():
        ida = abs(d.get("id", 0.0))
        if ida < 1e-7:
            starved.append(ref)
        elif "vds" in d and "vdsat" in d and abs(d["vds"]) < abs(d["vdsat"]) - 1e-3:
            triode.append(ref)
    return triode, starved


def check_bias_soundness(netlist_text: str, result: SizingResult,
                         tech: TechParams, spec: SizingSpec) -> tuple[bool, str | None]:
    """SPICE-grounded DC bias verdict: ``(sound, reason)``.

    Runs the DC ``.op`` (:func:`read_op_operating_point` — SE in unity
    feedback, FD with inputs/``vcm_ref`` at Vcm, issue #162) and condemns the
    bias only on positive evidence: the operating point **rails** (no usable
    mid-rail bias; for FD also a split output CM) or, single-ended, a device
    is **starved/triode**.  FD skips the per-device verdicts: with inputs at
    Vcm the CMFB amp's tail (its inputs also sit at Vcm) and the main tail
    run in *marginal* triode by design at low supplies — degraded, still
    functional, and universal to the family — so a device-level condemnation
    would reject every low-voltage CMFB variant that measurably amplifies at
    this very operating point; a dead FD circuit rails/splits its outputs
    instead, which the ``.op`` verdict already catches (the benches quantify
    any marginality).  An FD operating point must also be *stable*: an
    amplifier whose outputs keep ringing after a kick — in common mode (an
    unstable CMFB loop) or differentially (an unstable local loop the AC
    bench cannot see) — is condemned too (:func:`_fd_ringing`, issue #208).  Conservative by design: returns ``(True, None)`` when
    it cannot check (ngspice absent), so it only ever downgrades a feasible
    verdict.
    """
    if not ngspice_available():
        return True, None
    op, fail = _read_op(netlist_text, result, tech, spec)
    if op is None:
        if fail == "railed":
            return False, ("SPICE bias check: the .op operating point railed — "
                           "the circuit does not establish a usable mid-rail bias "
                           "point.")
        return False, ("SPICE bias check: the .op simulation failed or did not "
                       "converge — no operating point to assess.")
    _, ports, _ = _parse_subckt(netlist_text)
    if _Topo(ports).fd:
        # Output-state verdict above plus settling after a kick: the FD gate.
        ring = _fd_ringing(netlist_text, result, tech, spec)
        limit = _SETTLED_FRAC * spec.vdd
        if ring is not None and max(ring) > limit:
            cm, dm = ring
            which = ("the differential output oscillates — a local loop is "
                     "unstable" if dm > limit else
                     "the output common mode oscillates — the CMFB loop is "
                     "unstable")
            return False, (f"SPICE bias check: the outputs do not settle "
                           f"after a disturbance ({which}; residual CM "
                           f"{cm * 1e3:.0f} mV, differential {dm * 1e3:.0f} mV "
                           f"p-p).")
        return True, None
    triode, starved = _op_bias_problems(op)
    if starved or triode:
        parts = []
        if starved:
            parts.append(f"starved (<0.1µA): {', '.join(starved[:4])}"
                         + ("…" if len(starved) > 4 else ""))
        if triode:
            parts.append(f"in triode: {', '.join(triode[:4])}"
                         + ("…" if len(triode) > 4 else ""))
        return False, "SPICE bias check: " + "; ".join(parts) + " — bias not established."
    return True, None


def _bias_diagnostic(netlist_text: str, result: SizingResult,
                     tech: TechParams, spec: SizingSpec) -> str | None:
    """One-line summary of devices in triode / starved, when AC found no gain.

    Reuses the feedback-biased ``.op`` reader to explain *why* a circuit doesn't
    amplify (the usual cause: stacked devices don't fit the supply headroom).
    """
    try:
        op = read_op_operating_point(netlist_text, result, tech, spec)
    except Exception:
        op = None
    if not op:
        return None
    triode, starved = _op_bias_problems(op)
    parts = []
    if triode:
        parts.append(f"in triode: {', '.join(triode[:4])}"
                     + ("…" if len(triode) > 4 else ""))
    if starved:
        parts.append(f"starved (<0.1µA): {', '.join(starved[:4])}"
                     + ("…" if len(starved) > 4 else ""))
    if not parts:
        return None
    return "bias diagnostic — " + "; ".join(parts) + " (insufficient headroom?)"
