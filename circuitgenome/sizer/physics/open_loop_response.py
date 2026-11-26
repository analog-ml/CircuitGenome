"""The open-loop model: an amplifier's gain and phase at every frequency,
solved from its nodal equations.

The closed forms in :mod:`.equations` treat each extra pole as separate and
real.  They miss what decides a multi-stage amplifier's stability: poles that
pair up and ring, zeros from the compensation caps, an output buffer's gain
and pole.  This module writes the amplifier's small-signal half circuit as
matrices, ``(G + s·C)·v = b``, and reads GBW, phase margin and pole damping
off the solution numerically.  :doc:`/theory/rnmc_stability` walks through it.

:class:`OpenLoopResponse` solves any amplifier a :class:`CompensationScheme`
row describes.  Three rows are defined: two-stage Miller
(:data:`TWO_STAGE_MILLER`), nested Miller (:data:`NESTED_MILLER`) and reversed
nested Miller (:data:`REVERSED_NESTED_MILLER`).  Another scheme built from
gain stages and caps between their outputs is one more row.

Units are SI: A/V, F, Ω, Hz; angular frequencies in rad/s.
"""
from __future__ import annotations

import math
from functools import cached_property
from typing import NamedTuple

import numpy as np


# ---------------------------------------------------------------------------
# Describing the amplifier
# ---------------------------------------------------------------------------

class SmallSignalStage(NamedTuple):
    """One gain stage of the small-signal model.

    :param gm: Transconductance in A/V: how strongly the stage's input voltage
        steers its output current.
    :param g: Output conductance in A/V (``1/Rout``): how much current leaks
        from its output per volt.  ``0.0`` is floored to a tiny value so the
        DC gain stays finite.
    :param c: Parasitic capacitance from its output to ground in F, besides
        the load ``CL``.
    """

    gm: float
    g: float = 0.0
    c: float = 0.0


class Buffer(NamedTuple):
    """A source-follower output buffer between the last gain stage and ``CL``.

    Unpacks like the plain ``(gm, cgs, g)`` tuple it replaces.

    :param gm: The follower's transconductance in A/V.
    :param cgs: Its gate-source capacitance in F.
    :param g: Every other conductance on its output node in A/V (the
        output-stage drains, plus body effect), besides its own ``gm``.
    """

    gm: float
    cgs: float
    g: float


class CompensationScheme(NamedTuple):
    """How a compensation scheme wires the gain stages and the caps, and how
    its phase margin is read.

    Stages are numbered from ``0``, the input pair, which always inverts.

    :param inverting: Whether each later stage inverts, second stage first.
        A non-inverting stage is the one that passes through a current
        mirror when the amplifier has one.
    :param cap_nodes: ``(from, to)`` stage numbers of each compensation cap,
        outer cap first: the cap runs from one stage's output to the other's.
    :param near_crossings_count: Whether the phase margin also counts every
        *near*-crossing: a stretch where the gain, after dropping more than
        ``_NEAR_CROSSING_DB`` under 0 dB past the first crossing, climbs back
        within it.
    :param unstable_needs_crossing: How a loop with a right-half-plane pole
        reads when its gain never reaches 0 dB: no phase margin (``True``),
        or 0° like any other unstable loop (``False``).
    """

    inverting: tuple[bool, ...]
    cap_nodes: tuple[tuple[int, int], ...]
    near_crossings_count: bool
    unstable_needs_crossing: bool


#: One Miller cap around the inverting second stage.  The closed forms
#: (``gm1/(2π·Cc)``, :func:`~.equations.phase_margin_two_stage_deg`) miss the
#: output pole pulling the crossing down (~14 % at 55°), the series
#: resistor's zero cancelling that pole, and an output follower's gain and
#: pole (#274).
TWO_STAGE_MILLER = CompensationScheme(
    inverting=(True,), cap_nodes=((0, 1),),
    near_crossings_count=True, unstable_needs_crossing=True)

#: Nested Miller (NMC): both caps end on the third stage's output, ``Cc1``
#: from the first-stage output and ``Cc2`` from the second's; the second
#: stage does not invert.  :func:`~.equations.phase_margin_three_stage_deg`
#: ran 15–35° pessimistic against SPICE: behind a buffer the third stage
#: drives only the follower's gate, and the series resistors' zeros move both
#: extra poles (#274).
NESTED_MILLER = CompensationScheme(
    inverting=(False, True), cap_nodes=((0, 2), (1, 2)),
    near_crossings_count=True, unstable_needs_crossing=True)

