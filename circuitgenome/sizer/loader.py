"""Load technology parameters from a YAML config file."""
from __future__ import annotations
from pathlib import Path

import yaml

from .models import GridSpec, MosfetParams, TechParams

_BUILTIN_DIR = Path(__file__).parent / "config"


def load_tech(path: Path | str | None = None) -> TechParams:
    """Load :class:`~.models.TechParams` from a YAML file.

    :param path: Path to the technology YAML file. Defaults to the built-in
        ``tech_generic.yaml`` when ``None``.
    :returns: Parsed :class:`~.models.TechParams`.
    :raises FileNotFoundError: If the given path does not exist.
    :raises KeyError: If a required field is missing from the YAML.
    """
    if path is None:
        path = _BUILTIN_DIR / "tech_generic.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)

    def _mosfet(d: dict) -> MosfetParams:
        return MosfetParams(
            mu_cox=float(d["mu_cox"]),
            vth=float(d["vth"]),
            lam=float(d["lam"]),
        )

    def _grid(d: dict, min_key: str = "min", max_key: str = "max") -> GridSpec:
        return GridSpec(
            min=float(d[min_key]),
            max=float(d[max_key]),
            step=float(d["step"]),
        )

    cap_d = data["cap"]
    return TechParams(
        name=str(data["name"]),
        nmos=_mosfet(data["nmos"]),
        pmos=_mosfet(data["pmos"]),
        width=_grid(data["width"]),
        length=_grid(data["length"]),
        cap=GridSpec(
            min=float(cap_d["min_pf"]),
            max=float(cap_d["max_pf"]),
            step=float(cap_d["step_pf"]),
        ),
    )
