"""
Data models for the Initial Sizing module (Layer 3).

All structures are plain dataclasses — they carry no logic and can be freely
inspected or passed between pipeline stages.
"""
from __future__ import annotations
from dataclasses import dataclass, field


class UnsupportedTechError(ValueError):
    """Raised when a technology cannot be sized by the requested method.

    Currently raised when a PTM/SPICE-model node has no gm/Id LUT: such nodes
    must use the gm/Id pipeline, and the analytical (Level-1) sizer is not a
    valid fallback for them.
    """


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
    :param spice_model: Optional path (relative to the config dir, or absolute)
        to a SPICE ``.model`` card whose ``nmos``/``pmos`` models match the
        netlist (e.g. a BSIM4 ``.pm`` file). When ``None``, the SPICE-verification
        path synthesises a Level-1 model from ``mu_cox``/``vth``/``lam``.
    :param gmid_lut: Optional path (relative to the config dir, or absolute) to a
        committed gm/Id lookup table (``*_gmid.npz`` from
        ``tools/extract_tech.py --gm-id``). When present, sizing uses the
        procedural gm/Id path instead of the Level-1 analytical sizer.
    """
    name: str
    nmos: MosfetParams
    pmos: MosfetParams
    width: GridSpec
    length: GridSpec
    cap: GridSpec
    spice_model: str | None = None
    gmid_lut: str | None = None


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
    third_stage_current_ratio: float = 5.0
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
    :param warnings: Advisory messages, e.g. a likely ``--topology``/netlist
        mismatch. Empty when the netlist cleanly matches the topology.
    :param resistors: Sized load-resistor values in ohms, keyed by device
        reference (e.g. ``{"r1_load": 1.06e5}``). Empty when there are no
        sized resistors.
    :param bias_feasible: ``False`` when the gm/Id DC operating-point check finds
        a current source that cannot stay saturated (e.g. a cascode tail with no
        headroom) — the assumed bias current won't flow, so the frequency-domain
        metrics are optimistic. Always ``True`` for the Level-1 path.
    """
    transistors: dict[str, TransistorSizing]
    cc_pf: float | None
    metrics: dict[str, float]
    margins: dict[str, float]
    solver_status: str
    cc2_pf: float | None = None
    warnings: list[str] = field(default_factory=list)
    resistors: dict[str, float] = field(default_factory=dict)
    bias_feasible: bool = True
