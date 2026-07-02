"""Public entry point: run every metric testbench on a sized circuit."""
from __future__ import annotations

from ..models import SizingResult, SizingSpec, TechParams
from .deck import _dut, _inject_sizes, _parse_subckt
from .measure import _measure_ac, _measure_power, _measure_sr
from .op import _bias_diagnostic
from .rig import _Topo


def simulate_metrics(netlist_text: str, result: SizingResult,
                     tech: TechParams, spec: SizingSpec,
                     corner: str | None = None) -> dict[str, float | None]:
    """Return SPICE-measured metrics, mirroring ``_evaluate_metrics`` keys.

    Keys: ``power_w``, ``gain_db``, ``gbw_hz``, ``phase_margin_deg``,
    ``slew_rate_vps``, ``output_swing_max_v``, ``output_swing_min_v``.
    Missing/failed measurements are ``None``.  ``corner`` overrides the PDK
    library corner (foundry techs only); ``None`` uses the tech's nominal corner.
    """
    name, ports, body = _parse_subckt(netlist_text)
    body = _inject_sizes(body, result)
    body_dut = _dut(tech, name, body, corner)
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
