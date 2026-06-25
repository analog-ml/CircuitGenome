"""Tests for cascode-aware output resistance and the CASCODE sizing role."""
import math

from circuitgenome.sizer.gmid.blocks import cascode_device_refs, node_rout
from circuitgenome.synthesizer.models import Device


class _FakeModel:
    """Constant gm / gds for deterministic rout arithmetic."""
    def __init__(self, gm=1e-3, gds=1e-6):
        self._gm, self._gds = gm, gds

    def gm(self, dtype, w, l, ids):
        return self._gm

    def gds(self, dtype, w, l, ids):
        return self._gds


class _Sz:
    def __init__(self):
        self.w_um = self.l_um = self.ids_a = 1.0


def D(ref, t, **term):
    return Device(ref=ref, type=t, terminals=term)


def test_cascode_device_refs():
    # mn_casc stacked on mn_bot (source == mn_bot.drain); a plain CS is not cascode.
    bot = D("mn_bot", "nmos", g="net_bias1", d="n1", s="0")
    casc = D("mn_casc", "nmos", g="net_bias2", d="out", s="n1")
    plain = D("mn_cs", "nmos", g="net_bias1", d="x", s="0")
    refs = cascode_device_refs({"load": [bot, casc, plain]})
    assert refs == {"mn_casc"}


def test_node_rout_cascode_boost():
    model = _FakeModel(gm=1e-3, gds=1e-6)   # ro = 1 MΩ, gm·ro = 1000
    sizing = {"mn_bot": _Sz(), "mn_casc": _Sz()}
    bot = D("mn_bot", "nmos", g="b1", d="n1", s="0")
    casc = D("mn_casc", "nmos", g="b2", d="out", s="n1")
    r = node_rout("out", [bot, casc], model, sizing)
    # ro·(1 + gm·ro) = 1e6·(1 + 1e-3·1e6) = 1.001 GΩ
    assert math.isclose(r, 1e6 * (1 + 1e-3 * 1e6), rel_tol=1e-9)


def test_node_rout_plain_is_ro():
    model = _FakeModel(gm=1e-3, gds=1e-6)
    sizing = {"m": _Sz()}
    d = D("m", "nmos", g="b", d="out", s="0")   # source on rail → just ro
    assert abs(node_rout("out", [d], model, sizing) - 1e6) < 1.0


def test_node_rout_parallel_and_tail_stop():
    model = _FakeModel(gm=1e-3, gds=1e-6)
    sizing = {"m_load": _Sz(), "m_ip": _Sz()}
    load = D("m_load", "nmos", g="b", d="out", s="0")          # ro = 1 MΩ
    ip = D("m_ip", "pmos", g="in", d="out", s="net_tail")      # tail = AC ground
    # With net_tail as a stop the input pair contributes ro (not a cascode boost),
    # so rout = 1 MΩ ∥ 1 MΩ = 500 kΩ.
    r = node_rout("out", [load, ip], model, sizing, frozenset({"net_tail"}))
    assert abs(r - 0.5e6) < 1.0
