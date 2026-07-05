"""Per-metric ngspice testbenches.

Each testbench builds an independent deck around the sized DUT block and is
best-effort: a measurement that fails to converge/parse returns ``None``
instead of raising.

The AC bench detects the input polarity — which of (in1, in2) is the
non-inverting input — via a DC-settle check, and reports it so the slew,
swing, CMRR and PSRR benches reuse it instead of re-detecting.

Feedback rigs (all AC-coupled through huge L/C so the loop only sets the DC
operating point):

* **SE loop** (AC/CMRR/PSRR): ``Lfb`` closes out→inn at DC, ``Cfb`` AC-grounds
  the inverting input to the ``cm`` node.
* **FD loop** (AC/CMRR/PSRR): ``L1``/``L2`` close outp→inn / outn→inp at DC.
* **Unity buffer** (slew/swing): out→inn direct — the large-signal rigs.
"""
from __future__ import annotations

import numpy as np

from .deck import _run
from .rig import _POLARITIES, _deck, _fb_netmap

# Feedback L must be AC-OPEN even at the lowest sweep frequency (1 Hz):
# ωL = 2π·1·1e12 ≈ 6e12 Ω. C is an AC-short that grounds/couples at AC.
_LH, _CH = "1e12", "1e3"


def _pols(polarity):
    """Polarities to try, the AC-detected one first.

    The AC polarity is only a **hint**: ngspice's ``.op`` also converges on the
    *unstable* equilibrium of a positive-feedback loop, so the AC bench cannot
    always tell the two assignments apart (their small-signal magnitudes are
    symmetric for a differential pair).  The large-signal benches keep their
    own validity check and fall back to the other polarity when the hint rails.
    """
    if polarity is None:
        return _POLARITIES
    return (polarity, *(p for p in _POLARITIES if p != polarity))


def _lf_mag(name, ports, body_dut, vdd, ibias, fb, netmap, outexpr,
            sup_ac: bool = False):
    """Low-frequency (1 mHz, matching the gain bench's first AC point)
    magnitude of ``outexpr`` for a 1 V AC stimulus."""
    control = (f"ac lin 1 1e-3 1e-3\nlet vod={outexpr}\n"
               "wrdata __OUT__ real(vod) imag(vod)")
    deck = _deck(name, ports, body_dut, vdd, ibias, fb, netmap, control,
                 sup_ac=sup_ac)
    a = _run(deck, ["re", "im"])
    if a is None or a.shape[1] < 4:
        return None
    row = a[0]
    return float(np.hypot(row[1], row[3]))


def _measure_power(name, ports, body_dut, topo, vdd, ibias, vcm):
    """DC operating point → quiescent power."""
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
              "in1": "cm", "in2": "cm"}
    for o in topo.out:
        netmap[o] = o
    if topo.has_vcm:
        netmap["vcm_ref"] = "cm"
    deck = _deck(name, ports, body_dut, vdd, ibias, f"Vcm cm 0 {vcm}\n",
                 netmap, "op\nwrdata __OUT__ i(Vsup)")
    data = _run(deck, ["i(Vsup)"])
    if data is None:
        return None
    i = abs(float(data.flatten()[-1]))
    return vdd * i


def _pm_plausible(pm: float | None) -> bool:
    """True unless ``pm`` is outside the physical ``(0°, 180°]`` range.

    A stable amplifier's phase at the 0-dB crossing lies below its DC phase, so
    ``PM = 180° + phase`` lands in ``(0°, 180°]``.  A value **above 180°**
    (phase lead on a falling gain) is a non-minimum-phase — right-half-plane —
    response: a genuinely mis-compensated circuit, e.g. Miller-family
    compensation wrapped around a non-inverting second stage.  A value ≤ 0°
    means the crossing came from a corrupted sweep — typically the wrong
    feedback polarity settling into a measurable but meaningless response, or
    a phase-unwrap glitch.  Neither is a usable gain/GBW/PM measurement.
    ``None`` (no crossing found) carries no such evidence.
    """
    return pm is None or 0.0 < pm <= 180.0


