"""Load technology parameters from a YAML config file."""
from __future__ import annotations
from pathlib import Path

import yaml

from .models import GridSpec, MosfetParams, TechParams

_BUILTIN_DIR = Path(__file__).parent / "config"


def load_tech(path: Path | str | None = None) -> TechParams:
    """Load :class:`~.models.TechParams` from a YAML file.

    :param path: Path to the technology YAML file, or the short name of a
        built-in config (e.g. ``"ptm45"``, ``"generic"``, resolving to the
        bundled ``tech_<name>.yaml``). Defaults to the built-in
        ``tech_generic.yaml`` when ``None``.
    :returns: Parsed :class:`~.models.TechParams`.
    :raises FileNotFoundError: If the given path does not exist and is not a
        built-in config name.
    :raises KeyError: If a required field is missing from the YAML.
    """
    if path is None:
        path = _BUILTIN_DIR / "tech_generic.yaml"
    elif not Path(path).exists():
        # Not a real path → treat as a built-in config name ("ptm45" → tech_ptm45.yaml).
        builtin = _BUILTIN_DIR / f"tech_{Path(path).name}.yaml"
        if builtin.exists():
            path = builtin
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
    spice_model = data.get("spice_model")
    if spice_model:
        # Resolve relative to the config file's directory.
        sm = Path(spice_model)
        if not sm.is_absolute():
            sm = Path(path).parent / sm
        spice_model = str(sm)
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
        spice_model=spice_model,
    )
