"""Feedback-biased operating-point reading and the DC bias-soundness verdict."""
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
    """Return ``{ref: {'id','vds','vdsat'}}`` from a feedback-biased ``.op``.

    Biases the sized circuit in unity feedback (``Lfb``/``Cfb`` rig, polarity
    auto-detected via the settled output), then reads each MOSFET's actual
    operating point through ``@m.xdut.<ref>[...]``.  Single-ended only; returns
    ``None`` when ngspice is unavailable, the topology is fully-differential, or
    the bias doesn't settle.
    """
    op, _fail = _read_op(netlist_text, result, tech, spec)
    return op


def _read_op(
    netlist_text: str, result: SizingResult, tech: TechParams, spec: SizingSpec,
) -> tuple[dict[str, dict[str, float]] | None, str | None]:
    """:func:`read_op_operating_point` plus the failure kind when it is ``None``.

    The failure kind separates "the simulation ran and the output **railed** at
    both polarities" (``"railed"``) from "ngspice never produced a usable run"
    (``"sim-failed"`` — crash, non-convergence, or nothing probed); it is
    ``None`` when an operating point is returned.
    """
    if not ngspice_available():
        return None, "sim-failed"
    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd:
        return None, "sim-failed"
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
        op: dict[str, dict[str, float]] = {}
        for r, pre in prefixes.items():
            for m in re.finditer(re.escape(pre) + r"\[(\w+)\]\s*=\s*([-\d.eE+]+)", txt):
                op.setdefault(r, {})[m.group(1)] = float(m.group(2))
        if op:
            return op, None
    return None, ("railed" if ran else "sim-failed")


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

    Runs the feedback-biased ``.op`` (:func:`read_op_operating_point`) — which
    converges reliably, unlike the open-loop AC rig — and condemns the bias only on
    positive evidence: the operating point **rails** (no usable mid-rail bias) or a
    device is **starved/triode**.  Conservative by design: returns ``(True, None)``
    when it cannot check (ngspice absent, or a fully-differential topology the SE
    ``.op`` rig doesn't support), so it only ever downgrades a feasible verdict.
    """
    if not ngspice_available():
        return True, None
    _, ports, _ = _parse_subckt(netlist_text)
    if _Topo(ports).fd:
        return True, None
    op, fail = _read_op(netlist_text, result, tech, spec)
    if op is None:
        if fail == "railed":
            return False, ("SPICE bias check: the feedback operating point railed — "
                           "the circuit does not establish a usable mid-rail bias "
                           "point.")
        return False, ("SPICE bias check: the .op simulation failed or did not "
                       "converge — no operating point to assess.")
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
