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
_CONFIG = _HERE.parent / "circuitgenome" / "sizer" / "config"
_MODELS = _CONFIG / "models"

# Per-node extraction + geometry-grid settings.  Grids are node-appropriate
# starting points (um / pF); mu_cox/vth/lam are filled by extraction.
NODES: dict[str, dict] = {
    "45": dict(
        card="ptm_45nm_HP.pm", vdd=1.0, l_ext_um=0.09, w_ext_um=1.0,
        width=(0.10, 100.0, 0.05), length=(0.045, 1.0, 0.005),
        cap=(0.01, 10.0, 0.01),
        desc="PTM 45nm HP (BSIM4 bulk, metal-gate/high-K/strained-Si)",
    ),
    "32": dict(
        card="ptm_32nm_HP.pm", vdd=0.9, l_ext_um=0.064, w_ext_um=1.0,
        width=(0.05, 60.0, 0.05), length=(0.032, 0.6, 0.002),
        cap=(0.01, 10.0, 0.01),
        desc="PTM 32nm HP (BSIM4 bulk, metal-gate/high-K/strained-Si)",
    ),
    "22": dict(
        card="ptm_22nm_HP.pm", vdd=0.8, l_ext_um=0.044, w_ext_um=1.0,
        width=(0.05, 50.0, 0.05), length=(0.022, 0.5, 0.002),
        cap=(0.01, 10.0, 0.01),
        desc="PTM 22nm HP (BSIM4 bulk, metal-gate/high-K/strained-Si)",
    ),
    "16": dict(
        card="ptm_16nm_HP.pm", vdd=0.7, l_ext_um=0.032, w_ext_um=1.0,
        width=(0.05, 40.0, 0.05), length=(0.016, 0.4, 0.002),
        cap=(0.01, 10.0, 0.01),
        desc="PTM 16nm HP (BSIM4 bulk PREDICTIVE planar extrapolation; "
             "real 16nm silicon is FinFET)",
    ),
}

_UM = 1e-6


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


def _transfer(card: Path, dev: str, vdd: float, w_um: float, l_um: float):
    """Return (vgs[], id_abs[]) for a Vds=Vdd transfer sweep."""
    w, l = w_um * _UM, l_um * _UM
    step = vdd / 400.0
    if dev == "nmos":
        deck = f"""* transfer nmos
.include "{card}"
Vd d 0 {vdd}
Vg g 0 0
M1 d g 0 0 nmos W={w:.6e} L={l:.6e}
.dc Vg 0 {vdd} {step:.6e}
.control
run
wrdata __OUT__ i(Vd)
.endc
.end
"""
    else:
        deck = f"""* transfer pmos
.include "{card}"
Vs s 0 {vdd}
Vd d 0 0
Vg g 0 {vdd}
M1 d g s s pmos W={w:.6e} L={l:.6e}
.dc Vg {vdd} 0 {-step:.6e}
.control
run
wrdata __OUT__ i(Vd)
.endc
.end
"""
    data = _run_ngspice(deck)
    sweep, cur = data[:, 0], np.abs(data[:, 1])
    vgs = sweep if dev == "nmos" else (vdd - sweep)
    order = np.argsort(vgs)
    return vgs[order], cur[order]


def _output(card: Path, dev: str, vdd: float, vov: float, w_um: float, l_um: float):
    """Return (vds[], id_abs[]) for a fixed-overdrive output sweep."""
    w, l = w_um * _UM, l_um * _UM
    step = vdd / 400.0
    if dev == "nmos":
        deck = f"""* output nmos
.include "{card}"
Vd d 0 0
Vg g 0 {vov:.6e}
M1 d g 0 0 nmos W={w:.6e} L={l:.6e}
.dc Vd 0 {vdd} {step:.6e}
.control
run
wrdata __OUT__ i(Vd)
.endc
.end
"""
    else:
        deck = f"""* output pmos
.include "{card}"
Vs s 0 {vdd}
Vg g 0 {vdd - vov:.6e}
Vd d 0 {vdd}
M1 d g s s pmos W={w:.6e} L={l:.6e}
.dc Vd {vdd} 0 {-step:.6e}
.control
run
wrdata __OUT__ i(Vd)
.endc
.end
"""
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
    card = _MODELS / cfg["card"]
    vdd, w, l = cfg["vdd"], cfg["w_ext_um"], cfg["l_ext_um"]
    res = {}
    for dev in ("nmos", "pmos"):
        vgs, idr = _transfer(card, dev, vdd, w, l)
        vth, mu_cox = _fit_transfer(vgs, idr, w, l)
        vov = min(0.2, 0.4 * vdd)
        vds, ido = _output(card, dev, vdd, abs(vth) + vov, w, l)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node", choices=sorted(NODES), help="One node (default: all)")
    args = ap.parse_args()
    nodes = [args.node] if args.node else sorted(NODES)
    for node in nodes:
        params = extract(node)
        path = write_yaml(node, params)
        n, p = params["nmos"], params["pmos"]
        print(f"{node}nm: nmos µCox={n['mu_cox']:.3e} vth={n['vth']:.3f} "
              f"lam={n['lam']:.3f} | pmos µCox={p['mu_cox']:.3e} "
              f"vth={p['vth']:.3f} lam={p['lam']:.3f}  ->  {path.name}")


if __name__ == "__main__":
    main()