#: Reversed nested Miller (RNMC): both caps start at the first-stage output,
#: ``Cc1`` to the third stage's output and ``Cc2`` to the second's; the third
#: stage does not invert.  That couples the two extra poles into a pair that
#: can ring, and the pair and a buffer's gain below 1 pull the GBW under
#: ``gm1/(2π·Cc1)``, by ~20 % on a buffered design (#274).  A resistor
#: ``≈ 1/gm`` in series with ``Cc2`` cancels the zero a bare ``Cc2`` makes
#: near ``gm2/Cc2`` (#266).
REVERSED_NESTED_MILLER = CompensationScheme(
    inverting=(True, False), cap_nodes=((0, 2), (0, 1)),
    near_crossings_count=False, unstable_needs_crossing=False)


# ---------------------------------------------------------------------------
# The open-loop response
# ---------------------------------------------------------------------------

class OpenLoopResponse:
    """An amplifier's open loop, solved from its nodal equations.

    Build it once, then read :attr:`gbw_hz`, :attr:`phase_margin_deg` and
    :attr:`damping` off it.  The frequency sweep runs only when a reading
    first needs it, so a search that reads the damping of an unstable
    candidate does not pay for it.

    One entry in ``comp_r`` builds a single-ended amplifier.  Two build the
    ``(p, n)`` halves of a fully-differential one, driven by ``±gm1/2`` and
    read as the difference of their outputs.  Matched halves give exactly
    the single half's response.  When their compensation differs, they are
    coupled through the output common mode, which the CMFB feeds back into
    each first-stage node (#274); without a CMFB they stay independent.

    :param scheme: The :class:`CompensationScheme` the amplifier is wired as.
    :param stages: The gain stages, input pair first: one more than
        ``scheme.inverting`` has entries.  The first stage's ``gm`` is the
        one the loop sees.
    :param caps_f: Each compensation cap in F, in ``scheme.cap_nodes`` order
        (outer cap first).
    :param cl_f: Load capacitance in F, on the buffer's output when there is
        one.
    :param mirror_pole_hz: Pole in Hz of the non-inverting stage's current
        mirror, or ``None`` when it has none.
    :param buffer: The output buffer, or ``None``.
    :param comp_r: Per half, the resistor in Ω in series with each cap
        (``0.0`` for a bare cap), in ``scheme.cap_nodes`` order.  ``None`` is
        one half of bare caps.
    :param cmfb: ``(gm_c, pole_hz)`` of a fully-differential amplifier's
        common-mode feedback: the current it injects into each first-stage
        node per volt of output common mode, in A/V, and its pole in Hz
        (``None``: no pole).  ``None`` without a CMFB.
    """

    def __init__(
        self,
        scheme: CompensationScheme,
        stages: tuple[SmallSignalStage, ...],
        caps_f: tuple[float, ...],
        cl_f: float,
        *, mirror_pole_hz: float | None = None,
        buffer: Buffer | None = None,
        comp_r: tuple[tuple[float, ...], ...] | None = None,
        cmfb: tuple[float, float | None] | None = None,
    ):
        stages = _floored(stages)
        self._scheme = scheme
        self._G, self._C, self._b, self._out = _amplifier_network(
            scheme, stages, caps_f, cl_f, mirror_pole_hz, buffer,
            comp_r or ((0.0,) * len(caps_f),), cmfb)
        self._poles = _poles(self._G, self._C)
        self._nominal_crossover = stages[0].gm / caps_f[0]

    @cached_property
    def _sweep_points(self):
        """The gain and phase sweep (:func:`_sweep`), run once."""
        return _sweep(self._G, self._C, self._b, self._out, self._poles,
                      self._nominal_crossover)

    @cached_property
    def _zero_db_crossings(self) -> list[tuple[float, float]]:
        """Every 0 dB crossing (:func:`_crossings`), found once."""
        return _crossings(self._G, self._C, self._b, self._out, self._sweep_points)

    @property
    def gbw_hz(self) -> float | None:
        """GBW in Hz: the first 0 dB crossing, the one the AC bench reports.

        :returns: ``None`` when the gain never reaches 0 dB.
        """
        crossings = self._zero_db_crossings
        return crossings[0][0] / (2.0 * math.pi) if crossings else None

    @property
    def phase_margin_deg(self) -> float | None:
        """Phase margin in degrees, the worst over every 0 dB crossing.

        PM = min over every 0 dB crossing of (180° + ∠A(jω))

        The worst crossing counts: a lightly damped pole pair can lift the
        gain back above 0 dB after the phase has passed −180°, which gives a
        negative margin.  A scheme whose ``near_crossings_count`` is set
        counts its near-crossings too.

        :returns: ``0.0`` with a right-half-plane pole
            (:func:`_has_rhp_pole`); ``None`` when the gain never reaches
            0 dB.  ``scheme.unstable_needs_crossing`` says which of the two
            a loop that is both reads.
        """
        unstable = _has_rhp_pole(self._poles)
        if unstable and not self._scheme.unstable_needs_crossing:
            return 0.0
        crossings = self._zero_db_crossings
        if not crossings:
            return None
        if unstable:
            return 0.0
        margins = [pm for _w, pm in crossings]
        if self._scheme.near_crossings_count:
            margins += _near_crossing_margins(self._sweep_points, crossings[0][0])
        return min(margins)

    @property
    def damping(self) -> float:
        """How well damped the loop's poles are: the worst damping ``ζ``.

        ζ = −Re(p) / \\|p\\|  (the cosine of a pole's angle from the negative
        real axis)

        ``1`` is a real pole, ``0`` a pole pair that rings forever, below ``0``
        unstable.  The dominant (slowest) pole is skipped.  The sizer aims an
        RNMC loop at ``0.7``, the Butterworth placement (no resonance peak).

        :returns: The lowest ``ζ`` over the non-dominant poles; negative when
            any is in the right half plane.
        """
        non_dominant = sorted(self._poles, key=abs)[1:]
        return float(min((-p.real / abs(p) for p in non_dominant if abs(p) > 0),
                         default=1.0))


