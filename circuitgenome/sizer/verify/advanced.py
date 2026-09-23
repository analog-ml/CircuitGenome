"""Specialized ngspice characterization for experimental analog blocks."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import re
from typing import Sequence

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
from .rig import _deck, _fb_netmap, _Topo
from .measure import _edge_slew, _measure_ac


@dataclass(frozen=True)
class RailToRailVcmPoint:
    """One DC point from :func:`sweep_rail_to_rail_vcm`."""

    vcm_v: float
    output_v: float | None
    n_pair_current_a: float | None
    p_pair_current_a: float | None
    gm_total_s: float | None
    converged: bool


@dataclass(frozen=True)
class ClassAbLoadPoint:
    """One loaded AC/transient point for a static Class-AB output stage."""

    load_cap_f: float
    gain_db: float | None
    gbw_hz: float | None
    phase_margin_deg: float | None
    rise_slew_vps: float | None
    fall_slew_vps: float | None
    source_peak_a: float | None
    sink_peak_a: float | None
    rise_tracking_fraction: float | None
    fall_tracking_fraction: float | None
    rise_overshoot_v: float | None
    fall_undershoot_v: float | None
    settled: bool
    converged: bool


@dataclass(frozen=True)
class ClassAbLinearityPoint:
    """One closed-loop sine/THD point for the static Class-AB stage."""

    load_cap_f: float
    amplitude_v: float
    frequency_hz: float
    output_fundamental_v: float | None
    thd_percent: float | None
    crossover_residual_v: float | None
    converged: bool


@dataclass(frozen=True)
class ClassAbPvtPoint:
    """One voltage/temperature/process point for the Class-AB topology."""

    corner: str
    vdd_v: float
    temperature_c: float
    iq_a: float | None
    gain_db: float | None
    gbw_hz: float | None
    phase_margin_deg: float | None
    thd_percent: float | None
    crossover_residual_v: float | None
    converged: bool
    passed: bool


def _input_pair_refs(body: list[str]) -> dict[str, list[str]]:
    refs = {"nmos": [], "pmos": []}
    for line in body:
        tok = line.split()
        if (len(tok) >= 6 and tok[5].lower() in refs
                and tok[2] in ("in1", "in2")):
            refs[tok[5].lower()].append(tok[0])
    if any(len(group) != 2 for group in refs.values()):
        raise ValueError(
            "netlist does not contain one complementary two-pair input stage"
        )
    return refs


def _class_ab_pair_refs(
    body: list[str], sizing: SizingResult
) -> tuple[str, str]:
    """Return the high-side NMOS and low-side PMOS source followers."""
    devices = {}
    for line in body:
        tok = line.split()
        if len(tok) >= 6 and tok[5].lower() in _MOS_MODELS:
            devices[tok[0]] = {
                "d": tok[1], "g": tok[2], "s": tok[3],
                "type": tok[5].lower(),
            }
    rails = {"vdd!", "gnd!", "vss!", "0"}
    nmos = [ref for ref, device in devices.items()
            if ref in sizing.transistors and device["type"] == "nmos"]
    pmos = [ref for ref, device in devices.items()
            if ref in sizing.transistors and device["type"] == "pmos"]
    pairs = [
        (nref, pref) for nref in nmos for pref in pmos
        if ((devices[nref]["s"] == devices[pref]["s"]
             and devices[nref]["d"] in rails and devices[pref]["d"] in rails)
            or (devices[nref]["d"] == devices[pref]["d"]
                and devices[nref]["s"] in rails
                and devices[pref]["s"] in rails
                and devices[nref]["g"] == devices[pref]["g"]))
    ]
    if len(pairs) != 1:
        raise ValueError(
            "netlist does not contain one identifiable static Class-AB output pair"
        )
    return pairs[0]


def characterize_class_ab_linearity(
    netlist_text: str,
    sizing: SizingResult,
    tech: TechParams,
    spec: SizingSpec,
    *,
    amplitudes_v: Sequence[float] = (0.025, 0.05, 0.10, 0.15),
    load_cap_values: Sequence[float] | None = None,
    frequency_hz: float = 100e3,
    harmonics: int = 5,
    corner: str | None = None,
    temperature_c: float | None = None,
) -> tuple[ClassAbLinearityPoint, ...]:
    """Measure closed-loop THD and the nonlinear residual at zero crossings.

    The sine is centred on ``spec.vcm``.  Eight initial cycles are discarded;
    the following eight are uniformly resampled before an FFT.  Crossover
    residual is measured after removing the best-fit DC + fundamental sine,
    so ordinary closed-loop gain and phase error are not misreported as
    crossover distortion.
    """
    if not ngspice_available():
        raise RuntimeError("Class-AB linearity characterization requires ngspice")
    amplitudes = tuple(amplitudes_v)
    loads = tuple(load_cap_values) if load_cap_values is not None else (spec.cl,)
    if (not amplitudes or any(value <= 0 for value in amplitudes)
            or any(spec.vcm - value <= spec.vss
                   or spec.vcm + value >= spec.vdd for value in amplitudes)):
        raise ValueError("amplitudes_v must be positive and remain inside the rails")
    if not loads or any(value <= 0 for value in loads):
        raise ValueError("load_cap_values must contain positive capacitances")
    if frequency_hz <= 0 or harmonics < 2:
        raise ValueError("frequency_hz must be positive and harmonics >= 2")

    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd or "out" not in ports:
        raise ValueError("Class-AB linearity currently requires a single-ended DUT")
    _class_ab_pair_refs(body, sizing)  # reject a non-Class-AB netlist early
    body_dut = _dut(tech, name, _inject_sizes(body, sizing), corner)
    args = (name, ports, body_dut, topo, spec.vdd, spec.ibias, spec.vcm)
    period = 1.0 / frequency_hz
    settle_cycles, measure_cycles = 8, 8
    tstart = settle_cycles * period
    tstop = (settle_cycles + measure_cycles) * period
    sample_count = 4096
    points: list[ClassAbLinearityPoint] = []

    for load in loads:
        _gain, _gbw, _pm, _reason, polarity = _measure_ac(
            *args, load_cap_f=load, temperature_c=temperature_c)
        polarities = ((polarity,) if polarity is not None else ()) + tuple(
            item for item in (("in1", "in2"), ("in2", "in1"))
            if item != polarity
        )
        for amplitude in amplitudes:
            candidates = []
            for inp, inn in polarities:
                netmap = _fb_netmap(topo, inp, inn)
                feedback = (
                    "Rfb out inn 1\n"
                    f"Vsin inp 0 sin({spec.vcm} {amplitude} {frequency_hz})\n"
                    f"Cload out 0 {load}\n"
                )
                control = (
                    f"tran {period / 256.0} {tstop} {tstart}\n"
                    "wrdata __OUT__ v(inp) v(out)"
                )
                data = _run(
                    _deck(name, ports, body_dut, spec.vdd, spec.ibias,
                          feedback, netmap, control,
                          temperature_c=temperature_c),
                    ["v(inp)", "v(out)"],
                )
                if data is None or data.shape[0] < 100 or data.shape[1] < 4:
                    continue
                time, vin, vout = data[:, 0], data[:, 1], data[:, 3]
                uniform_t = np.linspace(tstart, tstop, sample_count, endpoint=False)
                uniform_in = np.interp(uniform_t, time, vin)
                uniform_out = np.interp(uniform_t, time, vout)
                phase = 2.0 * np.pi * frequency_hz * uniform_t
                design = np.column_stack((
                    np.ones(sample_count), np.sin(phase), np.cos(phase),
                ))
                coeff, *_ = np.linalg.lstsq(design, uniform_out, rcond=None)
                fundamental = float(np.hypot(coeff[1], coeff[2]))
                fitted = design @ coeff
                residual = uniform_out - fitted
                crossing = np.abs(uniform_in - spec.vcm) <= 0.02 * amplitude
                crossover = (float(np.max(np.abs(residual[crossing])))
                             if np.any(crossing) else None)

                spectrum = np.fft.rfft(uniform_out - np.mean(uniform_out))
                fundamental_bin = measure_cycles
                fundamental_fft = abs(spectrum[fundamental_bin])
                harmonic_bins = [fundamental_bin * order
                                 for order in range(2, harmonics + 1)
                                 if fundamental_bin * order < len(spectrum)]
                if fundamental_fft <= 0 or not harmonic_bins:
                    continue
                thd = 100.0 * float(np.sqrt(sum(
                    abs(spectrum[index]) ** 2 for index in harmonic_bins
                )) / fundamental_fft)
                converged = bool(
                    fundamental >= 0.8 * amplitude
                    and fundamental <= 1.2 * amplitude
                    and crossover is not None
                    and np.isfinite(thd)
                )
                candidates.append((converged, fundamental, thd, crossover))
            if candidates:
                converged, fundamental, thd, crossover = max(
                    candidates, key=lambda value: (value[0], value[1])
                )
                points.append(ClassAbLinearityPoint(
                    load, amplitude, frequency_hz, fundamental,
                    thd, crossover, converged,
                ))
            else:
                points.append(ClassAbLinearityPoint(
                    load, amplitude, frequency_hz,
                    None, None, None, False,
                ))
    return tuple(points)


def sweep_class_ab_pvt(
    netlist_text: str,
    sizing: SizingResult,
    tech: TechParams,
    spec: SizingSpec,
    *,
    supply_values: Sequence[float] | None = None,
    temperatures_c: Sequence[float] = (-40.0, 27.0, 125.0),
    corners: Sequence[str] | None = None,
    amplitude_v: float = 0.10,
    frequency_hz: float = 100e3,
    thd_max_percent: float = 1.0,
    crossover_max_v: float = 2e-3,
    iq_tolerance: float = 0.30,
) -> tuple[ClassAbPvtPoint, ...]:
    """Sweep fixed geometry over process, supply and temperature.

    PTM/card technologies without a corner library are honestly reported as
    one ``nominal`` process model; foundry technologies use their configured
    corner list unless ``corners`` is supplied explicitly.
    """
    if not ngspice_available():
        raise RuntimeError("Class-AB PVT sweep requires ngspice")
    supplies = tuple(supply_values) if supply_values is not None else tuple(
        spec.vdd * scale for scale in (0.9, 1.0, 1.1)
    )
    temperatures = tuple(temperatures_c)
    if not supplies or any(value <= spec.vss for value in supplies):
        raise ValueError("supply_values must exceed VSS")
    if not temperatures:
        raise ValueError("temperatures_c must not be empty")
    if corners is not None and not tech.spice_lib:
        raise ValueError("this technology has no process-corner library")
    process_corners: tuple[str | None, ...]
    if corners is not None:
        process_corners = tuple(corners)
    elif tech.spice_lib:
        process_corners = tuple(tech.spice_lib.corners) or (tech.spice_lib.corner,)
    else:
        process_corners = (None,)

    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd or "out" not in ports:
        raise ValueError("Class-AB PVT sweep currently requires a single-ended DUT")
    nref, pref = _class_ab_pair_refs(body, sizing)
    models = {
        tok[0]: tok[5].lower() for line in body
        if len(tok := line.split()) >= 6 and tok[5].lower() in _MOS_MODELS
    }
    prefixes = {
        ref: _dev_prefix(tech, ref, models[ref]) for ref in (nref, pref)
    }
    target_iq = spec.ibias * spec.output_stage_current_ratio
    points: list[ClassAbPvtPoint] = []

    for corner in process_corners:
        body_dut = _dut(tech, name, _inject_sizes(body, sizing), corner)
        for vdd in supplies:
            condition_spec = replace(spec, vdd=vdd)
            args = (name, ports, body_dut, topo, vdd, spec.ibias,
                    condition_spec.vcm)
            for temperature in temperatures:
                gain, gbw, pm, ac_reason, polarity = _measure_ac(
                    *args, load_cap_f=spec.cl, temperature_c=temperature)
                polarities = ((polarity,) if polarity is not None else ()) + tuple(
                    item for item in (("in1", "in2"), ("in2", "in1"))
                    if item != polarity
                )
                iq_candidates = []
                for inp, inn in polarities:
                    netmap = _fb_netmap(topo, inp, inn)
                    feedback = (
                        f"Vcm cm 0 {condition_spec.vcm}\n"
                        "Lfb out inn 1e12\nCfb inn cm 1e3\n"
                        "Vid inp cm dc 0\n"
                    )
                    probes = "\n".join(
                        f"print {prefix}[id]" for prefix in prefixes.values()
                    )
                    deck = _deck(
                        name, ports, body_dut, vdd, spec.ibias,
                        feedback, netmap,
                        "op\nprint v(out)\n" + probes,
                        temperature_c=temperature,
                    )
                    text = _run_capture(deck)
                    if text is None:
                        continue
                    match = re.search(r"v\(out\)\s*=\s*([-\d.eE+]+)", text)
                    currents = [
                        _probe_value(text, prefix, "id")
                        for prefix in prefixes.values()
                    ]
                    if not match or any(value is None for value in currents):
                        continue
                    output = float(match.group(1))
                    iq = sum(abs(value) for value in currents) / len(currents)
                    iq_candidates.append((
                        abs(output - condition_spec.vcm), iq,
                    ))
                iq = (min(iq_candidates, key=lambda item: item[0])[1]
                      if iq_candidates else None)
                linearity = characterize_class_ab_linearity(
                    netlist_text, sizing, tech, condition_spec,
                    amplitudes_v=(amplitude_v,),
                    load_cap_values=(spec.cl,),
                    frequency_hz=frequency_hz,
                    corner=corner,
                    temperature_c=temperature,
                )[0]
                converged = bool(
                    iq is not None and ac_reason is None
                    and gain is not None and gbw is not None and pm is not None
                    and linearity.converged
                )
                passed = bool(
                    converged
                    and abs(iq / target_iq - 1.0) <= iq_tolerance
                    and (spec.gain_min_db is None or gain >= spec.gain_min_db)
                    and (spec.gbw_min_hz is None or gbw >= spec.gbw_min_hz)
                    and (spec.phase_margin_min_deg is None
                         or pm >= spec.phase_margin_min_deg)
                    and linearity.thd_percent <= thd_max_percent
                    and linearity.crossover_residual_v <= crossover_max_v
                )
                points.append(ClassAbPvtPoint(
                    corner or "nominal", vdd, temperature, iq,
                    gain, gbw, pm, linearity.thd_percent,
                    linearity.crossover_residual_v, converged, passed,
                ))
    return tuple(points)


def characterize_class_ab_loads(
    netlist_text: str,
    sizing: SizingResult,
    tech: TechParams,
    spec: SizingSpec,
    *,
    load_cap_values: Sequence[float] = (
        0.5e-12, 1e-12, 2e-12, 5e-12, 10e-12,
    ),
) -> tuple[ClassAbLoadPoint, ...]:
    """Measure loaded Class-AB stability and bidirectional large-signal drive.

    Each point uses the requested capacitor in both the loop-gain and unity-
    follower transient benches.  Source/sink peaks are the actual output-pair
    device currents, not an estimate inferred from ``CL * SR``.
    """
    if not ngspice_available():
        raise RuntimeError("Class-AB load characterization requires ngspice on PATH")
    loads = tuple(load_cap_values)
    if not loads or any(value <= 0 for value in loads):
        raise ValueError("load_cap_values must contain positive capacitances")

    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd or "out" not in ports:
        raise ValueError("Class-AB load characterization requires a single-ended DUT")
    nref, pref = _class_ab_pair_refs(body, sizing)
    models = {
        tok[0]: tok[5].lower() for line in body
        if len(tok := line.split()) >= 6 and tok[5].lower() in _MOS_MODELS
    }
    nprefix = _dev_prefix(tech, nref, models[nref])
    pprefix = _dev_prefix(tech, pref, models[pref])
    device_nodes = {
        tok[0]: {"d": tok[1], "s": tok[3]}
        for line in body if len(tok := line.split()) >= 6
    }
    common_source_pair = device_nodes[nref]["d"] == device_nodes[pref]["d"]
    body_dut = _dut(tech, name, _inject_sizes(body, sizing))
    args = (name, ports, body_dut, topo, spec.vdd, spec.ibias, spec.vcm)
    step = 0.30 * (spec.vdd - spec.vss)
    if step <= 0:
        raise ValueError("VDD must exceed VSS")
    sr_hint = spec.slew_rate_min_vps
    t_edge = max(3.0 * step / sr_hint, 100e-9) if sr_hint else 1e-6
    t0 = 0.02 * t_edge
    tstop = t0 + 2.0 * t_edge
    low_target = spec.vcm - step / 2.0
    high_target = spec.vcm + step / 2.0
    if low_target <= spec.vss or high_target >= spec.vdd:
        raise ValueError("Class-AB step targets must lie inside the supply rails")

    points: list[ClassAbLoadPoint] = []
    for load in loads:
        gain, gbw, pm, ac_reason, polarity = _measure_ac(
            *args, load_cap_f=load)
        candidates = []
        polarities = ((polarity,) if polarity is not None else ()) + tuple(
            item for item in (("in1", "in2"), ("in2", "in1"))
            if item != polarity
        )
        for inp, inn in polarities:
            netmap = _fb_netmap(topo, inp, inn)
            feedback = (
                "Rfb out inn 1\n"
                f"Vstep inp 0 pulse({low_target} {high_target} "
                f"{t0} 10p 10p {t_edge} {tstop})\n"
                f"Cload out 0 {load}\n"
            )
            control = (
                f"save all {nprefix}[id] {pprefix}[id]\n"
                f"tran {tstop / 4000.0} {tstop}\n"
                f"wrdata __OUT__ v(out) {nprefix}[id] {pprefix}[id]"
            )
            data = _run_capture_or_table(
                _deck(name, ports, body_dut, spec.vdd, spec.ibias,
                      feedback, netmap, control)
            )
            if data is None or data.shape[0] < 20 or data.shape[1] < 6:
                continue
            time, output = data[:, 0], data[:, 1]
            n_current, p_current = data[:, 3], data[:, 5]
            rise_mask = time < t0 + t_edge
            fall_mask = time >= t0 + t_edge
            rise = _edge_slew(time[rise_mask], output[rise_mask], spec.vdd)
            fall = _edge_slew(time[fall_mask], output[fall_mask], spec.vdd)
            if rise is None or fall is None:
                continue
            rise_active = (time >= t0) & (time < t0 + t_edge)
            fall_active = fall_mask
            source_current = p_current if common_source_pair else n_current
            sink_current = n_current if common_source_pair else p_current
            source_peak = float(np.max(np.abs(source_current[rise_active])))
            sink_peak = float(np.max(np.abs(sink_current[fall_active])))
            rise_overshoot = max(0.0, float(np.max(output[rise_active])) - high_target)
            fall_undershoot = max(0.0, low_target - float(np.min(output[fall_active])))
            tail = max(4, int(0.05 * data.shape[0]))
            high_slice = output[rise_mask][-tail:]
            low_slice = output[-tail:]
            initial_slice = output[time < t0]
            initial_level = float(np.median(initial_slice))
            high_level = float(np.median(high_slice))
            low_level = float(np.median(low_slice))
            rise_fraction = (high_level - initial_level) / step
            fall_fraction = (high_level - low_level) / step
            tolerance = 0.05 * step
            settled = bool(
                abs(high_level - high_target) <= tolerance
                and abs(low_level - low_target) <= tolerance
            )
            candidates.append((
                settled, rise, fall, source_peak, sink_peak,
                rise_fraction, fall_fraction, rise_overshoot, fall_undershoot,
            ))
        if candidates:
            best = max(
                candidates, key=lambda value: (value[0], min(value[5], value[6]))
            )
            (settled, rise, fall, source, sink, rise_fraction,
             fall_fraction, over, under) = best
            converged = bool(settled and ac_reason is None and gbw is not None
                             and pm is not None)
            points.append(ClassAbLoadPoint(
                load, gain, gbw, pm, rise, fall, source, sink,
                rise_fraction, fall_fraction,
                over, under, settled, converged,
            ))
        else:
            points.append(ClassAbLoadPoint(
                load, gain, gbw, pm, None, None, None, None,
                None, None, None, None, False, False,
            ))
    return tuple(points)


def _run_capture_or_table(deck: str) -> np.ndarray | None:
    """Run a transient table while keeping advanced.py's public API compact."""
    return _run(deck, ["v(out)", "i(nout)", "i(pout)"])


