"""DC operating-point reading and the bias-soundness verdict (SE and FD)."""
from __future__ import annotations

import re

from ..models import SizingResult, SizingSpec, TechParams
from .deck import (
    _MOS_MODELS,
    _dev_prefix,
    _dut,
    _inject_sizes,
    _parse_subckt,
    _run_capture,
    ngspice_available,
)
from .rig import _Topo, _iref_sink, _rig, _xline


def read_op_operating_point(
    netlist_text: str, result: SizingResult, tech: TechParams, spec: SizingSpec,
) -> dict[str, dict[str, float]] | None:
    """Return ``{ref: {'id','vds','vdsat'}}`` from a DC ``.op``.

    Single-ended: biases the sized circuit in unity feedback (``Lfb``/``Cfb``
    rig, polarity auto-detected via the settled output).  Fully differential
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
        if not (0.1 * vdd < float(mo.group(1)) < 0.9 * vdd):
            continue  # wrong polarity → output railed
        op = _parse_probes(prefixes, txt)
        if op:
            return op, None
    return None, ("railed" if ran else "sim-failed")


def _parse_probes(prefixes: dict[str, str], txt: str) -> dict[str, dict[str, float]]:
    """Collect ``{ref: {param: value}}`` from printed ``@m...[param]`` probes."""
    op: dict[str, dict[str, float]] = {}
    for r, pre in prefixes.items():
        for m in re.finditer(re.escape(pre) + r"\[(\w+)\]\s*=\s*([-\d.eE+]+)", txt):
            op.setdefault(r, {})[m.group(1)] = float(m.group(2))
    return op


#: Largest |V(outp) − V(outn)| (fraction of the supply) an FD ``.op`` may
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
    any marginality).  Conservative by design: returns ``(True, None)`` when
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
        return True, None   # output-state verdict above is the FD gate
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
