"""Shared testbench rig: port classification, supplies, DUT instantiation.

Every measurement wraps the same sized DUT block in a small rig; the helpers
here remove the per-testbench boilerplate: the port→net map for a chosen input
polarity (:func:`_fb_netmap`), and full-deck assembly (:func:`_deck`).  The
input polarity — which of (in1, in2) is the non-inverting input — is detected
once by the AC testbench (via the DC-settle check) and reused by the slew,
swing, CMRR and PSRR benches.

The bias-current source direction is not fixed: it follows the DUT's ``ibias``
reference-diode orientation (:func:`_iref_sink`), because an NMOS-referenced
bias generator needs the reference current pushed *into* the pin while a
PMOS-referenced one needs it pulled *out*.
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


def _iref_sink(dut_lines) -> bool:
    """True when the external bias source must pull current *out of* ``ibias``.

    The DUT's reference is the diode-connected MOSFET on the ``ibias`` net
    (``d == g == ibias``).  With its source at ``gnd!`` (NMOS reference, e.g.
    ``diode_connected_mosfet_bias``) the diode sinks an *injected* current;
    with its source at ``vdd!`` (PMOS reference, e.g. ``magic_battery_bias``
    or ``resistor_bias``) the diode conducts from the supply *out of* the pin,
    so the external source must sink the current to ground — injecting instead
    floats the pin above VDD and leaves the whole bias generator dead.

    Works on raw and PDK-rewritten device lines alike (both keep the
    ``ref d g s b model`` token order).  No reference diode, or conflicting
    diodes on both rails, falls back to inject (the historical behavior).
    """
    rails = set()
    for line in dut_lines:
        tok = line.split()
        if len(tok) >= 6 and tok[1].lower() == tok[2].lower() == "ibias":
            s = tok[3].lower()
            if "vdd" in s:
                rails.add("vdd")
            elif "gnd" in s or s in ("0", "vss!"):
                rails.add("gnd")
    return rails == {"vdd"}


def _rig(vdd: float, ibias: float, sup_ac: bool = False,
         sink: bool = False) -> str:
    """Supply + bias-current sources; ``sup_ac`` rides 1 V AC on VDD (PSRR).

    ``sink`` pulls the reference current out of the ``ibias`` pin instead of
    injecting it — required by PMOS-referenced bias generators (see
    :func:`_iref_sink`).
    """
    iref = (f"Iref ibias 0 dc {ibias}" if sink       # pull out of the pin
            else f"Iref 0 ibias dc {ibias}")         # inject into the pin
    return (
        f"Vsup vdd 0 dc {vdd}{' ac 1' if sup_ac else ''}\n"
        f"{iref}\n"
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
    """Assemble a full ngspice deck: DUT + supplies + testbench ``fb`` + control.

    The bias-current direction adapts to the DUT block's reference diode
    (:func:`_iref_sink`).
    """
    return (body_dut.replace("__PORTS__", " ".join(ports))
            + _rig(vdd, ibias, sup_ac, sink=_iref_sink(body_dut.splitlines()))
            + fb + _xline(name, ports, netmap) + "\n"
            + f".control\n{control}\n.endc\n.end\n")