# ---------------------------------------------------------------------------
# Internals: building the circuit
# ---------------------------------------------------------------------------

class _Network:
    """Nodal equations ``(G + s·C)·v = b`` of a small-signal circuit, built
    one element at a time.  A node is an index; ``None`` is ground."""

    def __init__(self):
        self.n = 0
        self._g: list[tuple[int, int, float]] = []
        self._c: list[tuple[int, int, float]] = []
        self._b: dict[int, float] = {}

    def node(self) -> int:
        """Add a node and return its index."""
        self.n += 1
        return self.n - 1

    @staticmethod
    def _stamp(entries, i, j, x):
        """Add a two-terminal element of value ``x`` between ``i`` and ``j``
        to ``entries``, the standard nodal-analysis pattern."""
        entries.append((i, i, x))
        if j is not None:
            entries += [(j, j, x), (i, j, -x), (j, i, -x)]

    def cap(self, i, j, c):
        """Capacitor ``c`` in F between nodes ``i`` and ``j``."""
        self._stamp(self._c, i, j, c)

    def conductance(self, i, j, g):
        """Conductance ``g`` in A/V between nodes ``i`` and ``j``."""
        self._stamp(self._g, i, j, g)

    def vccs(self, i, ctrl, gm):
        """Current ``gm·v[ctrl]`` drawn out of node ``i`` (a transistor's
        controlled current)."""
        self._g.append((i, ctrl, gm))

    def input_stage(self, gm, g, c) -> int:
        """The input pair: current ``gm·Vin`` (inverting, ``Vin = 1``) into a
        new node with conductance ``g`` and capacitance ``c``.  Returns the
        node."""
        v = self.node()
        self._b[v] = -gm
        self.conductance(v, None, g)
        self.cap(v, None, c)
        return v

    def stage(self, ctrl, gm, g, c, *, inverting=True, mirror_pole_hz=None) -> int:
        """A gain stage driven by node ``ctrl`` into a new node with
        conductance ``g`` and capacitance ``c``.

        A non-inverting stage with a current mirror passes through it first:
        an extra node that follows ``v[ctrl]`` behind a pole at
        ``mirror_pole_hz``.  Returns the output node.
        """
        if mirror_pole_hz:
            m = self.node()
            self.vccs(m, ctrl, -1.0)
            self.conductance(m, None, 1.0)
            self.cap(m, None, 1.0 / (2.0 * math.pi * mirror_pole_hz))
            ctrl = m
        v = self.node()
        self.vccs(v, ctrl, gm if inverting else -gm)
        self.conductance(v, None, g)
        self.cap(v, None, c)
        return v

    def branch(self, i, j, c, r):
        """Compensation cap ``c`` from ``i`` to ``j``, through resistor ``r``
        in Ω when it is non-zero."""
        if not r:
            self.cap(i, j, c)
            return
        x = self.node()
        self.cap(i, x, c)
        self.conductance(x, j, 1.0 / r)

    def load(self, v, cl_f, buffer) -> int:
        """Load ``cl_f`` on node ``v``, or on a source-follower ``buffer``'s
        output, which the follower drives from ``v``.  Returns the node that
        carries the load."""
        if buffer is None:
            self.cap(v, None, cl_f)
            return v
        gm_f, cgs_f, g_f = buffer
        out = self.node()
        self.cap(v, out, cgs_f)
        self.vccs(out, v, -gm_f)
        self.conductance(out, None, gm_f + g_f)
        self.cap(out, None, cl_f)
        return out

    def matrices(self):
        """The assembled ``(G, C, b)`` as numpy arrays."""
        G, C, b = np.zeros((self.n, self.n)), np.zeros((self.n, self.n)), np.zeros(self.n)
        for m, entries in ((G, self._g), (C, self._c)):
            for i, j, x in entries:
                m[i, j] += x
        for i, x in self._b.items():
            b[i] = x
        return G, C, b