def _loop_fb(topo, vcm, drive: str) -> tuple[str, str]:
    """The AC-coupled feedback loop + output expression, with ``drive`` lines.

    SE: ``Lfb`` (DC feedback) + ``Cfb`` (AC ground to ``cm``).  FD: ``L1``/
    ``L2`` cross-feedback with the output CM pinned at ``ocm``.
    """
    if topo.fd:
        fb = (f"Vocm ocm 0 {vcm}\n"
              f"L1 outp inn {_LH}\nL2 outn inp {_LH}\n" + drive)
        return fb, "v(outp)-v(outn)"
    fb = (f"Vcm cm 0 {vcm}\n"
          f"Lfb out inn {_LH}\nCfb inn cm {_CH}\n" + drive)
    return fb, "v(out)"


def _measure_ac(name, ports, body_dut, topo, vdd, ibias, vcm):
    """Open-loop AC: returns ``(gain_db, gbw_hz, pm_deg, reason, polarity)``.

    AC-coupled feedback: huge L closes the loop at DC (sets bias ≈ CM), huge C
    AC-grounds the inverting input; a 1 V AC source drives the non-inverting
    input.  The (in1,in2)->(non-inv,inv) assignment is auto-detected via the DC
    operating point (output must settle near CM, not a rail), and among the
    branches that settle the one with a *plausible* phase margin wins (higher
    gain breaks ties): a corrupted branch shows an implausible PM and must not
    outrank the honest measurement just because its gain reads higher.  The
    winning ``polarity`` is returned for the other benches to reuse (``None``
    when nothing usable settled).

    The **measured** low-frequency gain is reported even when it is ≤ 0 dB (a
    mis-biased circuit that does not amplify) — ``gbw``/``pm`` are then ``None``
    (no 0-dB crossing) and ``reason`` explains why.  When every settled branch
    is corrupt (PM outside ``(0°, 180°]``) the whole extraction is discarded:
    all three values are ``None`` and ``reason`` says so.  ``reason`` is
    ``None`` on a normal (positive-gain) measurement.
    """
    settled = False
    best: tuple[float, float | None, float | None] | None = None
    best_rank: tuple[bool, float] | None = None
    best_pol: tuple[str, str] | None = None
    for inp, inn in _POLARITIES:
        netmap = _fb_netmap(topo, inp, inn)
        # No AC-grounding caps in the FD drive — they would short out Vid.
        drive = ("Vid inp inn ac 1\n" if topo.fd else "Vid inp cm ac 1\n")
        fb, outexpr = _loop_fb(topo, vcm, drive)
        # DC check: output must settle near CM (negative feedback), else swap.
        dc = _deck(name, ports, body_dut, vdd, ibias, fb, netmap,
                   f"op\nlet vchk={outexpr}\nwrdata __OUT__ vchk")
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
        # The sweep starts at 1 mHz so the first point sits below even a
        # sub-Hz dominant pole (high-gain designs): the phase baseline and the
        # reported low-frequency gain are then genuinely DC values — starting
        # at 1 Hz skews the phase reference (and thus PM) by tens of degrees.
        ac = _deck(name, ports, body_dut, vdd, ibias, fb, netmap,
                   f"ac dec 30 1e-3 1e10\nlet vod={outexpr}\n"
                   "wrdata __OUT__ real(vod) imag(vod)")
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
            best, best_rank, best_pol = (gain_db, gbw, pm), rank, (inp, inn)
    if best is None:
        reason = ("open-loop AC did not settle (output railed) — gain not measurable"
                  if not settled else "open-loop AC sweep did not converge")
        return None, None, None, reason, None
    gain_db, gbw, pm = best
    if not _pm_plausible(pm):
        if pm > 180.0:
            # Phase LEAD at the crossing on a falling gain = a non-minimum-
            # phase (right-half-plane) response — a genuinely mis-compensated
            # circuit (e.g. Miller-family compensation wrapped around a
            # non-inverting second stage), not a sweep artifact.
            return None, None, None, (
                f"AC phase leads at the 0-dB crossing (PM {pm:.0f}° > 180°) — "
                "right-half-plane response; the stage-inversion/compensation "
                "combination is unsound"), None
        # Every settled branch was corrupt: its gain/GBW come from the same
        # meaningless sweep, so discard the extraction rather than report it.
        return None, None, None, (
            f"AC phase at the 0-dB crossing is implausible (PM {pm:.0f}° "
            f"outside (0°, 180°]) — extraction artifact, discarded"), None
    reason = (None if gain_db > 0
              else "measured gain ≤ 0 dB — circuit does not amplify as biased")
    return gain_db, gbw, pm, reason, best_pol