def _probe_value(text: str, prefix: str, parameter: str) -> float | None:
    match = re.search(
        re.escape(prefix) + rf"\[{parameter}\]\s*=\s*([-\d.eE+]+)", text
    )
    return float(match.group(1)) if match else None


def sweep_rail_to_rail_vcm(
    netlist_text: str,
    sizing: SizingResult,
    tech: TechParams,
    spec: SizingSpec,
    *,
    vcm_values: Sequence[float] | None = None,
    output_common_mode_v: float | None = None,
) -> tuple[RailToRailVcmPoint, ...]:
    """Sweep complementary-input operation independently of output swing.

    A level-shifted unity-feedback loop holds the output at
    ``output_common_mode_v`` while both inputs settle at each requested VCM.
    This separates input common-mode coverage from output swing.  The reported
    pair currents are sums over the two devices of each polarity; ``gm_total``
    is the average per-branch NMOS gm plus the average per-branch PMOS gm.
    """
    if not ngspice_available():
        raise RuntimeError("rail-to-rail VCM sweep requires ngspice on PATH")
    name, ports, body = _parse_subckt(netlist_text)
    topo = _Topo(ports)
    if topo.fd or "out" not in ports:
        raise ValueError("rail-to-rail VCM sweep currently requires a single-ended DUT")
    refs = _input_pair_refs(body)
    missing = [r for group in refs.values() for r in group
               if r not in sizing.transistors]
    if missing:
        raise ValueError("input devices are not sized: " + ", ".join(missing))

    values = tuple(vcm_values) if vcm_values is not None else tuple(
        spec.vss + frac * (spec.vdd - spec.vss)
        for frac in (0.05, 0.25, 0.50, 0.75, 0.95)
    )
    if not values:
        raise ValueError("vcm_values must not be empty")
    if any(v < spec.vss or v > spec.vdd for v in values):
        raise ValueError("VCM sweep point lies outside the supply rails")
    out_cm = spec.vcm if output_common_mode_v is None else output_common_mode_v
    if not spec.vss < out_cm < spec.vdd:
        raise ValueError("output_common_mode_v must lie strictly inside the rails")

    body_dut = _dut(tech, name, _inject_sizes(body, sizing))
    models = {tok[0]: tok[5].lower() for line in body
              if len(tok := line.split()) >= 6 and tok[5].lower() in _MOS_MODELS}
    prefixes = {
        ref: _dev_prefix(tech, ref, models[ref])
        for group in refs.values() for ref in group
    }
    probes = "\n".join(
        command
        for prefix in prefixes.values()
        for command in (f"print {prefix}[id]", f"print {prefix}[gm]")
    )

    points: list[RailToRailVcmPoint] = []
    for vcm in values:
        candidates = []
        offset = out_cm - vcm
        for inp, inn in (("in1", "in2"), ("in2", "in1")):
            netmap = _fb_netmap(topo, inp, inn)
            feedback = (
                f"Vcm cm 0 {vcm}\n"
                "Lfb out fb 1e12\n"
                f"Vshift fb inn dc {offset}\n"
                "Cfb inn cm 1e3\n"
                "Vid inp cm dc 0\n"
            )
            deck = _deck(
                name, ports, body_dut, spec.vdd, spec.ibias,
                feedback, netmap, "op\nprint v(out)\n" + probes,
            )
            text = _run_capture(deck)
            if text is None:
                continue
            match = re.search(r"v\(out\)\s*=\s*([-\d.eE+]+)", text)
            if not match:
                continue
            output = float(match.group(1))
            currents: dict[str, float] = {}
            gms: dict[str, float] = {}
            for ref, prefix in prefixes.items():
                current = _probe_value(text, prefix, "id")
                gm = _probe_value(text, prefix, "gm")
                if current is not None:
                    currents[ref] = abs(current)
                if gm is not None:
                    gms[ref] = abs(gm)
            if len(currents) != 4 or len(gms) != 4:
                continue
            n_i = sum(currents[r] for r in refs["nmos"])
            p_i = sum(currents[r] for r in refs["pmos"])
            gm_total = (
                sum(gms[r] for r in refs["nmos"]) / 2.0
                + sum(gms[r] for r in refs["pmos"]) / 2.0
            )
            candidates.append((abs(output - out_cm), output, n_i, p_i, gm_total))
        if not candidates:
            points.append(RailToRailVcmPoint(vcm, None, None, None, None, False))
            continue
        error, output, n_i, p_i, gm_total = min(candidates, key=lambda item: item[0])
        points.append(RailToRailVcmPoint(
            vcm, output, n_i, p_i, gm_total,
            error <= 0.05 * (spec.vdd - spec.vss),
        ))
    return tuple(points)