def _floored(stages):
    """``stages`` with each output conductance floored at ``1e-9·gm``: a zero
    one would make the DC gain infinite, and the floor keeps it finite
    without moving anything near crossover."""
    return tuple(SmallSignalStage(s.gm, max(s.g, 1e-9 * s.gm), s.c) for s in stages)


def _half_circuit(net, drive, scheme, stages, caps_f, cl_f, mirror_pole_hz,
                  buffer, comp_r):
    """Add one half circuit to ``net``, wired as ``scheme`` describes.

    :param net: The :class:`_Network` to add to.
    :param drive: The input pair's transconductance for this half in A/V
        (signed; half of ``gm1`` per side when fully differential).
    :param scheme: The :class:`CompensationScheme` to wire.
    :param stages: Floored stages, input pair first; the first's ``gm`` is
        replaced by ``drive``.
    :param caps_f: Each compensation cap in F, in ``scheme.cap_nodes`` order.
    :param cl_f: Load capacitance in F.
    :param mirror_pole_hz: Pole in Hz of the non-inverting stage's current
        mirror, or ``None`` when it has none.
    :param buffer: The output :class:`Buffer`, or ``None``.
    :param comp_r: Resistor in Ω in series with each cap (``0.0`` for a bare
        cap), in ``scheme.cap_nodes`` order.
    :returns: ``(first-stage node, node carrying CL)``.
    """
    first, *later = stages
    nodes = [net.input_stage(drive, first.g, first.c)]
    for s, inverting in zip(later, scheme.inverting):
        nodes.append(net.stage(nodes[-1], s.gm, s.g, s.c, inverting=inverting,
                               mirror_pole_hz=None if inverting else mirror_pole_hz))
    for (start, end), c_f, r_ohm in zip(scheme.cap_nodes, caps_f, comp_r):
        net.branch(nodes[start], nodes[end], c_f, r_ohm)
    return nodes[0], net.load(nodes[-1], cl_f, buffer)


def _amplifier_network(scheme, stages, caps_f, cl_f, mirror_pole_hz, buffer,
                       comp_r, cmfb):
    """Nodal equations of an amplifier wired as ``scheme`` (``Vin = 1``).

    ``stages`` are floored; every parameter is as for
    :class:`OpenLoopResponse`, whose docstring also says how one or two
    halves in ``comp_r`` are built and coupled.

    :returns: ``(G, C, b, out)``; ``out`` is the output node, or the ``(p, n)``
        pair of nodes.
    """
    net = _Network()
    gm1 = stages[0].gm

    def half(drive, r):
        return _half_circuit(net, drive, scheme, stages, caps_f, cl_f,
                             mirror_pole_hz, buffer, r)
    if len(comp_r) == 1:
        _v1, out = half(gm1, comp_r[0])
        return (*net.matrices(), out)
    (v1p, op), (v1n, on) = half(gm1 / 2.0, comp_r[0]), half(-gm1 / 2.0, comp_r[1])
    if cmfb:
        gm_c, pole_hz = cmfb
        m = net.node()
        net.vccs(m, op, -0.5)
        net.vccs(m, on, -0.5)
        net.conductance(m, None, 1.0)
        if pole_hz:
            net.cap(m, None, 1.0 / (2.0 * math.pi * pole_hz))
        net.vccs(v1p, m, -gm_c)
        net.vccs(v1n, m, -gm_c)
    return (*net.matrices(), (op, on))


# ---------------------------------------------------------------------------
# Internals: reading the loop
# ---------------------------------------------------------------------------

def _poles(G, C):
    """Poles of the circuit in rad/s: the roots of ``det(G + s·C) = 0``,
    found as the eigenvalues of ``−C⁻¹G``.

    A node with no capacitance (a compensation cap's junction with its
    resistor, when no parasitic is given) makes ``C`` singular: a pole at
    infinity.  Adding ``1e-9`` of the largest capacitance to every node then
    puts it far past everything else instead.
    """
    try:
        return np.linalg.eigvals(-np.linalg.solve(C, G))
    except np.linalg.LinAlgError:
        floor = 1e-9 * np.abs(C).max() * np.eye(len(C))
        return np.linalg.eigvals(-np.linalg.solve(C + floor, G))


