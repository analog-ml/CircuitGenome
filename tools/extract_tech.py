#!/usr/bin/env python3
"""Extract Level-1 sizing parameters (mu_cox, vth, lam) from a BSIM4 card.

The CircuitGenome sizer uses a Level-1 square-law MOSFET model
(``gm = sqrt(2*mu_cox*(W/L)*IDS)``, ``gd = lam*IDS``).  Real PDK / predictive
models (here: ASU Predictive Technology Models, BSIM4 ``level=54``) carry
hundreds of parameters instead.  This tool fits *effective* Level-1 equivalents
by simulating the actual BSIM4 device in ngspice and fitting:

* **vth / mu_cox** from a transfer sweep (``Id`` vs ``Vgs`` at ``Vds=Vdd``):
  in strong inversion ``sqrt(Id) = sqrt(mu_cox*W/(2L)) * (Vgs - vth)``, so a
  linear fit of ``sqrt(Id)`` vs ``Vgs`` gives ``vth`` (x-intercept) and
  ``mu_cox = 2 * slope**2 * L / W``.
* **lam** from an output sweep (``Id`` vs ``Vds`` at fixed overdrive): in
  saturation ``Id = Id0 * (1 + lam*Vds)``, so ``lam = slope / intercept``.

Because mu_cox(effective) and lam are bias/length dependent, the extraction
geometry (``Wext``, ``Lext``) and bias are recorded in the emitted YAML header.

Usage::

    python3 tools/extract_tech.py            # regenerate all built-in nodes
    python3 tools/extract_tech.py --node 45  # one node

Requires: ngspice on PATH, numpy.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent / "circuitgenome" / "sizer" / "shared"
_CONFIG = _SHARED / "config"
_MODELS = _CONFIG / "models"
_PDK = _SHARED / "pdk"

# Per-node extraction + geometry-grid settings.  Grids are node-appropriate
# starting points (um / pF); mu_cox/vth/lam are filled by extraction.
#
# ``kind`` selects the SPICE device interface:
#   "model" (default) — flat ``.include`` of a BSIM4 card defining ``.model
#                       nmos``/``pmos`` (PTM); devices are ``M`` instances.
#   "pdk"             — foundry library: ``.include`` a design file then
#                       ``.lib <file> <corner>``; devices are subcircuits (``X``)
#                       named ``nmos_dev``/``pmos_dev`` with the BSIM4 device at
#                       the internal node ``m0`` (handle ``@m.x1.m0``).
NODES: dict[str, dict] = {
    "gf180mcu": dict(
        kind="pdk",
        design="gf180/ngspice/design.ngspice",
        lib="gf180/ngspice/sm141064.ngspice", corner="typical",
        nmos_dev="nmos_3p3", pmos_dev="pmos_3p3",
        vdd=3.3, l_ext_um=0.28, w_ext_um=10.0,
        width=(0.22, 100.0, 0.005), length=(0.28, 4.0, 0.02),
        cap=(0.01, 20.0, 0.01), out="gf180mcu",
        desc="GF180MCU 180nm core 3.3V (nmos_3p3/pmos_3p3, BSIM4 level=54)",
    ),
    "45": dict(
        card="ptm_45nm_HP.pm", vdd=1.0, l_ext_um=0.09, w_ext_um=1.0,
        width=(0.10, 100.0, 0.05), length=(0.045, 1.0, 0.005),
        cap=(0.01, 10.0, 0.01),
        desc="PTM 45nm HP (BSIM4 bulk, metal-gate/high-K/strained-Si)",
    ),
}

_UM = 1e-6


class _Model:
    """Per-node SPICE device interface (PTM ``.model`` M-device vs PDK subckt).

    Provides the model-include block, an instance-line builder, and the
    operating-point handle prefix, so the sweep decks below are device-agnostic.
    """

    def __init__(self, cfg: dict):
        if cfg.get("kind") == "pdk":
            inc = f'.include "{_PDK / cfg["design"]}"\n' if cfg.get("design") else ""
            inc += f'.lib "{_PDK / cfg["lib"]}" {cfg.get("corner", "typical")}\n'
            self.include = inc
            self._dev = {"nmos": cfg["nmos_dev"], "pmos": cfg["pmos_dev"]}
            self._x = "x"
            self.prefix = "@m.x1.m0"
        else:
            self.include = f'.include "{_MODELS / cfg["card"]}"\n'
            self._dev = {"nmos": "nmos", "pmos": "pmos"}
            self._x = "M"
            self.prefix = "@m1"

    def inst(self, dev: str, nodes: str, w: float, l: float) -> str:
        """A single sized device instance named ``<x>1`` on ``nodes`` (d g s b)."""
        return f"{self._x}1 {nodes} {self._dev[dev]} W={w:.6e} L={l:.6e}"


def _run_ngspice(deck: str) -> np.ndarray:
    """Run a deck in ngspice batch mode and return the wrdata table."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.dat"
        sp = Path(d) / "deck.sp"
        sp.write_text(deck.replace("__OUT__", str(out)))
        subprocess.run(
            ["ngspice", "-b", str(sp)],
            capture_output=True, text=True, check=True,
        )
        return np.loadtxt(out)