def _edge_slew(t, vo, vdd) -> float | None:
    """Standard 20%-80% slew of one output transition (robust to edge spikes):
    the average slope across the central 60% of the swing."""
    if len(t) < 4:
        return None
    v0, vf = vo[0], vo[-1]
    swing = vf - v0
    if abs(swing) < 0.05 * vdd:
        return None
    prog = (vo - v0) / swing            # 0→1 fraction of the transition
    idx = np.where((prog >= 0.2) & (prog <= 0.8))[0]
    if idx.size < 2:
        return None
    dt = t[idx[-1]] - t[idx[0]]
    if dt <= 0:
        return None
    sr = abs(0.6 * swing) / dt
    return float(sr) if sr > 0 else None


def _measure_sr(name, ports, body_dut, topo, vdd, ibias, vcm, polarity=None,
                sr_hint: float | None = None):
    """Unity-gain large-signal pulse → slew rate (V/s), the **min of the
    rising and falling edges**.  SE only (best-effort).

    The transient starts from the DC operating point (no ``uic`` — a
    zero-state start would measure the power-up transient instead of the step
    response), and the pulse width is scaled so a design slewing at ≥ 1/3 of
    the spec target (``sr_hint``) completes its transition inside the window.
    """
    if topo.fd:
        return None   # FD direct-feedback SR harness omitted in this pass
    step = 0.3 * vdd
    # Window long enough for 3× the spec-implied transition time (default 1 µs
    # per edge when the spec does not constrain slew rate).
    t_edge = max(3.0 * step / sr_hint, 60e-9) if sr_hint else 1e-6
    t0 = 0.02 * t_edge                     # settle margin before the step
    for inp, inn in _pols(polarity):
        netmap = _fb_netmap(topo, inp, inn)
        # unity buffer: out -> inverting input (direct), pulse the non-inverting input
        fb = (f"Rfb out inn 1\n"
              f"Vstep inp 0 pulse({vcm} {vcm + step} {t0} 10p 10p {t_edge} 1)\n")
        deck = _deck(name, ports, body_dut, vdd, ibias, fb, netmap,
                     f"tran {(t0 + 2 * t_edge) / 2000} {t0 + 2 * t_edge}\n"
                     "wrdata __OUT__ v(out)")
        a = _run(deck, ["v(out)"])
        if a is None or a.shape[0] < 10:
            continue
        t, vo = a[:, 0], a[:, 1]
        if abs(vo[0] - vcm) > 0.4 * vdd:   # unity buffer must start near CM
            continue
        rising = t < t0 + t_edge           # pulse falls back at t0 + t_edge
        edges = [s for s in (_edge_slew(t[rising], vo[rising], vdd),
                             _edge_slew(t[~rising], vo[~rising], vdd))
                 if s is not None]
        if edges:
            return float(min(edges))
    return None


