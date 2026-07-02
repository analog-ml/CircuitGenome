"""Load technology parameters and performance specs from YAML config files."""
from __future__ import annotations
from pathlib import Path

import yaml

from .models import GridSpec, MosfetParams, SizingSpec, SpiceLib, TechParams

_BUILTIN_DIR = Path(__file__).parent / "config"


def load_spec(path: Path | str) -> SizingSpec:
    """Load a :class:`~.models.SizingSpec` from a YAML file.

    Keys that are not ``SizingSpec`` fields are ignored, so a spec file may
    carry extra annotations (comments, provenance) without breaking loading.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    return SizingSpec(
        **{k: v for k, v in data.items() if k in SizingSpec.__dataclass_fields__})


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

    def _resolve(rel: str | None) -> str | None:
        if not rel:
            return None
        p = Path(rel)
        if not p.is_absolute():
            p = Path(path).parent / p
        return str(p)

    # Resolve relative to the config file's directory.
    spice_model = _resolve(data.get("spice_model"))
    gmid_lut = _resolve(data.get("gmid_lut"))

    spice_lib = None
    lib_d = data.get("spice_lib")
    if lib_d:
        spice_lib = SpiceLib(
            file=_resolve(lib_d["file"]),
            corner=str(lib_d.get("corner", "typical")),
            design=_resolve(lib_d.get("design")),
            corners=[str(c) for c in lib_d.get("corners", [])],
        )
    device_map = data.get("device_map")
    if device_map is not None:
        device_map = {str(k): str(v) for k, v in device_map.items()}

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
        gmid_lut=gmid_lut,
        spice_lib=spice_lib,
        device_map=device_map,
    )
