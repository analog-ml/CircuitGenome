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
* **FD loop** (AC): inputs resistor-anchored at Vcm, the CMFB owns the output
  CM (:func:`_loop_fb`).
* **FD servo** (CMRR/PSRR, under PDK device mismatch): a clamped, DC-only
  servo nulls the mismatch offset at the inputs (:func:`_fd_servo_fb`).
* **Unity buffer** (slew/swing): out→inn direct — the large-signal rigs.
* **FD gain −1** (slew/swing): behavioural sources feed back only the
  differential output, pinning the input CM at Vcm (:func:`_fd_inverting_fb`).
"""
from __future__ import annotations

import re

import numpy as np

from .deck import _run, _run_capture
from .rig import _POLARITIES, _deck, _fb_netmap

# Feedback L must be AC-OPEN even at the lowest sweep frequency (1 Hz):
# ωL = 2π·1·1e12 ≈ 6e12 Ω. C is an AC-short that grounds/couples at AC.
_LH, _CH = "1e12", "1e3"
_RH = "1e9"   # FD input-anchor resistors (zero DC drop into MOS gates)


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


def _lf_mag(name, ports, body_dut, vdd, ibias, cl, fb, netmap, outexpr,
            sup_ac: bool = False):
    """Low-frequency (1 mHz, matching the gain bench's first AC point)
    magnitude of ``outexpr`` for a 1 V AC stimulus."""
    control = (f"ac lin 1 1e-3 1e-3\nlet vod={outexpr}\n"
               "wrdata __OUT__ real(vod) imag(vod)")
    deck = _deck(name, ports, body_dut, vdd, ibias, cl, fb, netmap, control,
                 sup_ac=sup_ac)
    a = _run(deck, ["re", "im"])
    if a is None or a.shape[1] < 4:
        return None
    row = a[0]
    return float(np.hypot(row[1], row[3]))


def _measure_power(name, ports, body_dut, topo, vdd, ibias, cl, vcm):
    """DC operating point → quiescent power."""
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
              "in1": "cm", "in2": "cm"}
    for o in topo.out:
        netmap[o] = o
    if topo.has_vcm:
        netmap["vcm_ref"] = "cm"
    deck = _deck(name, ports, body_dut, vdd, ibias, cl, f"Vcm cm 0 {vcm}\n",
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
    compensation wrapped around a non-inverting second stage.  On the SE
    bench a value ≤ 0° means the crossing came from a corrupted sweep —
    typically the wrong feedback polarity settling into a measurable but
    meaningless response, or a phase-unwrap glitch.  Neither is a usable
    gain/GBW/PM measurement.  (The FD bench has no feedback loop to corrupt,
    so :func:`_measure_ac` reports its PM ≤ 0° as a real instability.)
    ``None`` (no crossing found) carries no such evidence.
    """
    return pm is None or 0.0 < pm <= 180.0


def _loop_fb(topo, vcm, drive: str) -> tuple[str, str]:
    """The DC-bias network + output expression, with ``drive`` lines.

    SE: ``Lfb`` (DC feedback) + ``Cfb`` (AC ground to ``cm``).  FD (issue
    #165): inputs DC-anchored at Vcm through huge inductors (DC shorts, AC
    opens) with the CMFB owning the output CM — the standard FD bench.  The
    old cross-feedback ties (``outp``→``inn``/``outn``→``inp``) were a
    pre-#165 crutch that invented an output CM for unregulated circuits;
    against a real CM loop they form a bistable DC network whose degenerate
    all-off solution ngspice picks at tight headroom (sky130: healthy
    circuits measured −79 dB), and they made the FD DC sanity check vacuous
    (ties force ``outp == outn``).  With free outputs that check is
    meaningful again.
    """
    if topo.fd:
        # Resistor anchors, not inductors: L anchors + the 0 V differential
        # drive would form a singular L–V loop (two DC shorts plus a source),
        # which ngspice regularizes into garbage bias.  MOS gates draw no DC
        # current, so the R drop is zero and the inputs sit exactly at Vcm.
        fb = (f"Vocm ocm 0 {vcm}\nVicm icm 0 {vcm}\n"
              f"R1 icm inp {_RH}\nR2 icm inn {_RH}\n" + drive)
        return fb, "v(outp)-v(outn)"
    fb = (f"Vcm cm 0 {vcm}\n"
          f"Lfb out inn {_LH}\nCfb inn cm {_CH}\n" + drive)
    return fb, "v(out)"


def _measure_ac(name, ports, body_dut, topo, vdd, ibias, cl, vcm):
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
    all three values are ``None`` and ``reason`` says so.  The one exception
    is a fully-differential PM ≤ 0°, which is a real open-loop instability and
    is returned as measured (issue #236).  ``reason`` is ``None`` on a normal
    (positive-gain) measurement.
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
        dc = _deck(name, ports, body_dut, vdd, ibias, cl, fb, netmap,
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
        ac = _deck(name, ports, body_dut, vdd, ibias, cl, fb, netmap,
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
        if topo.fd:
            # The FD bench has no loop around the DUT (resistor-anchored
            # inputs, the CMFB owns the output CM), so the input polarity only
            # flips the sign of v(outp)-v(outn): it cannot corrupt the sweep.
            # PM ≤ 0° is a real instability at this load — report it, so the
            # design fails its PM spec on the number (issue #236).
            return gain_db, gbw, pm, None, best_pol
        # Every settled branch was corrupt: its gain/GBW come from the same
        # meaningless sweep, so discard the extraction rather than report it.
        return None, None, None, (
            f"AC phase at the 0-dB crossing is implausible (PM {pm:.0f}° "
            f"outside (0°, 180°]) — extraction artifact, discarded"), None
    reason = (None if gain_db > 0
              else "measured gain ≤ 0 dB — circuit does not amplify as biased")
    return gain_db, gbw, pm, reason, best_pol


def _fd_inverting_fb(vcm, drive: str) -> str:
    """FD inverting gain −1 feedback around the DUT (the swing/slew rig).

    Ideal behavioural sources close the loop on the **differential** signal
    only: each input sits at ``Vcm ± (v(vd) + v(outp) − v(outn))/4`` — the
    summing-node voltages of a resistive −1 network (β = ½), so
    ``v(outp) − v(outn) → −v(vd)`` while the CMFB holds the output CM at
    ``ocm``.  The input CM stays pinned at Vcm, so the rig measures
    **output** limits, never ICMR (the FD twin of :func:`_measure_swing`'s
    inverting sweep, issue #126).  A real resistor network would instead tie
    the input CM to the output CM: an output pair stuck at a rail drags the
    inputs out of range, which turns the pair off and holds the outputs there
    — a latched operating point ngspice finds (issue #208).  The sources also
    draw no current, so the outputs carry only ``cl``.  ``drive`` must define
    the differential source node ``vd``.
    """
    fb = "(v(vd)+v(outp)-v(outn))/4"
    return (f"Vocm ocm 0 {vcm}\n" + drive
            + f"Binn inn 0 v={vcm}+{fb}\nBinp inp 0 v={vcm}-{fb}\n")


_SR_SPAN = 0.2   # fraction of the total edge swing the slew window spans
_SR_RANGE = (0.1, 0.9)   # the part of the swing a slew window must lie within


def _edge_slew(t, vo, vdd) -> float | None:
    """Peak slew rate of one output transition (V/s): the steepest secant
    across any window spanning ``_SR_SPAN`` of the total swing, inside the
    usual 10 %–90 % measurement range (``_SR_RANGE``).

    Slew rate is the *current-limited* rate the output can move — the fast,
    roughly constant ramp early in a large step. The previous 20%-80% average
    measured that only when the edge had fully settled inside the window; on an
    edge whose transient ends on a slow pole-zero-doublet *settling* tail the
    80% mark lands in the tail and the "slew" collapsed to the settling speed
    (e.g. 0.7 V/µs measured vs the current-limited ibias/Cc plateau — issue #65).

    A fixed-voltage-span secant reads the plateau instead: for each start sample
    it takes the first later sample that has moved ``_SR_SPAN`` of the swing in
    the edge's direction (via the running extreme, so end-ringing can't cut a
    window short), and keeps the steepest such secant. The span is wide enough
    to skip the sub-sample feedback-cap feedthrough spike, short enough not to
    average in the settling tail.

    Slewing is the current-limited middle of the edge, so a window must start
    no earlier than the first sample past 10 % of the swing and end by the
    first one past 90 %.  Both ends of an edge can outrun its ramp: the step
    couples straight through the Miller caps into the output at the start
    (three-stage edges read ~8 % high off that jump), and near the target the
    input stage leaves saturation and the loop pulls the output in -- as do
    the overshoot and ringing past it (a two-stage edge ramping at ~6 V/µs
    read 10 V/µs off its undershoot)."""
    if len(t) < 4:
        return None
    swing = vo[-1] - vo[0]
    if abs(swing) < 0.05 * vdd:
        return None
    span = _SR_SPAN * abs(swing)
    # First sample that is `span` past each start, in the edge's direction. The
    # running extreme is monotone, so searchsorted finds that crossing per start.
    if swing > 0:
        j = np.searchsorted(np.maximum.accumulate(vo), vo + span)
    else:
        j = np.searchsorted(-np.minimum.accumulate(vo), span - vo)
    # First samples past 10 % and 90 % of the swing bound the windows.
    toward = vo if swing > 0 else -vo
    lo, hi = (toward[0] + f * abs(swing) for f in _SR_RANGE)
    start, end = int(np.argmax(toward >= lo)), int(np.argmax(toward >= hi))
    i = np.arange(len(t))
    reached = (i >= start) & (j <= end)
    if not reached.any():
        return None
    i, j = i[reached], j[reached]
    dt = t[j] - t[i]
    good = dt > 0
    if not good.any():
        return None
    return float(np.max(np.abs(vo[j] - vo[i])[good] / dt[good]))


def _follows_pulse(v_rise, v_fall, step) -> bool:
    """Whether a unity buffer's output followed the pulse: up by at least half
    the step on its rising half, and back down by as much on its falling half.

    The wrong feedback polarity is positive feedback: the output starts at the
    CM (the ``.op`` sits on the unstable equilibrium) and then latches to a rail
    instead, and that collapse would otherwise be read as a fast edge."""
    if len(v_rise) == 0 or len(v_fall) == 0:
        return False
    return (v_rise.max() - v_rise[0] >= 0.5 * step
            and v_fall[0] - v_fall.min() >= 0.5 * step)


def _measure_sr(name, ports, body_dut, topo, vdd, ibias, cl, vcm, polarity=None,
                sr_hint: float | None = None):
    """Unity-gain large-signal pulse → slew rate (V/s), the **min of the
    rising and falling edges** (best-effort).  FD: :func:`_measure_sr_fd`.

    The transient starts from the DC operating point (no ``uic`` — a
    zero-state start would measure the power-up transient instead of the step
    response), and the pulse width is scaled so a design slewing at ≥ 1/3 of
    the spec target (``sr_hint``) completes its transition inside the window.

    The step is centred on Vcm (``Vcm ± 0.15·Vdd``): a unity buffer's input
    common mode rides with the output, and a step from Vcm up to
    Vcm + 0.3·Vdd moved it a whole step away from the quiescent point the
    amplifier was sized at -- starving a resistor tail on a PMOS pair, or the
    headroom of a PMOS tail, and pushing an NMOS pair under a telescopic
    cascode out of saturation (rising edges read 30-85 % slow).  Centring
    halves that excursion, so the edges measure slewing rather than the
    input common-mode range.
    """
    if topo.fd:
        return _measure_sr_fd(name, ports, body_dut, topo, vdd, ibias, cl, vcm,
                              polarity, sr_hint)
    step = 0.3 * vdd
    # Window long enough for 3× the spec-implied transition time (default 1 µs
    # per edge when the spec does not constrain slew rate).
    t_edge = max(3.0 * step / sr_hint, 60e-9) if sr_hint else 1e-6
    t0 = 0.02 * t_edge                     # settle margin before the step
    for inp, inn in _pols(polarity):
        netmap = _fb_netmap(topo, inp, inn)
        # unity buffer: out -> inverting input (direct), pulse the non-inverting
        # input across a step centred on Vcm
        lo, hi = vcm - step / 2, vcm + step / 2
        fb = (f"Rfb out inn 1\n"
              f"Vstep inp 0 pulse({lo} {hi} {t0} 10p 10p {t_edge} 1)\n")
        deck = _deck(name, ports, body_dut, vdd, ibias, cl, fb, netmap,
                     f"tran {(t0 + 2 * t_edge) / 2000} {t0 + 2 * t_edge}\n"
                     "wrdata __OUT__ v(out)")
        a = _run(deck, ["v(out)"])
        if a is None or a.shape[0] < 10:
            continue
        t, vo = a[:, 0], a[:, 1]
        if abs(vo[0] - lo) > 0.4 * vdd:    # unity buffer must start near the low step
            continue
        rising = t < t0 + t_edge           # pulse falls back at t0 + t_edge
        if not _follows_pulse(vo[rising], vo[~rising], step):
            continue
        edges = [s for s in (_edge_slew(t[rising], vo[rising], vdd),
                             _edge_slew(t[~rising], vo[~rising], vdd))
                 if s is not None]
        if edges:
            return float(min(edges))
    return None


def _measure_swing(name, ports, body_dut, topo, vdd, ibias, cl, vcm, polarity=None):
    """Inverting −1 DC sweep → ``(swing_max, swing_min)`` in V.  FD:
    :func:`_measure_swing_fd`.

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
        return _measure_swing_fd(name, ports, body_dut, topo, vdd, ibias, cl,
                                 vcm, polarity)
    step = max(vdd / 200.0, 1e-3)
    for inp, inn in _pols(polarity):
        netmap = _fb_netmap(topo, inp, inn)
        fb = (f"Rfb out inn 10meg\nRin src inn 10meg\n"
              f"Vp inp 0 dc {vcm}\nVin src 0 dc {vcm}\n")
        deck = _deck(name, ports, body_dut, vdd, ibias, cl, fb, netmap,
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


def _measure_sr_fd(name, ports, body_dut, topo, vdd, ibias, cl, vcm,
                   polarity=None, sr_hint: float | None = None):
    """FD slew rate (V/s): a differential step through the gain −1 rig
    (:func:`_fd_inverting_fb`), the **min over the rising and falling edge of
    each output**.

    Per output, like the SE bench and like the sizer's ``ibias/Cc`` (each half
    slews its own Miller cap): the differential source steps 0.6·Vdd, so each
    output moves 0.3·Vdd — the same excursion as the SE unity-buffer step.  Pulse
    width scales with ``sr_hint`` as in :func:`_measure_sr`.
    """
    step = 0.3 * vdd
    t_edge = max(3.0 * step / sr_hint, 60e-9) if sr_hint else 1e-6
    t0 = 0.02 * t_edge
    src = f"Vd vd 0 pulse(0 {2 * step} {t0} 10p 10p {t_edge} 1)\n"
    for inp, inn in _pols(polarity):
        netmap = _fb_netmap(topo, inp, inn)
        deck = _deck(name, ports, body_dut, vdd, ibias, cl,
                     _fd_inverting_fb(vcm, src), netmap,
                     f"tran {(t0 + 2 * t_edge) / 2000} {t0 + 2 * t_edge}\n"
                     "wrdata __OUT__ v(outp) v(outn)")
        a = _run(deck, ["v(outp)", "v(outn)"])
        if a is None or a.shape[0] < 10 or a.shape[1] < 4:
            continue
        t, vp, vn = a[:, 0], a[:, 1], a[:, 3]
        # Negative feedback holds both outputs at the CM before the step; the
        # wrong polarity latches them apart.
        if abs(vp[0] - vcm) > 0.4 * vdd or abs(vn[0] - vcm) > 0.4 * vdd:
            continue
        first = t < t0 + t_edge            # pulse returns at t0 + t_edge
        edges = [s for vo in (vp, vn) for part in (first, ~first)
                 if (s := _edge_slew(t[part], vo[part], vdd)) is not None]
        if len(edges) == 4:
            return float(min(edges))
    return None


def _measure_swing_fd(name, ports, body_dut, topo, vdd, ibias, cl, vcm,
                      polarity=None):
    """FD output swing ``(swing_max, swing_min)`` in V, per output.

    DC-sweeps the differential source ``vd`` from −Vdd to +Vdd through the
    gain −1 rig (:func:`_fd_inverting_fb`), so ideally
    ``outp = Vocm − vd/2`` and ``outn`` its mirror, each crossing the whole
    supply.  The tracking region is the contiguous span around ``vd = 0``
    where **both** outputs still follow (absolute slope ≥ 0.7 of the ideal ½); at
    its edge the first output has saturated.  Reported per output, as the
    sizer predicts
    it (``Vdd − Vdsat`` / ``Vss + Vdsat`` of the output device): the lower of
    the two outputs' highs and the higher of their lows.
    """
    step = max(vdd / 200.0, 1e-3)
    src = "Vd vd 0 dc 0\n"
    for inp, inn in _pols(polarity):
        netmap = _fb_netmap(topo, inp, inn)
        deck = _deck(name, ports, body_dut, vdd, ibias, cl,
                     _fd_inverting_fb(vcm, src), netmap,
                     f"dc Vd {-vdd} {vdd} {step}\nwrdata __OUT__ v(outp) v(outn)")
        a = _run(deck, ["v(outp)", "v(outn)"])
        if a is None or a.shape[0] < 20 or a.shape[1] < 4:
            continue
        vd, vp, vn = a[:, 0], a[:, 1], a[:, 3]
        track = (np.gradient(vp, vd) <= -0.35) & (np.gradient(vn, vd) >= 0.35)
        icm = int(np.argmin(np.abs(vd)))
        if not track[icm]:
            continue   # does not track at CM → wrong polarity (latched)
        lo = hi = icm
        while lo > 0 and track[lo - 1]:
            lo -= 1
        while hi < len(track) - 1 and track[hi + 1]:
            hi += 1
        # outp peaks at the low-vd edge, outn at the high-vd edge.
        return (float(min(vp[lo], vn[hi])), float(max(vp[hi], vn[lo])))
    return None, None


def _measure_cmrr(name, ports, body_dut, topo, vdd, ibias, cl, vcm, polarity,
                  adm_db):
    """Single-ended common-mode rejection: ``CMRR = Adm − Acm`` in dB.

    Same DC feedback loop as the gain bench, but the 1 V AC rides on the
    ``cm`` node itself, i.e. on the input **common mode** (the inverting input
    receives it through ``Cfb``, the non-inverting one through the 0 V
    ``Vid``).  Fully-differential designs use :func:`_measure_fd_rejection`.
    """
    if adm_db is None or polarity is None:
        return None
    inp, inn = polarity
    netmap = _fb_netmap(topo, inp, inn)
    fb, outexpr = _loop_fb(topo, vcm, "Vid inp cm dc 0\n")
    fb = fb.replace(f"Vcm cm 0 {vcm}\n", f"Vcm cm 0 dc {vcm} ac 1\n")
    acm = _lf_mag(name, ports, body_dut, vdd, ibias, cl, fb, netmap, outexpr)
    if acm is None or acm <= 1e-12:   # numerical zero → nothing was measured
        return None
    return adm_db - 20.0 * np.log10(acm)


def _measure_psrr(name, ports, body_dut, topo, vdd, ibias, cl, vcm, polarity,
                  adm_db):
    """Single-ended positive-supply rejection: ``PSRR+ = Adm − Avdd`` in dB.

    Same DC feedback loop as the gain bench with a quiet input; the 1 V AC
    rides on VDD and the supply-to-output gain is read at low frequency.
    Fully-differential designs use :func:`_measure_fd_rejection`.
    """
    if adm_db is None or polarity is None:
        return None
    inp, inn = polarity
    netmap = _fb_netmap(topo, inp, inn)
    fb, outexpr = _loop_fb(topo, vcm, "Vid inp cm dc 0\n")
    avdd = _lf_mag(name, ports, body_dut, vdd, ibias, cl, fb, netmap, outexpr,
                   sup_ac=True)
    if avdd is None or avdd <= 1e-12:   # numerical zero → nothing was measured
        return None
    return adm_db - 20.0 * np.log10(avdd)


# Seeds of the PDK mismatch draws the FD rejection benches run; the worst
# draw is reported.
_MISMATCH_SEEDS = tuple(range(1, 9))
_SERVO_CLAMP_V = 0.05   # per-input limit on the FD offset servo's correction

# One run per draw: the DC differential output and output common mode (did
# the draw settle?), then the 1 mHz differential output for each 1 V AC
# stimulus in turn — input difference, input common mode, supply.
_FD_REJECTION_CONTROL = """op
let vchk=v(outp)-v(outn)
print vchk
let vocm=(v(outp)+v(outn))/2
print vocm
ac lin 1 1e-3 1e-3
let adm=mag(v(outp)-v(outn))
print adm
alter vdi acmag=0
alter vci acmag=1
ac lin 1 1e-3 1e-3
let acm=mag(v(outp)-v(outn))
print acm
alter vci acmag=0
alter vsup acmag=1
ac lin 1 1e-3 1e-3
let avdd=mag(v(outp)-v(outn))
print avdd"""


def _fd_servo_fb(vcm, sign: int) -> str:
    """FD rig whose inputs a DC servo moves to null the differential output.

    Mismatch gives the amplifier an input offset of a few mV, and its open-loop
    gain turns that into a railed differential output on the resistor-anchored
    gain bench (:func:`_loop_fb`).  Here the differential output is fed back to
    the inputs through a 1e12 s low-pass, so the servo cancels the offset at DC
    and is open at the 1 mHz measurement point.  Its correction is clamped to
    ``±_SERVO_CLAMP_V`` per input — ample for an mV offset — so it cannot drive
    the inputs out of range into a latched operating point.  ``sign`` picks the
    feedback direction (the FD polarity hint cannot tell it).  ``Vdi`` (input
    difference) carries the AC; ``Vci`` (input common mode) is quiet until
    :data:`_FD_REJECTION_CONTROL` moves the AC onto it.
    """
    lim = _SERVO_CLAMP_V
    corr = f"max(min({sign}*v(lp)/2,{lim}),-{lim})"
    return (f"Vocm ocm 0 {vcm}\nVci ci 0 dc 0\nVdi di 0 dc 0 ac 1\n"
            "Esrv vsrv 0 outp outn 1\nRsrv vsrv lp 1\nCsrv lp 0 1e12\n"
            f"Binp inp 0 v={vcm}+v(ci)+v(di)/2-{corr}\n"
            f"Binn inn 0 v={vcm}+v(ci)-v(di)/2+{corr}\n")


def _fd_draw(name, ports, body_dut, vdd, ibias, cl, vcm, netmap,
             sign) -> dict[str, float] | None:
    """One mismatch draw in the servo rig: ``vchk``/``vocm``/``adm``/``acm``/``avdd``.

    ``None`` when ngspice does not print all five (e.g. no ``.op``)."""
    deck = _deck(name, ports, body_dut, vdd, ibias, cl,
                 _fd_servo_fb(vcm, sign), netmap, _FD_REJECTION_CONTROL)
    txt = _run_capture(deck)
    if not txt:
        return None
    vals = dict(re.findall(r"^(vchk|vocm|adm|acm|avdd)\s*=\s*([-\d.eE+]+)",
                           txt, re.M))
    if len(vals) < 5:
        return None
    return {k: float(v) for k, v in vals.items()}


def _draw_settled(r: dict[str, float] | None, vdd: float) -> bool:
    """Whether a draw (:func:`_fd_draw`) settled at a live operating point.

    The servo must have nulled the difference to within an offset (a draw it
    cannot hold sits near a rail instead), and the output common mode must
    sit inside the ``0.1–0.9·Vdd`` window the FD bias check uses: both outputs
    collapsed to one rail, the all-off state some mismatch draws settle in,
    null the difference too but have almost no gain.
    """
    return (r is not None and abs(r["vchk"]) < 0.05 * vdd
            and 0.1 * vdd < r["vocm"] < 0.9 * vdd)


def _measure_fd_rejection(name, ports, mc_bodies, topo, vdd, ibias, cl, vcm,
                          polarity):
    """Fully-differential ``(cmrr_db, psrr_db, skipped)``: the worst over
    mismatch draws, and how many draws did not settle.

    A perfectly matched FD circuit turns neither input common mode nor supply
    ripple into a differential output — both halves move together — so its
    rejection is set by device mismatch alone.  Each body in ``mc_bodies`` is
    the DUT with one seeded PDK mismatch draw (``deck._dut(mismatch_seed=…)``).
    Per draw, the servo rig (:func:`_fd_servo_fb`) reads the 1 mHz differential
    output for a differential (Adm), common-mode (Acm) and supply (Avdd) 1 V
    stimulus at that draw's own operating point: ``CMRR = Adm/Acm`` and
    ``PSRR+ = Adm/Avdd``, in dB.  The minimum over the draws is reported; a
    draw that does not settle at a live operating point (:func:`_draw_settled`)
    with either servo direction is skipped and counted, and a metric with no
    usable draw is ``None``.
    """
    if polarity is None:
        return None, None, 0
    netmap = _fb_netmap(topo, *polarity)
    cmrr: list[float] = []
    psrr: list[float] = []
    signs = (1, -1)
    skipped = 0
    for body in mc_bodies:
        for sign in signs:
            r = _fd_draw(name, ports, body, vdd, ibias, cl, vcm, netmap, sign)
            if _draw_settled(r, vdd):
                break
        else:   # neither servo direction settled this draw
            skipped += 1
            continue
        signs = (sign, -sign)   # the direction is the circuit's, not the draw's
        for out, key in ((cmrr, "acm"), (psrr, "avdd")):
            if r["adm"] > 0 and r[key] > 1e-12:   # numerical zero → not measured
                out.append(20.0 * np.log10(r["adm"] / r[key]))
    return (min(cmrr) if cmrr else None, min(psrr) if psrr else None, skipped)
