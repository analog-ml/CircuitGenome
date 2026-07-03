"""Shared testbench rig: port classification, supplies, DUT instantiation.

Every measurement wraps the same sized DUT block in a small rig; the helpers
here remove the per-testbench boilerplate: the port→net map for a chosen input
polarity (:func:`_fb_netmap`), and full-deck assembly (:func:`_deck`).  The
input polarity — which of (in1, in2) is the non-inverting input — is detected
once by the AC testbench (via the DC-settle check) and reused by the slew,
swing, CMRR and PSRR benches.
"""
from __future__ import annotations

# Both input-polarity assignments: (non-inverting, inverting).
_POLARITIES = (("in1", "in2"), ("in2", "in1"))


class _Topo:
    def __init__(self, ports: list[str]):
        self.ports = ports
        self.fd = "outp" in ports and "outn" in ports
        self.out = ["outp", "outn"] if self.fd else ["out"]
        self.has_vcm = "vcm_ref" in ports


def _xline(name: str, ports: list[str], netmap: dict) -> str:
    """X-instance wiring DUT ports to testbench nets via ``netmap``."""
    nodes = " ".join(netmap[p] for p in ports)
    return f"Xdut {nodes} {name}"


def _rig(vdd: float, ibias: float, sup_ac: bool = False) -> str:
    """Supply + bias-current sources; ``sup_ac`` rides 1 V AC on VDD (PSRR)."""
    return (
        f"Vsup vdd 0 dc {vdd}{' ac 1' if sup_ac else ''}\n"
        f"Iref 0 ibias dc {ibias}\n"     # inject bias current into the ibias node
    )


def _fb_netmap(topo: _Topo, inp: str, inn: str) -> dict:
    """Port→net map for a feedback testbench with the given input polarity."""
    netmap = {"ibias": "ibias", "vdd!": "vdd", "gnd!": "0",
              inp: "inp", inn: "inn"}
    if topo.fd:
        netmap["outp"], netmap["outn"] = "outp", "outn"
        netmap["vcm_ref"] = "ocm"
    else:
        netmap["out"] = "out"
    return netmap


def _deck(name: str, ports: list[str], body_dut: str, vdd: float, ibias: float,
          fb: str, netmap: dict, control: str, sup_ac: bool = False) -> str:
    """Assemble a full ngspice deck: DUT + supplies + testbench ``fb`` + control."""
    return (body_dut.replace("__PORTS__", " ".join(ports))
            + _rig(vdd, ibias, sup_ac) + fb
            + _xline(name, ports, netmap) + "\n"
            + f".control\n{control}\n.endc\n.end\n")
