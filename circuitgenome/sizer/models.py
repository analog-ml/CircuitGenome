"""
Data models for the Initial Sizing module (Layer 3).

All structures are plain dataclasses — they carry no logic and can be freely
inspected or passed between pipeline stages.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MosfetParams:
    """CMOS process parameters for one device polarity.

    :param mu_cox: Process transconductance parameter µ·Cox in A/V².
    :param vth: Threshold voltage in V (positive for NMOS, negative for PMOS).
    :param lam: Channel-length modulation coefficient λ in 1/V (always positive).
    """
    mu_cox: float
    vth: float
    lam: float


@dataclass
class GridSpec:
    """Discrete geometry or capacitance grid.

    :param min: Minimum value (µm for W/L, pF for cap).
    :param max: Maximum value.
    :param step: Discretisation step.
    """
    min: float
    max: float
    step: float


@dataclass
class TechParams:
    """Process technology parameters loaded from a YAML config file.

    :param name: Technology identifier (e.g. ``"generic_parameterized"``).
    :param nmos: NMOS Shichman-Hodges parameters.
    :param pmos: PMOS Shichman-Hodges parameters.
    :param width: Transistor width grid in µm.
    :param length: Transistor length grid in µm.
    :param cap: Compensation capacitor grid in pF.
    """
    name: str
    nmos: MosfetParams
    pmos: MosfetParams
    width: GridSpec
    length: GridSpec
    cap: GridSpec


@dataclass
class SizingSpec:
    """Performance specification for initial sizing.

    All performance bounds are optional — ``None`` means unconstrained.

    :param vdd: Positive supply voltage in V.
    :param vss: Negative supply voltage in V (0.0 for single supply).
    :param ibias: External input bias current in A.
    :param cl: Output load capacitance in F.
    :param second_stage_current_ratio: Second-stage quiescent current as a
        multiple of ``ibias``  (``iDS_2 = ratio × ibias``). Default 2.0.
    :param gain_min_db: Minimum open-loop DC gain in dB.
    :param gbw_min_hz: Minimum unity-gain bandwidth in Hz.
    :param phase_margin_min_deg: Minimum phase margin in degrees.
    :param slew_rate_min_vps: Minimum slew rate in V/s.
    :param power_max_w: Maximum total quiescent power in W.
    :param output_swing_max_v: Maximum output voltage in V (absolute, e.g. 4.6 for a 5V supply).
    :param output_swing_min_v: Minimum output voltage in V (absolute, e.g. 0.4).
    :param cmrr_min_db: Minimum CMRR in dB.
    :param psrr_min_db: Minimum PSRR in dB (positive supply, approximate).
    """
    vdd: float
    vss: float
    ibias: float
    cl: float
    second_stage_current_ratio: float = 2.0
    gain_min_db: float | None = None
    gbw_min_hz: float | None = None
    phase_margin_min_deg: float | None = None
    slew_rate_min_vps: float | None = None
    power_max_w: float | None = None
    output_swing_max_v: float | None = None
    output_swing_min_v: float | None = None
    cmrr_min_db: float | None = None
    psrr_min_db: float | None = None


@dataclass
class TransistorSizing:
    """Sizing result for a single transistor.

    :param ref: Device reference in the netlist (e.g. ``"m1_input_pair"``).
    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm.
    :param ids_a: Quiescent drain-source current in A.
    :param vgs_v: Quiescent gate-source voltage in V.
    :param vds_sat_v: Minimum \|VDS\| for saturation in V.
    """
    ref: str
    w_um: float
    l_um: float
    ids_a: float
    vgs_v: float
    vds_sat_v: float


@dataclass
class SizingResult:
    """Output of :func:`~circuitgenome.sizer.sizer.size_circuit`.

    :param transistors: Per-transistor sizing keyed by device reference.
    :param cc_pf: Compensation capacitor value in pF, or ``None`` for single-stage.
    :param metrics: Computed performance metrics, e.g.
        ``{"gain_db": 90.1, "gbw_hz": 3.0e6, ...}``.
    :param margins: Safety margin for each constrained spec (actual/spec for
        min specs, spec/actual for max specs). Values > 1 mean spec is met.
    :param solver_status: OR-Tools CP-SAT status string:
        ``"OPTIMAL"``, ``"FEASIBLE"``, ``"INFEASIBLE"``, or ``"UNKNOWN"``.
    """
    transistors: dict[str, TransistorSizing]
    cc_pf: float | None
    metrics: dict[str, float]
    margins: dict[str, float]
    solver_status: str
