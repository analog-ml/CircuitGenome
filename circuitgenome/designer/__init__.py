"""Designer module (Layer 4): spec-driven synthesis + sizing + verification.

:func:`design` chains the three lower layers end to end: enumerate every valid
circuit for the chosen template(s), size each with the gm/Id sizer, keep the
circuits whose ngspice-measured metrics meet the target spec, export the
survivors as sized flat SPICE netlists, and return a
:class:`~.models.DesignReport` with per-template statistics and the best
design points.

Typical usage::

    from circuitgenome.designer import design

    report = design("spec_gf180.yaml", "designs/",
                    templates=["two_stage_opamp_single_ended"],
                    limit=100, workers=4, progress=print)
    print(len(report.solutions), "designs meet the spec")
"""
from .designer import design
from .models import DesignReport, DesignSolution, TemplateStats

__all__ = [
    "design",
    "DesignReport",
    "DesignSolution",
    "TemplateStats",
]