def _transfer(model: _Model, dev: str, vdd: float, w_um: float, l_um: float):
    """Return (vgs[], id_abs[]) for a Vds=Vdd transfer sweep."""
    w, l = w_um * _UM, l_um * _UM
    step = vdd / 400.0
    if dev == "nmos":
        src = f"Vd d 0 {vdd}\nVg g 0 0\n{model.inst('nmos', 'd g 0 0', w, l)}\n"
        sweep = f".dc Vg 0 {vdd} {step:.6e}"
    else:
        src = (f"Vs s 0 {vdd}\nVd d 0 0\nVg g 0 {vdd}\n"
               f"{model.inst('pmos', 'd g s s', w, l)}\n")
        sweep = f".dc Vg {vdd} 0 {-step:.6e}"
    deck = (f"* transfer {dev}\n{model.include}{src}{sweep}\n"
            ".control\nrun\nwrdata __OUT__ i(Vd)\n.endc\n.end\n")
    data = _run_ngspice(deck)
    sweep_v, cur = data[:, 0], np.abs(data[:, 1])
    vgs = sweep_v if dev == "nmos" else (vdd - sweep_v)
    order = np.argsort(vgs)
    return vgs[order], cur[order]


def _output(model: _Model, dev: str, vdd: float, vov: float, w_um: float, l_um: float):
    """Return (vds[], id_abs[]) for a fixed-overdrive output sweep."""
    w, l = w_um * _UM, l_um * _UM
    step = vdd / 400.0
    if dev == "nmos":
        src = f"Vd d 0 0\nVg g 0 {vov:.6e}\n{model.inst('nmos', 'd g 0 0', w, l)}\n"
        sweep = f".dc Vd 0 {vdd} {step:.6e}"
    else:
        src = (f"Vs s 0 {vdd}\nVg g 0 {vdd - vov:.6e}\nVd d 0 {vdd}\n"
               f"{model.inst('pmos', 'd g s s', w, l)}\n")
        sweep = f".dc Vd {vdd} 0 {-step:.6e}"
    deck = (f"* output {dev}\n{model.include}{src}{sweep}\n"
            ".control\nrun\nwrdata __OUT__ i(Vd)\n.endc\n.end\n")
    data = _run_ngspice(deck)
    vds_raw, cur = data[:, 0], np.abs(data[:, 1])
    vds = vds_raw if dev == "nmos" else (vdd - vds_raw)
    order = np.argsort(vds)
    return vds[order], cur[order]


def _fit_transfer(vgs, idr, w_um, l_um):
    """Fit sqrt(Id) vs Vgs in strong inversion -> (vth, mu_cox)."""
    idmax = idr.max()
    # Strong-inversion window: 10%..50% of Id_max (above weak inversion,
    # below heavy velocity-saturation roll-off where the square law breaks).
    mask = (idr > 0.10 * idmax) & (idr < 0.50 * idmax)
    vg, sq = vgs[mask], np.sqrt(idr[mask])
    slope, intercept = np.polyfit(vg, sq, 1)
    vth = -intercept / slope
    mu_cox = 2.0 * slope ** 2 * (l_um / w_um)
    return vth, mu_cox


def _fit_lambda(vds, idr, vdd):
    """Fit Id = Id0*(1+lam*Vds) in deep saturation -> lam (1/V)."""
    mask = vds > 0.5 * vdd
    slope, intercept = np.polyfit(vds[mask], idr[mask], 1)
    return slope / intercept


def extract(node: str) -> dict:
    cfg = NODES[node]
    model = _Model(cfg)
    vdd, w, l = cfg["vdd"], cfg["w_ext_um"], cfg["l_ext_um"]
    res = {}
    for dev in ("nmos", "pmos"):
        vgs, idr = _transfer(model, dev, vdd, w, l)
        vth, mu_cox = _fit_transfer(vgs, idr, w, l)
        vov = min(0.2, 0.4 * vdd)
        vds, ido = _output(model, dev, vdd, abs(vth) + vov, w, l)
        lam = _fit_lambda(vds, ido, vdd)
        res[dev] = dict(
            mu_cox=mu_cox,
            vth=vth if dev == "nmos" else -abs(vth),
            lam=lam,
        )
    return res


