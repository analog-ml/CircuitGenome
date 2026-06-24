"""ngspice post-sizing verification of op-amp performance metrics.

Re-simulates a *sized* circuit (W/L from :class:`~.models.SizingResult`) in
ngspice using the **same technology** as initial sizing, to cross-check the
closed-form metrics from ``_evaluate_metrics``.  For the card-less ``generic``
tech a Level-1 model is synthesised from ``mu_cox``/``vth``/``lam`` (so SPICE ≈
the analytical Level-1 formulas); for PTM nodes the BSIM4 ``.pm`` card is
included (so the delta reflects the Level-1-vs-device gap).

This is **best-effort verification**, not sign-off: each metric is measured by an
independent testbench and any that fails to converge/parse returns ``None``
(printed as ``n/a``) instead of raising.

Public API:
    ``ngspice_available() -> bool``
    ``simulate_metrics(netlist_text, result, tech, spec) -> dict[str, float|None]``
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .models import SizingResult, SizingSpec, TechParams

_MOS_MODELS = ("nmos", "pmos")


def ngspice_available() -> bool:
    """True if the ``ngspice`` binary is on PATH."""
    return shutil.which("ngspice") is not None


# --------------------------------------------------------------------------- #
# Deck building blocks
# --------------------------------------------------------------------------- #
def _emit_model(tech: TechParams) -> str:
    """Return the SPICE ``.model``/``.include`` block for ``tech``."""
    if tech.spice_model:
        return f'.include "{tech.spice_model}"\n'
    n, p = tech.nmos, tech.pmos
    # Shichman-Hodges (level=1): KP=µCox, VTO=Vth, LAMBDA=λ.
    return (
        f".model nmos nmos level=1 kp={n.mu_cox:.6e} vto={n.vth:.4f} "
        f"lambda={n.lam:.4f} gamma=0 phi=0.7\n"
        f".model pmos pmos level=1 kp={p.mu_cox:.6e} vto={p.vth:.4f} "
        f"lambda={p.lam:.4f} gamma=0 phi=0.7\n"
    )


def _parse_subckt(netlist_text: str):
    """Return ``(name, ports, body_lines)`` for the single ``.subckt`` block."""
    lines = netlist_text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().lower().startswith(".subckt"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip().lower().startswith(".ends"))
    head = lines[start].split()
    name, ports = head[1], head[2:]
    body = [l for l in lines[start + 1:end] if l.strip()]
    return name, ports, body


def _inject_sizes(body: list[str], result: SizingResult) -> list[str]:
    """Set sized W/L (MOSFETs), Cc (comp caps) and R (sized load resistors)."""
    cc1 = result.cc_pf
    cc2 = result.cc2_pf if result.cc2_pf is not None else cc1
    out = []
    for line in body:
        tok = line.split()
        ref = tok[0]
        low = ref.lower()
        if len(tok) >= 6 and tok[5].lower() in _MOS_MODELS and ref in result.transistors:
            s = result.transistors[ref]
            out.append(f"{line.rstrip()} W={s.w_um:.5f}u L={s.l_um:.5f}u")
        elif ref in result.resistors and len(tok) >= 4:
            # sized load resistor: replace the placeholder value with R (ohms)
            out.append(f"{tok[0]} {tok[1]} {tok[2]} {result.resistors[ref]:.4f}")
        elif low.startswith("c") and "comp" in low and len(tok) >= 4:
            val = cc2 if "comp2" in low else cc1
            if val:
                out.append(f"{tok[0]} {tok[1]} {tok[2]} {val:.4f}p")
            else:
                out.append(line.rstrip())
        else:
            out.append(line.rstrip())
    return out


def _dut(tech: TechParams, name: str, body: list[str]) -> str:
    """Title line + model block + the sized ``.subckt`` definition.

    The leading comment is mandatory: SPICE always treats the first deck line as
    the title and ignores it, which would otherwise swallow the first ``.model``.
    """
    return ("* circuitgenome spice verification\n"
            + _emit_model(tech) + f".subckt {name} __PORTS__\n"
            + "\n".join(body) + "\n.ends\n")


def _run(deck: str, vectors: list[str]) -> np.ndarray | None:
    """Run ``deck`` in ngspice -b; return the wrdata table (or None on failure).

    ``deck`` must contain ``__OUT__`` where the wrdata filename goes and a
    ``.control`` block ending in ``wrdata __OUT__ <vectors>``.
    """
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.dat"
        sp = Path(d) / "deck.sp"
        sp.write_text(deck.replace("__OUT__", str(out)))
        try:
            subprocess.run(["ngspice", "-b", str(sp)], capture_output=True,
                           text=True, timeout=60)
        except Exception:
            return None
        if not out.exists():
            return None
        try:
            data = np.loadtxt(out)
        except Exception:
            return None
        return np.atleast_2d(data)


# --------------------------------------------------------------------------- #
# Port classification + shared rig
# --------------------------------------------------------------------------- #
class _Topo:
    def __init__(self, ports: list[str]):
        self.ports = ports
        self.fd = "outp" in ports and "outn" in ports
        self.out = ["outp", "outn"] if self.fd else ["out"]
        self.has_vcm = "vcm_ref" in ports


def _xline(name: str, ports: list[str], netmap: dict) -> str:
    """X-instance wiring DUT ports to testbench nets via ``netmap``."""
    nodes = " ".join(netmap[p] for p in ports)
    return f"Xdut {nodes} {name}"


def _rig(vdd: float, ibias: float) -> str:
    return (
        f"Vsup vdd 0 {vdd}\n"
        f"Iref 0 ibias dc {ibias}\n"     # inject bias current into the ibias node
    )


# --------------------------------------------------------------------------- #
# Metric testbenches
# --------------------------------------------------------------------------- #
def _measure_power(name, ports, body_dut, topo, vdd, ibias, vcm):
    """DC operating point → quiescent power."""
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
              "in1": "cm", "in2": "cm"}
    for o in topo.out:
        netmap[o] = o
    if topo.has_vcm:
        netmap["vcm_ref"] = "cm"
    deck = (
        body_dut.replace("__PORTS__", " ".join(ports))
        + _rig(vdd, ibias)
        + f"Vcm cm 0 {vcm}\n"
        + _xline(name, ports, netmap) + "\n"
        + ".control\nop\nwrdata __OUT__ i(Vsup)\n.endc\n.end\n"
    )
    data = _run(deck, ["i(Vsup)"])
    if data is None:
        return None
    i = abs(float(data.flatten()[-1]))
    return vdd * i


def _measure_ac(name, ports, body_dut, topo, vdd, ibias, vcm):
    """Open-loop AC: returns ``(gain_db, gbw_hz, pm_deg, reason)``.

    AC-coupled feedback: huge L closes the loop at DC (sets bias ≈ CM), huge C
    AC-grounds the inverting input; a 1 V AC source drives the non-inverting
    input.  The (in1,in2)->(non-inv,inv) assignment is auto-detected via the DC
    operating point (output must settle near CM, not a rail).

    The **measured** low-frequency gain is reported even when it is ≤ 0 dB (a
    mis-biased circuit that does not amplify) — ``gbw``/``pm`` are then ``None``
    (no 0-dB crossing) and ``reason`` explains why.  ``reason`` is ``None`` on a
    normal (positive-gain) measurement.
    """
    # Feedback L must be AC-OPEN even at the lowest sweep frequency (1 Hz):
    # ωL = 2π·1·1e12 ≈ 6e12 Ω. C grounds the inverting input at AC.
    Lh, Ch = "1e12", "1e3"
    settled = False
    best: tuple[float, float | None, float | None] | None = None
    for inp, inn in (("in1", "in2"), ("in2", "in1")):
        netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
                  inp: "inp", inn: "inn"}
        if topo.fd:
            netmap["outp"], netmap["outn"] = "outp", "outn"
            netmap["vcm_ref"] = "ocm"
            # DC feedback outp->inn, outn->inp (huge L = AC-open) sets the input
            # CM ≈ output CM; the floating Vid injects the differential AC.  No
            # AC-grounding caps here — they would short out Vid.
            fb = (f"Vocm ocm 0 {vcm}\n"
                  f"L1 outp inn {Lh}\nL2 outn inp {Lh}\n"
                  f"Vid inp inn ac 1\n")
            outexpr = "v(outp)-v(outn)"
            dccheck = "v(outp)-v(outn)"
        else:
            netmap["out"] = "out"
            fb = (f"Vcm cm 0 {vcm}\n"
                  f"Lfb out inn {Lh}\nCfb inn cm {Ch}\n"
                  f"Vid inp cm ac 1\n")
            outexpr = "v(out)"
            dccheck = "v(out)"
        # DC check: output must settle near CM (negative feedback), else swap.
        dc = (body_dut.replace("__PORTS__", " ".join(ports)) + _rig(vdd, ibias)
              + fb + _xline(name, ports, netmap) + "\n"
              + f".control\nop\nlet vchk={dccheck}\nwrdata __OUT__ vchk\n.endc\n.end\n")
        d = _run(dc, ["vchk"])
        if d is None:
            continue
        vchk = float(d.flatten()[-1])
        ok = (abs(vchk) < 0.3 * vdd) if topo.fd else (0.1 * vdd < vchk < 0.9 * vdd)
        if not ok:
            continue
        settled = True
        # AC: dump real/imag of the (differential) output; mag/phase in numpy.
        # wrdata writes a scale (frequency) column per vector → [f, re, f, im].
        ac = (body_dut.replace("__PORTS__", " ".join(ports)) + _rig(vdd, ibias)
              + fb + _xline(name, ports, netmap) + "\n"
              + f".control\nac dec 30 1 1e10\nlet vod={outexpr}\n"
              + "wrdata __OUT__ real(vod) imag(vod)\n.endc\n.end\n")
        a = _run(ac, ["re", "im"])
        if a is None or a.shape[0] < 5 or a.shape[1] < 4:
            continue
        f, re, im = a[:, 0], a[:, 1], a[:, 3]
        mag = np.hypot(re, im)
        good = (f > 0) & (mag > 0)
        f, mag = f[good], mag[good]
        re, im = re[good], im[good]
        if len(f) < 5:
            continue
        gdb = 20 * np.log10(mag)
        # Phase relative to DC removes the unknown inverting-input baseline
        # (DC phase is ~0° for non-inverting drive, ~±180° otherwise).
        phase = np.degrees(np.unwrap(np.arctan2(im, re)))
        phase -= phase[0]
        gain_db = float(gdb[0])
        gbw = pm = None
        below = np.where(gdb <= 0)[0]
        if below.size and below[0] > 0:
            i1 = int(below[0]); i0 = i1 - 1
            lf0, lf1 = np.log10(f[i0]), np.log10(f[i1])
            t = (0 - gdb[i0]) / (gdb[i1] - gdb[i0])
            gbw = float(10 ** (lf0 + t * (lf1 - lf0)))
            ph_gbw = phase[i0] + t * (phase[i1] - phase[i0])
            pm = float(180.0 + ph_gbw)   # excess phase is negative → PM < 180
        # Keep the higher-gain polarity (the negative-feedback one); report it
        # even if ≤ 0 dB so a mis-biased circuit isn't silently dropped.
        if best is None or gain_db > best[0]:
            best = (gain_db, gbw, pm)
    if best is None:
        reason = ("open-loop AC did not settle (output railed) — gain not measurable"
                  if not settled else "open-loop AC sweep did not converge")
        return None, None, None, reason
    gain_db, gbw, pm = best
    reason = (None if gain_db > 0
              else "measured gain ≤ 0 dB — circuit does not amplify as biased")
    return gain_db, gbw, pm, reason


def _measure_sr(name, ports, body_dut, topo, vdd, ibias, vcm):
    """Unity-gain large-signal step → slew rate (V/s). SE only (best-effort)."""
    if topo.fd:
        return None   # FD direct-feedback SR harness omitted in this pass
    step = 0.3 * vdd
    for inp, inn in (("in1", "in2"), ("in2", "in1")):
        netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
                  inp: "inp", inn: "inn", "out": "out"}
        # unity buffer: out -> inverting input (direct), step the non-inverting input
        fb = (f"Rfb out inn 1\n"
              f"Vstep inp 0 pulse({vcm} {vcm + step} 5n 10p 10p 1 1)\n")
        deck = (body_dut.replace("__PORTS__", " ".join(ports)) + _rig(vdd, ibias)
                + fb + _xline(name, ports, netmap) + "\n"
                + ".control\ntran 0.05n 60n uic\nwrdata __OUT__ v(out)\n.endc\n.end\n")
        a = _run(deck, ["v(out)"])
        if a is None or a.shape[0] < 10:
            continue
        t, vo = a[:, 0], a[:, 1]
        if abs(vo[0] - vcm) > 0.4 * vdd:   # unity buffer must start near CM
            continue
        # Standard 20%-80% slew measurement (robust to edge spikes): the average
        # slope across the central 60% of the output transition.
        v0, vf = vo[0], vo[-1]
        swing = vf - v0
        if abs(swing) < 0.05 * vdd:
            continue
        lo, hi = v0 + 0.2 * swing, v0 + 0.8 * swing
        prog = (vo - v0) / swing            # 0→1 fraction of the transition
        idx = np.where((prog >= 0.2) & (prog <= 0.8))[0]
        if idx.size < 2:
            continue
        dt = t[idx[-1]] - t[idx[0]]
        if dt <= 0:
            continue
        sr = abs(0.6 * swing) / dt
        if sr > 0:
            return float(sr)
    return None


# --------------------------------------------------------------------------- #
# Operating-point read (for SPICE-in-the-loop refinement)
# --------------------------------------------------------------------------- #
def _run_capture(deck: str) -> str | None:
    """Run ``deck`` in ngspice -b and return stdout (for ``print`` output)."""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "deck.sp"
        sp.write_text(deck)
        try:
            p = subprocess.run(["ngspice", "-b", str(sp)], capture_output=True,
                               text=True, timeout=60)
        except Exception:
            return None
        return p.stdout


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
    if not ngspice_available():
        return None
    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd:
        return None
    body_dut = _dut(tech, name, _inject_sizes(body, result))
    refs = list(result.transistors)
    if not refs:
        return None
    vdd, ibias = spec.vdd, spec.ibias
    vcm = (spec.vdd + spec.vss) / 2.0
    probe = "".join(
        f"print @m.xdut.{r}[id]\nprint @m.xdut.{r}[vds]\nprint @m.xdut.{r}[vdsat]\n"
        for r in refs
    )
    for inp, inn in (("in1", "in2"), ("in2", "in1")):
        netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
                  inp: "inp", inn: "inn", "out": "out"}
        fb = (f"Vcm cm 0 {vcm}\nLfb out inn 1e12\nCfb inn cm 1e3\n"
              f"Vid inp cm dc 0\n")
        deck = (body_dut.replace("__PORTS__", " ".join(ports)) + _rig(vdd, ibias)
                + fb + _xline(name, ports, netmap) + "\n"
                + ".control\nop\nprint v(out)\n" + probe + ".endc\n.end\n")
        txt = _run_capture(deck)
        if txt is None:
            continue
        mo = re.search(r"v\(out\)\s*=\s*([-\d.eE+]+)", txt)
        if not mo or not (0.1 * vdd < float(mo.group(1)) < 0.9 * vdd):
            continue  # wrong polarity → output railed
        op: dict[str, dict[str, float]] = {}
        for m in re.finditer(r"@m\.xdut\.(\w+)\[(\w+)\]\s*=\s*([-\d.eE+]+)", txt):
            op.setdefault(m.group(1), {})[m.group(2)] = float(m.group(3))
        if op:
            return op
    return None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def simulate_metrics(netlist_text: str, result: SizingResult,
                     tech: TechParams, spec: SizingSpec) -> dict[str, float | None]:
    """Return SPICE-measured metrics, mirroring ``_evaluate_metrics`` keys.

    Keys: ``power_w``, ``gain_db``, ``gbw_hz``, ``phase_margin_deg``,
    ``slew_rate_vps``, ``output_swing_max_v``, ``output_swing_min_v``.
    Missing/failed measurements are ``None``.
    """
    name, ports, body = _parse_subckt(netlist_text)
    body = _inject_sizes(body, result)
    body_dut = _dut(tech, name, body)
    topo = _Topo(ports)
    vdd = spec.vdd
    ibias = spec.ibias
    vcm = (spec.vdd + spec.vss) / 2.0

    out: dict[str, float | None] = {
        "power_w": None, "gain_db": None, "gbw_hz": None,
        "phase_margin_deg": None, "slew_rate_vps": None,
        "output_swing_max_v": None, "output_swing_min_v": None,
    }
    notes: list[str] = []
    try:
        out["power_w"] = _measure_power(name, ports, body_dut, topo, vdd, ibias, vcm)
    except Exception:
        pass
    try:
        g, gbw, pm, reason = _measure_ac(name, ports, body_dut, topo, vdd, ibias, vcm)
        out["gain_db"], out["gbw_hz"], out["phase_margin_deg"] = g, gbw, pm
        if reason:
            notes.append(reason + " (GBW/PM not measurable)")
            bias = _bias_diagnostic(netlist_text, result, tech, spec)
            if bias:
                notes.append(bias)
    except Exception:
        pass
    try:
        out["slew_rate_vps"] = _measure_sr(name, ports, body_dut, topo, vdd, ibias, vcm)
    except Exception:
        pass
    if notes:
        out["notes"] = notes  # type: ignore[assignment]  # advisory, not a metric
    return out


def _op_bias_problems(op: dict[str, dict[str, float]]) -> tuple[list[str], list[str]]:
    """Return ``(triode_refs, starved_refs)`` from an operating-point dict.

    Starved: drain current below 0.1 µA (device effectively off). Triode: |Vds|
    below |Vdsat| (a current source/amplifier device pushed out of saturation).
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
    op = read_op_operating_point(netlist_text, result, tech, spec)
    if op is None:
        return False, ("SPICE bias check: the feedback operating point railed — the "
                       "circuit does not establish a usable mid-rail bias point.")
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
