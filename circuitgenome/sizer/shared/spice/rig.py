"""Shared testbench rig: port classification, supplies, DUT instantiation."""
from __future__ import annotations


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


def _rig(vdd: float, ibias: float) -> str:
    return (
        f"Vsup vdd 0 {vdd}\n"
        f"Iref 0 ibias dc {ibias}\n"     # inject bias current into the ibias node
    )