def write_yaml(node: str, params: dict) -> Path:
    cfg = NODES[node]
    n, p = params["nmos"], params["pmos"]
    wmin, wmax, wstep = cfg["width"]
    lmin, lmax, lstep = cfg["length"]
    cmin, cmax, cstep = cfg["cap"]
    out = _CONFIG / f"tech_ptm{node}.yaml"
    out.write_text(f"""\
# {cfg['desc']}
# Level-1 parameters (mu_cox, vth, lam) EXTRACTED with ngspice from the BSIM4
# card models/{cfg['card']} by tools/extract_tech.py.
# Source: ASU Predictive Technology Model (PTM), https://ptm.asu.edu
#   W. Zhao, Y. Cao, "New Generation of Predictive Technology Model for
#   Sub-45nm Design Exploration," ISQED 2006.  Mirror: https://mec.umn.edu/ptm
# Extraction point: Vdd={cfg['vdd']} V, W={cfg['w_ext_um']} um, L={cfg['l_ext_um']} um
#   (effective square-law fit; mu_cox/lam are bias/length dependent).
name: ptm{node}_hp
description: "{cfg['desc']}"
spice_model: models/{cfg['card']}   # BSIM4 card for SPICE verification

nmos:
  mu_cox: {n['mu_cox']:.6e}   # A/V²  (effective µn·Cox)
  vth: {n['vth']:.4f}            # V
  lam: {n['lam']:.4f}            # 1/V

pmos:
  mu_cox: {p['mu_cox']:.6e}   # A/V²  (effective µp·Cox)
  vth: {p['vth']:.4f}           # V
  lam: {p['lam']:.4f}            # 1/V

width:
  min: {wmin}
  max: {wmax}
  step: {wstep}

length:
  min: {lmin}
  max: {lmax}
  step: {lstep}

cap:
  min_pf: {cmin}
  max_pf: {cmax}
  step_pf: {cstep}
""")
    return out


# ---------------------------------------------------------------------------
# gm/Id lookup-table characterization
# ---------------------------------------------------------------------------
# The gm/Id sizing path drives geometry from a SPICE-characterized table instead
# of the square law.  For each polarity and channel length we sweep Vgs in
# saturation (Vds = Vdd/2) at a fixed extraction width and read the BSIM4
# operating-point parameters, then invert onto a uniform gm/Id axis.

# Uniform gm/Id axis (1/V): weak inversion (~24) down to strong inversion (~6).
_GMID_AXIS = np.round(np.arange(6.0, 24.0 + 1e-9, 0.5), 3)


def _gmid_sweep(model: _Model, dev: str, vdd: float, w_um: float, l_um: float):
    """Sweep Vgs at Vds=Vdd/2; return per-point (gm_id, id_w, gm_gds, ft, vdsat, vgs).

    ``id_w`` is Id per µm of width (A/µm); ``vgs`` is the gate-source voltage
    magnitude (positive for both polarities).  All quantities are magnitudes.
    """
    w, l = w_um * _UM, l_um * _UM
    vds = vdd / 2.0
    step = vdd / 400.0
    p = model.prefix
    fields = f"{p}[id] {p}[gm] {p}[gds] {p}[cgg] {p}[vdsat]"
    saves = f".save {fields}\n"
    if dev == "nmos":
        src = f"Vd d 0 {vds:.6e}\nVg g 0 0\n{model.inst('nmos', 'd g 0 0', w, l)}\n"
        sweep = f".dc Vg 0 {vdd} {step:.6e}"
    else:
        src = (f"Vs s 0 {vdd}\nVd d 0 {vdd - vds:.6e}\nVg g 0 {vdd}\n"
               f"{model.inst('pmos', 'd g s s', w, l)}\n")
        sweep = f".dc Vg {vdd} 0 {-step:.6e}"
    deck = (f"* gmid {dev}\n{model.include}{src}{saves}{sweep}\n"
            f".control\nrun\nwrdata __OUT__ {fields}\n.endc\n.end\n")
    data = _run_ngspice(deck)
    # wrdata writes a scale column before each vector: [vg, id, vg, gm, vg, gds, ...].
    vg = data[:, 0]
    idr = np.abs(data[:, 1])
    gm = np.abs(data[:, 3])
    gds = np.abs(data[:, 5])
    cgg = np.abs(data[:, 7])
    vdsat = np.abs(data[:, 9])
    vgs = vg if dev == "nmos" else (vdd - vg)
    with np.errstate(divide="ignore", invalid="ignore"):
        gm_id = gm / idr
        id_w = idr / w_um
        gm_gds = gm / gds
        ft = gm / (2.0 * np.pi * cgg)
    return gm_id, id_w, gm_gds, ft, vdsat, vgs