def _has_rhp_pole(poles) -> bool:
    """Whether any pole is in the right half plane (the loop is unstable).

    A pole below ``1e-12`` of the fastest one does not count: its sign is
    numerical round-off.  That is the dominant pole of a loop whose output
    conductances were floored (DC gain ~1e27).
    """
    resolved = 1e-12 * max((abs(p) for p in poles), default=0.0)
    return any(p.real > 1e-9 * abs(p) and abs(p) > resolved for p in poles)


def _open_loop_gain(G, C, b, out, w):
    """Open-loop gain ``A(jω)`` at each angular frequency in ``w``.

    :param out: The output node, or a ``(p, n)`` pair read as ``v[p] − v[n]``.
    :returns: Complex gains, signed so that ``A(0) > 0``.
    """
    def read(v):
        return v[..., out[0]] - v[..., out[1]] if isinstance(out, tuple) else v[..., out]
    rhs = np.broadcast_to(b[:, None], (len(w), len(b), 1))
    v = np.linalg.solve(G + 1j * w[:, None, None] * C, rhs)[..., 0]
    return read(v) * np.sign(read(np.linalg.solve(G, b)))


def _sweep(G, C, b, out, poles, w_t):
    """Open-loop gain on a 4000-point log sweep, from two decades below the
    lowest of the poles and ``w_t`` (the nominal crossover in rad/s) to two
    decades above the highest.

    :returns: ``(ω in rad/s, |A| in dB, ∠A in °)`` arrays; the phase is
        unwrapped.
    """
    corners = [abs(p) for p in poles if abs(p) > 0] + [w_t]
    w = np.logspace(math.log10(min(corners)) - 2, math.log10(max(corners)) + 2, 4000)
    a = _open_loop_gain(G, C, b, out, w)
    return w, 20.0 * np.log10(np.abs(a)), np.degrees(np.unwrap(np.angle(a)))


def _crossings(G, C, b, out, sweep) -> list[tuple[float, float]]:
    """Every 0 dB crossing of the open loop, in order of frequency.

    Each crossing is found between two points of ``sweep`` (what
    :func:`_sweep` returns) and refined by three false-position steps, so it
    does not depend on the sweep's grid.  The phase is read at the refined
    point.

    :returns: ``(ω in rad/s, phase margin in °)`` per crossing.
    """
    def gain(lw):
        return _open_loop_gain(G, C, b, out, np.array([10.0 ** lw]))[0]

    w, mag_db, phase = sweep
    found = []
    for i in np.nonzero(np.diff(np.sign(mag_db)))[0]:
        x0, x1 = math.log10(w[i]), math.log10(w[i + 1])
        d0, d1 = mag_db[i], mag_db[i + 1]
        for _ in range(3):
            x = x0 + d0 / (d0 - d1) * (x1 - x0)
            d = 20.0 * math.log10(abs(gain(x)))
            if d == 0.0:
                break
            if (d > 0.0) == (d0 > 0.0):
                x0, d0 = x, d
            else:
                x1, d1 = x, d
        lw = x if d == 0.0 else x0 + d0 / (d0 - d1) * (x1 - x0)
        turn = np.angle(gain(lw) / gain(math.log10(w[i])), deg=True)
        found.append((10.0 ** lw, float(180.0 + phase[i] + turn)))
    return found


#: dB under 0 dB within which a gain that climbs back counts as reaching it.
#: Past the first crossing a lightly damped pole pair can lift the gain back
#: toward 0 dB; model and SPICE agree on that peak only to 0.07–0.45 dB, so a
#: peak at −0.3 dB in the model can be a real second crossing (#274).
_NEAR_CROSSING_DB = 0.5


def _near_crossing_margins(sweep, first_crossing) -> list[float]:
    """Phase margin in ° at every sweep point of a near-crossing: a stretch
    where the gain, after dropping more than ``_NEAR_CROSSING_DB`` under 0 dB
    past the first crossing, climbs back within it.

    :param sweep: What :func:`_sweep` returns.
    :param first_crossing: The first 0 dB crossing in rad/s.
    """
    w, mag_db, phase = sweep
    past = w > first_crossing
    dipped = np.cumsum(past & (mag_db < -_NEAR_CROSSING_DB)) > 0
    near = dipped & (mag_db >= -_NEAR_CROSSING_DB) & (mag_db < 0.0)
    return [float(180.0 + p) for p in phase[near]]
