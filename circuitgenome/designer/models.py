"""Data models for the Designer module (Layer 4).

Plain dataclasses — they carry no logic and serialize straight into the
``report.json`` the designer writes next to the exported netlists.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DesignSolution:
    """One accepted design: a synthesized circuit whose sized, SPICE-measured
    performance meets every measurable spec.

    :param name: enumeration name (``circuit_0007``), matching the index the
        synthesizer CLI would assign, so solutions cross-reference
        ``cg synthesize`` output.
    :param topology: the :class:`~circuitgenome.synthesizer.models.TopologyTemplate` name.
    :param variants: slot name → chosen module-variant name.
    :param metrics: ngspice-measured metrics (``gain_db``, ``gbw_hz``,
        ``phase_margin_deg``, ``slew_rate_vps``, ``power_w``); ``None`` when a
        measurement could not be extracted.
    :param margins: normalized margin per constrained+measured spec
        (``(meas − min)/min`` for min-specs, ``(max − meas)/max`` for
        max-specs); every value is ≥ 0 by construction.
    :param worst_margin: the smallest margin (``inf`` when no measured spec
        constrains this design) — the robustness ranking key.
    :param netlist_path: the sized flat SPICE netlist written for this design.
    """
    name: str
    topology: str
    variants: dict[str, str]
    metrics: dict[str, float | None]
    margins: dict[str, float]
    worst_margin: float
    netlist_path: str


@dataclass
class TemplateStats:
    """Counts for one template's enumerate→size→verify run.

    ``enumerated`` counts the candidates actually evaluated (after ``limit``);
    the rejection counters partition ``enumerated − accepted`` by the pipeline
    stage that rejected the circuit.
    """
    template: str
    enumerated: int = 0
    sizing_failed: int = 0     # solver found no sizing
    bias_infeasible: int = 0   # analytical or SPICE DC bias check rejected
    spec_failed: int = 0       # simulated, but missed ≥ 1 measured spec
    errors: int = 0            # unexpected per-circuit exceptions
    accepted: int = 0
    runtime_s: float = 0.0


@dataclass
class DesignReport:
    """Output of :func:`~circuitgenome.designer.design`.

    :param spec: echo of the target :class:`~circuitgenome.sizer.SizingSpec` fields.
    :param tech: technology name the run sized and simulated against.
    :param solutions: every accepted design, in enumeration order.
    :param stats: per-template counters, keyed by topology name.
    :param best_points: criterion → the winning solution among ``solutions``
        (``highest_gain``, ``highest_gbw``, ``highest_phase_margin``,
        ``lowest_power``, ``most_robust``).
    :param unverified_specs: constrained spec fields the ngspice rig cannot
        measure (CMRR/PSRR/output swing) — solutions are *not* rejected on
        these; verify them separately.
    :param runtime_s: wall-clock time for the whole run.
    """
    spec: dict
    tech: str
    solutions: list[DesignSolution] = field(default_factory=list)
    stats: dict[str, TemplateStats] = field(default_factory=dict)
    best_points: dict[str, DesignSolution] = field(default_factory=dict)
    unverified_specs: list[str] = field(default_factory=list)
    runtime_s: float = 0.0