def _invert_to_axis(gm_id, fields: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Resample per-point fields onto the uniform gm/Id axis at one L.

    ``gm_id`` decreases as Vgs rises; we keep the strictly-decreasing, finite,
    in-window samples, sort ascending, and linearly interpolate (np.interp
    clamps out-of-range axis points to the endpoints).
    """
    g = gm_id
    good = np.isfinite(g)
    for v in fields.values():
        good &= np.isfinite(v)
    good &= (g >= _GMID_AXIS[0] - 2.0) & (g <= _GMID_AXIS[-1] + 4.0)
    g = g[good]
    order = np.argsort(g)
    g = g[order]
    # Deduplicate equal gm/Id (flat weak-inversion tail) so np.interp is well posed.
    keep = np.concatenate(([True], np.diff(g) > 1e-9))
    g = g[keep]
    out: dict[str, np.ndarray] = {}
    for name, v in fields.items():
        vv = v[good][order][keep]
        out[name] = np.interp(_GMID_AXIS, g, vv)
    return out


def extract_gmid(node: str) -> dict:
    """Build the gm/Id LUT arrays for one node (both polarities)."""
    cfg = NODES[node]
    model = _Model(cfg)
    vdd, w = cfg["vdd"], cfg["w_ext_um"]
    lmin, lmax, lstep = cfg["length"]
    # ~10 log-spaced lengths from L_min to L_max, snapped to the length grid.
    raw = np.geomspace(lmin, lmax, 10)
    l_axis = np.unique(np.round(np.round(raw / lstep) * lstep, 6))
    data: dict[str, np.ndarray] = {"gm_id_axis": _GMID_AXIS, "l_axis": l_axis}
    for dev in ("nmos", "pmos"):
        cube = {k: [] for k in ("id_w", "gm_gds", "ft", "vdsat", "vgs")}
        for l_um in l_axis:
            gm_id, id_w, gm_gds, ft, vdsat, vgs = _gmid_sweep(model, dev, vdd, w, float(l_um))
            row = _invert_to_axis(
                gm_id,
                {"id_w": id_w, "gm_gds": gm_gds, "ft": ft, "vdsat": vdsat, "vgs": vgs},
            )
            for k in cube:
                cube[k].append(row[k])
        for k, rows in cube.items():
            data[f"{dev}_{k}"] = np.asarray(rows)  # shape (n_L, n_gmid)
    return data


def write_npz(node: str, data: dict) -> Path:
    stem = NODES[node].get("out", f"ptm{node}")
    out = _MODELS / f"{stem}_gmid.npz"
    np.savez(out, **data)
    return out


def inject_gmid_lut_yaml(node: str, npz_name: str) -> None:
    """Add/update a ``gmid_lut:`` line in the node's tech YAML (after spice_model)."""
    yml = _CONFIG / f"tech_ptm{node}.yaml"
    if not yml.exists():
        return
    lines = yml.read_text().splitlines(keepends=True)
    rel = f"models/{npz_name}"
    if any(l.lstrip().startswith("gmid_lut:") for l in lines):
        lines = [
            (f"gmid_lut: {rel}   # gm/Id LUT for the procedural PTM sizer\n"
             if l.lstrip().startswith("gmid_lut:") else l)
            for l in lines
        ]
    else:
        idx = next((i for i, l in enumerate(lines)
                    if l.lstrip().startswith("spice_model:")), None)
        new = f"gmid_lut: {rel}   # gm/Id LUT for the procedural PTM sizer\n"
        if idx is not None:
            lines.insert(idx + 1, new)
        else:
            lines.append(new)
    yml.write_text("".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node", choices=sorted(NODES), help="One node (default: all)")
    ap.add_argument("--gm-id", action="store_true",
                    help="Characterize the gm/Id LUT (write *_gmid.npz + YAML gmid_lut:) "
                         "instead of the Level-1 fit.")
    args = ap.parse_args()
    nodes = [args.node] if args.node else sorted(NODES)
    if args.gm_id:
        for node in nodes:
            data = extract_gmid(node)
            path = write_npz(node, data)
            inject_gmid_lut_yaml(node, path.name)
            ax = data["gm_id_axis"]
            print(f"{node}nm gm/Id LUT: {data['l_axis'].size} lengths × "
                  f"{ax.size} gm/Id points [{ax[0]}..{ax[-1]}/V]  ->  {path.name}")
        return
    for node in nodes:
        params = extract(node)
        path = write_yaml(node, params)
        n, p = params["nmos"], params["pmos"]
        print(f"{node}nm: nmos µCox={n['mu_cox']:.3e} vth={n['vth']:.3f} "
              f"lam={n['lam']:.3f} | pmos µCox={p['mu_cox']:.3e} "
              f"vth={p['vth']:.3f} lam={p['lam']:.3f}  ->  {path.name}")


if __name__ == "__main__":
    main()
