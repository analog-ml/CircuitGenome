"""Per-metric ngspice testbenches (power, AC gain/GBW/PM, slew rate).

Each testbench builds an independent deck around the sized DUT block and is
best-effort: a measurement that fails to converge/parse returns ``None``
instead of raising.
"""
from __future__ import annotations

import numpy as np

from .deck import _run
from .rig import _rig, _xline


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


def _pm_plausible(pm: float | None) -> bool:
    """True unless ``pm`` is outside the physical ``(0°, 180°]`` range.

    A stable amplifier's phase at the 0-dB crossing lies below its DC phase, so
    ``PM = 180° + phase`` lands in ``(0°, 180°]``.  A value outside that range
    means the crossing came from a **corrupted sweep** — typically the wrong
    feedback polarity settling into a measurable but meaningless response
    (seen with two-input second stages), or a phase-unwrap glitch — not from
    the real amplifier.  ``None`` (no crossing found) carries no such evidence.
    """
    return pm is None or 0.0 < pm <= 180.0


def _measure_ac(name, ports, body_dut, topo, vdd, ibias, vcm):
    """Open-loop AC: returns ``(gain_db, gbw_hz, pm_deg, reason)``.

    AC-coupled feedback: huge L closes the loop at DC (sets bias ≈ CM), huge C
    AC-grounds the inverting input; a 1 V AC source drives the non-inverting
    input.  The (in1,in2)->(non-inv,inv) assignment is auto-detected via the DC
    operating point (output must settle near CM, not a rail), and among the
    branches that settle the one with a *plausible* phase margin wins (higher
    gain breaks ties): a corrupted branch shows an implausible PM and must not
    outrank the honest measurement just because its gain reads higher.

    The **measured** low-frequency gain is reported even when it is ≤ 0 dB (a
    mis-biased circuit that does not amplify) — ``gbw``/``pm`` are then ``None``
    (no 0-dB crossing) and ``reason`` explains why.  When every settled branch
    is corrupt (PM outside ``(0°, 180°]``) the whole extraction is discarded:
    all three values are ``None`` and ``reason`` says so.  ``reason`` is
    ``None`` on a normal (positive-gain) measurement.
    """
    # Feedback L must be AC-OPEN even at the lowest sweep frequency (1 Hz):
    # ωL = 2π·1·1e12 ≈ 6e12 Ω. C grounds the inverting input at AC.
    Lh, Ch = "1e12", "1e3"
    settled = False
    best: tuple[float, float | None, float | None] | None = None
    best_rank: tuple[bool, float] | None = None
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
        # Keep the plausible-PM branch, then the higher gain (the negative-
        # feedback one); report it even if ≤ 0 dB so a mis-biased circuit
        # isn't silently dropped.
        rank = (_pm_plausible(pm), gain_db)
        if best_rank is None or rank > best_rank:
            best, best_rank = (gain_db, gbw, pm), rank
    if best is None:
        reason = ("open-loop AC did not settle (output railed) — gain not measurable"
                  if not settled else "open-loop AC sweep did not converge")
        return None, None, None, reason
    gain_db, gbw, pm = best
    if not _pm_plausible(pm):
        # Every settled branch was corrupt: its gain/GBW come from the same
        # meaningless sweep, so discard the extraction rather than report it.
        return None, None, None, (
            f"AC phase at the 0-dB crossing is implausible (PM {pm:.0f}° "
            f"outside (0°, 180°]) — extraction artifact, discarded")
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