def _measure_swing(name, ports, body_dut, topo, vdd, ibias, vcm, polarity=None):
    """Inverting −1 DC sweep → ``(swing_max, swing_min)`` in V.  SE only.

    The output is driven across the supply through an inverting gain −1
    network while the non-inverting input stays at CM, so the input stage
    never leaves its common-mode range: the sweep measures **output** swing,
    not the intersection of output swing and ICMR (a unity buffer, whose
    input rides with the output, conflates the two — issue #126).  Large
    feedback resistors keep the DUT's output unloaded.

    The output is read over the contiguous **tracking region** around CM
    (small-signal slope ≤ −0.7 — outside it the output stage has saturated
    and the output no longer follows).  The region's edge values are the
    reachable output extremes.
    """
    if topo.fd:
        return None, None
    step = max(vdd / 200.0, 1e-3)
    for inp, inn in _pols(polarity):
        netmap = _fb_netmap(topo, inp, inn)
        fb = (f"Rfb out inn 10meg\nRin src inn 10meg\n"
              f"Vp inp 0 dc {vcm}\nVin src 0 dc {vcm}\n")
        deck = _deck(name, ports, body_dut, vdd, ibias, fb, netmap,
                     f"dc Vin 0 {vdd} {step}\nwrdata __OUT__ v(out)")
        a = _run(deck, ["v(out)"])
        if a is None or a.shape[0] < 20 or a.shape[1] < 2:
            continue
        vin, vo = a[:, 0], a[:, 1]           # ideal: vo = 2·vcm − vin
        slope = np.gradient(vo, vin)
        track = slope <= -0.7
        icm = int(np.argmin(np.abs(vin - vcm)))
        if not track[icm]:
            continue   # loop does not even track at CM → wrong polarity
        lo = hi = icm
        while lo > 0 and track[lo - 1]:
            lo -= 1
        while hi < len(track) - 1 and track[hi + 1]:
            hi += 1
        return float(vo[lo]), float(vo[hi])  # inverting: max at low vin
    return None, None


def _measure_cmrr(name, ports, body_dut, topo, vdd, ibias, vcm, polarity,
                  adm_db):
    """Common-mode rejection: ``CMRR = Adm − Acm`` in dB.

    Same DC feedback loop as the gain bench, but the 1 V AC rides on the input
    **common mode**: SE — the ``cm`` node itself carries the AC (the inverting
    input receives it through ``Cfb``, the non-inverting one through the
    0 V ``Vid``); FD — both gates are AC-coupled to one source, and the
    common-mode → differential conversion is read at the output.
    """
    if adm_db is None or polarity is None:
        return None
    inp, inn = polarity
    netmap = _fb_netmap(topo, inp, inn)
    if topo.fd:
        drive = (f"Vci ci 0 dc 0 ac 1\n"
                 f"Cc1 inp ci {_CH}\nCc2 inn ci {_CH}\n")
        fb, outexpr = _loop_fb(topo, vcm, drive)
    else:
        fb, outexpr = _loop_fb(topo, vcm, "Vid inp cm dc 0\n")
        fb = fb.replace(f"Vcm cm 0 {vcm}\n", f"Vcm cm 0 dc {vcm} ac 1\n")
    acm = _lf_mag(name, ports, body_dut, vdd, ibias, fb, netmap, outexpr)
    if acm is None or acm <= 1e-12:   # numerical zero → nothing was measured
        return None
    return adm_db - 20.0 * np.log10(acm)


def _measure_psrr(name, ports, body_dut, topo, vdd, ibias, vcm, polarity,
                  adm_db):
    """Positive-supply rejection: ``PSRR+ = Adm − Avdd`` in dB.

    Same DC feedback loop as the gain bench with a quiet input; the 1 V AC
    rides on VDD and the supply-to-output gain is read at low frequency.
    """
    if adm_db is None or polarity is None:
        return None
    inp, inn = polarity
    netmap = _fb_netmap(topo, inp, inn)
    drive = ("Vid inp inn dc 0\n" if topo.fd else "Vid inp cm dc 0\n")
    fb, outexpr = _loop_fb(topo, vcm, drive)
    avdd = _lf_mag(name, ports, body_dut, vdd, ibias, fb, netmap, outexpr,
                   sup_ac=True)
    if avdd is None or avdd <= 1e-12:   # numerical zero → nothing was measured
        return None
    return adm_db - 20.0 * np.log10(avdd)
