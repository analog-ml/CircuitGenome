"""SPICE deck building blocks: models, netlist parsing/sizing, ngspice runs.

Everything here is measurement-agnostic: emitting the technology's model
block, parsing the DUT ``.subckt``, injecting sized W/L/R/Cc values, and the
two ngspice runners (table output via ``wrdata``, text output via ``print``).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..models import SizingResult, TechParams

_MOS_MODELS = ("nmos", "pmos")


def ngspice_available() -> bool:
    """True if the ``ngspice`` binary is on PATH."""
    return shutil.which("ngspice") is not None


def _emit_model(tech: TechParams, corner: str | None = None) -> str:
    """Return the SPICE ``.model``/``.include``/``.lib`` block for ``tech``.

    * ``spice_lib`` (foundry PDK, e.g. GF180MCU): ``.include`` the global design
      file (if any) then ``.lib "<file>" <corner>`` — devices are subcircuits.
    * ``spice_model`` (PTM): flat ``.include`` of ``.model nmos``/``pmos`` cards.
    * neither: synthesise a Level-1 ``.model`` from ``mu_cox``/``vth``/``lam``.
    """
    if tech.spice_lib:
        lib = tech.spice_lib
        out = f'.include "{lib.design}"\n' if lib.design else ""
        return out + f'.lib "{lib.file}" {corner or lib.corner}\n'
    if tech.spice_model:
        return f'.include "{tech.spice_model}"\n'
    n, p = tech.nmos, tech.pmos
    # Shichman-Hodges (level=1): KP=µCox, VTO=Vth, LAMBDA=λ.
    return (
        f".model nmos nmos level=1 kp={n.mu_cox:.6e} vto={n.vth:.4f} "
        f"lambda={n.lam:.4f} gamma=0 phi=0.7\n"
        f".model pmos pmos level=1 kp={p.mu_cox:.6e} vto={p.vth:.4f} "
        f"lambda={p.lam:.4f} gamma=0 phi=0.7\n"
    )


def _xref(ref: str) -> str:
    """Subcircuit instance name for a MOSFET ``ref`` (``m1_in`` → ``x1_in``)."""
    return ("x" + ref[1:]) if ref[:1].lower() == "m" else ("x" + ref)


def _dev_prefix(tech: TechParams, ref: str) -> str:
    """ngspice operating-point handle prefix for device ``ref`` inside ``Xdut``.

    PTM/generic instantiate ``.model`` MOSFETs (flat ``@m.xdut.<ref>``); a PDK
    with a ``device_map`` instantiates subcircuits, so the BSIM4 device sits one
    level deeper at the subckt's internal ``m0`` (``@m.xdut.<xref>.m0``).
    """
    if tech.device_map:
        return f"@m.xdut.{_xref(ref)}.m0"
    return f"@m.xdut.{ref}"


def _emit_body(tech: TechParams, body: list[str]) -> list[str]:
    """Rewrite generic ``m… nmos/pmos`` lines as PDK subcircuit ``x…`` instances.

    No-op unless ``tech.device_map`` is set.  Maps the model token via the map
    (``nmos`` → ``nmos_3p3``) and lowercases ``W=``/``L=`` to the subckt params.
    """
    dm = tech.device_map
    if not dm:
        return body
    out: list[str] = []
    for line in body:
        tok = line.split()
        if len(tok) >= 6 and tok[5].lower() in dm:
            rest = " ".join(tok[6:]).replace("W=", "w=").replace("L=", "l=")
            out.append(
                f"{_xref(tok[0])} {tok[1]} {tok[2]} {tok[3]} {tok[4]} "
                f"{dm[tok[5].lower()]} {rest}".rstrip())
        else:
            out.append(line)
    return out


def _parse_subckt(netlist_text: str):
    """Return ``(name, ports, body_lines)`` for the single ``.subckt`` block."""
    lines = netlist_text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().lower().startswith(".subckt"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip().lower().startswith(".ends"))
    head = lines[start].split()
    name, ports = head[1], head[2:]
    body = [l for l in lines[start + 1:end] if l.strip()]
    return name, ports, body


def sized_netlist(netlist_text: str, result: SizingResult) -> str:
    """Return ``netlist_text`` with the sized values from ``result`` injected.

    Each MOSFET in the single ``.subckt`` block gets its ``W=``/``L=``, sized
    load resistors get their ohm value and the compensation cap(s) get
    ``cc_pf``/``cc2_pf`` — the same injection the verification decks use.  Any
    lines before the ``.subckt`` (title/comments) are preserved, so the output
    is a standalone flat netlist ready for SPICE.
    """
    lines = netlist_text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().lower().startswith(".subckt"))
    name, ports, body = _parse_subckt(netlist_text)
    body = _inject_sizes(body, result)
    out = lines[:start] + [f".subckt {name} {' '.join(ports)}"] + body + [".ends"]
    return "\n".join(out) + "\n"


def _inject_sizes(body: list[str], result: SizingResult) -> list[str]:
    """Set sized W/L (MOSFETs), Cc (comp caps) and R (sized load resistors)."""
    cc1 = result.cc_pf
    cc2 = result.cc2_pf if result.cc2_pf is not None else cc1
    out = []
    for line in body:
        tok = line.split()
        ref = tok[0]
        low = ref.lower()
        if len(tok) >= 6 and tok[5].lower() in _MOS_MODELS and ref in result.transistors:
            s = result.transistors[ref]
            out.append(f"{line.rstrip()} W={s.w_um:.5f}u L={s.l_um:.5f}u")
        elif ref in result.resistors and len(tok) >= 4:
            # sized load resistor: replace the placeholder value with R (ohms)
            out.append(f"{tok[0]} {tok[1]} {tok[2]} {result.resistors[ref]:.4f}")
        elif low.startswith("c") and "comp" in low and len(tok) >= 4:
            val = cc2 if "comp2" in low else cc1
            if val:
                out.append(f"{tok[0]} {tok[1]} {tok[2]} {val:.4f}p")
            else:
                out.append(line.rstrip())
        else:
            out.append(line.rstrip())
    return out


def _dut(tech: TechParams, name: str, body: list[str],
         corner: str | None = None) -> str:
    """Title line + model block + the sized ``.subckt`` definition.

    The leading comment is mandatory: SPICE always treats the first deck line as
    the title and ignores it, which would otherwise swallow the first ``.model``.
    For a PDK tech the device lines are rewritten to subcircuit instances.
    """
    return ("* circuitgenome spice verification\n"
            + _emit_model(tech, corner) + f".subckt {name} __PORTS__\n"
            + "\n".join(_emit_body(tech, body)) + "\n.ends\n")


def _run(deck: str, vectors: list[str]) -> np.ndarray | None:
    """Run ``deck`` in ngspice -b; return the wrdata table (or None on failure).

    ``deck`` must contain ``__OUT__`` where the wrdata filename goes and a
    ``.control`` block ending in ``wrdata __OUT__ <vectors>``.
    """
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.dat"
        sp = Path(d) / "deck.sp"
        sp.write_text(deck.replace("__OUT__", str(out)))
        try:
            subprocess.run(["ngspice", "-b", str(sp)], capture_output=True,
                           text=True, timeout=60)
        except Exception:
            return None
        if not out.exists():
            return None
        try:
            data = np.loadtxt(out)
        except Exception:
            return None
        return np.atleast_2d(data)


def _run_capture(deck: str) -> str | None:
    """Run ``deck`` in ngspice -b and return stdout (for ``print`` output)."""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "deck.sp"
        sp.write_text(deck)
        try:
            p = subprocess.run(["ngspice", "-b", str(sp)], capture_output=True,
                               text=True, timeout=60)
        except Exception:
            return None
        return p.stdout
