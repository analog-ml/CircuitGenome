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


def _validate_mosfets(
    nmos: MosfetParams, pmos: MosfetParams, *, is_level1: bool
) -> None:
    """Reject silently-broken process params at load time (issue #158).

    gm/Id (LUT) techs may omit ``vth``/``mu_cox``/``lam`` entirely — the LUT
    supplies every operating-point voltage. Level-1/analytical techs (no
    ``gmid_lut``) require all three, with ``lam``/``mu_cox`` positive and the
    ``vth`` sign matching the polarity, so a bad fit can't be committed silently.
    """
    if not is_level1:
        # Only sanity-check a vth if one is present (it is optional here).
        if nmos.vth is not None and nmos.vth <= 0:
            raise ValueError(f"nmos.vth must be positive, got {nmos.vth}")
        if pmos.vth is not None and pmos.vth >= 0:
            raise ValueError(f"pmos.vth must be negative, got {pmos.vth}")
        return
    if nmos.vth is None or nmos.vth <= 0:
        raise ValueError(
            f"nmos.vth must be a positive number for a Level-1 tech "
            f"(no gmid_lut), got {nmos.vth}")
    if pmos.vth is None or pmos.vth >= 0:
        raise ValueError(
            f"pmos.vth must be a negative number for a Level-1 tech "
            f"(no gmid_lut), got {pmos.vth}")
    for name, p in (("nmos", nmos), ("pmos", pmos)):
        if p.mu_cox is None or p.mu_cox <= 0:
            raise ValueError(
                f"{name}.mu_cox must be a positive number for a Level-1 tech "
                f"(no gmid_lut), got {p.mu_cox}")
        if p.lam is None or p.lam <= 0:
            raise ValueError(
                f"{name}.lam must be a positive number for a Level-1 tech "
                f"(no gmid_lut), got {p.lam}")


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
            vth=float(d["vth"]) if "vth" in d else None,
            mu_cox=float(d["mu_cox"]) if "mu_cox" in d else None,
            lam=float(d["lam"]) if "lam" in d else None,
            gamma=float(d.get("gamma", 0.0)),
            phi=float(d.get("phi", 0.7)),
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
    device_handle = data.get("device_handle")
    if device_handle is not None:
        device_handle = {str(k): str(v) for k, v in device_handle.items()}

    # The nmos/pmos blocks are optional for gm/Id techs (they may carry only
    # gamma/phi, or nothing); Level-1 techs are caught by _validate_mosfets.
    nmos = _mosfet(data.get("nmos") or {})
    pmos = _mosfet(data.get("pmos") or {})
    _validate_mosfets(nmos, pmos, is_level1=gmid_lut is None)

    return TechParams(
        name=str(data["name"]),
        nmos=nmos,
        pmos=pmos,
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
        device_handle=device_handle,
        wl_units=str(data.get("wl_units", "m")),
    )
