"""Small SPICE-grounded calibrations applied after analytical sizing."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .models import SizingResult, SizingSpec, TechParams
from .verify import check_bias_soundness, ngspice_available, read_op_operating_point
from .verify.deck import _parse_subckt


@dataclass(frozen=True)
class ClassAbIqCalibration:
    """Result of :func:`calibrate_class_ab_iq`."""

    sizing: SizingResult
    target_iq_a: float
    measured_iq_a: float | None
    iterations: int
    converged: bool


def _class_ab_output_pair(netlist_text: str, result: SizingResult) -> tuple[str, str]:
    """Find a complementary follower or common-source push-pull pair."""
    _name, _ports, body = _parse_subckt(netlist_text)
    devices = {}
    for line in body:
        tok = line.split()
        if len(tok) >= 6 and tok[5].lower() in ("nmos", "pmos"):
            devices[tok[0]] = {
                "d": tok[1], "g": tok[2], "s": tok[3], "type": tok[5].lower()
            }
    rails = {"vdd!", "gnd!", "vss!", "0"}
    nmos = [ref for ref, d in devices.items()
            if ref in result.transistors and d["type"] == "nmos"]
    pmos = [ref for ref, d in devices.items()
            if ref in result.transistors and d["type"] == "pmos"]
    pairs = [
        (n, p) for n in nmos for p in pmos
        if ((devices[n]["s"] == devices[p]["s"]
             and devices[n]["d"] in rails and devices[p]["d"] in rails)
            or (devices[n]["d"] == devices[p]["d"]
                and devices[n]["s"] in rails and devices[p]["s"] in rails
                and devices[n]["g"] == devices[p]["g"]))
    ]
    if len(pairs) != 1:
        raise ValueError("netlist does not contain one identifiable static Class-AB output pair")
    return pairs[0]


def calibrate_class_ab_iq(
    netlist_text: str,
    initial: SizingResult,
    tech: TechParams,
    spec: SizingSpec,
    *,
    tolerance: float = 0.10,
    max_iterations: int = 5,
) -> ClassAbIqCalibration:
    """Calibrate the Class-AB output-pair widths against ngspice quiescent current.

    The analytical mirror ratio is a good seed, but short-channel output
    conductance and body effect make the output current differ from the diode
    reference.  A common multiplicative width correction preserves N/P balance
    while closing that model-to-SPICE error.  This is a local deterministic
    post-sizing step, not a global optimizer.
    """
    if not ngspice_available():
        raise RuntimeError("Class-AB IQ calibration requires ngspice on PATH")
    if not (0 < tolerance < 1):
        raise ValueError("tolerance must lie between 0 and 1")
    if max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")

    pair = _class_ab_output_pair(netlist_text, initial)
    target = spec.ibias * spec.output_stage_current_ratio
    if target <= 0:
        raise ValueError("Class-AB target IQ must be positive")
    current = initial
    measured = None
    for iteration in range(max_iterations + 1):
        op = read_op_operating_point(netlist_text, current, tech, spec)
        if op is None or any(ref not in op for ref in pair):
            break
        currents = [abs(op[ref].get("id", 0.0)) for ref in pair]
        measured = sum(currents) / len(currents)
        if measured > 0 and abs(measured / target - 1.0) <= tolerance:
            sound, _reason = check_bias_soundness(netlist_text, current, tech, spec)
            return ClassAbIqCalibration(current, target, measured, iteration, sound)
        if iteration == max_iterations or measured <= 0:
            break
        scale = target / measured
        transistors = dict(current.transistors)
        changed = False
        for ref in pair:
            old = transistors[ref]
            width = tech.width.snap(old.w_um * scale)
            changed |= width != old.w_um
            transistors[ref] = replace(old, w_um=width)
        current = replace(current, transistors=transistors)
        if not changed:
            break

    sound, _reason = check_bias_soundness(netlist_text, current, tech, spec)
    converged = bool(
        sound and measured is not None and measured > 0
        and abs(measured / target - 1.0) <= tolerance
    )
    return ClassAbIqCalibration(
        current, target, measured, min(max_iterations, iteration), converged
    )
