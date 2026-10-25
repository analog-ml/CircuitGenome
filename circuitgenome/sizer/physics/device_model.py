"""Small-signal device-model abstraction shared by both sizing paths.

The op-amp *topology* relationships (gain = ∏ gmᵢ·routᵢ, GBW = gm1/2πCc, phase
margin, CMRR, …) are model-independent — only the device *primitives* differ:

* the **Level-1** square law (``equations.gm``/``gd``/``vds_sat``), used for the
  card-less generic tech, and
* the **gm/Id** lookup table (:class:`~.gmid_lut.GmIdLut`), used for PTM nodes.

Both implement :class:`DeviceModel` so ``compute_requirements`` and
``evaluate_metrics`` in :mod:`~circuitgenome.sizer.sizer` can stay single-source.
``Level1Model`` reproduces the existing numbers exactly (regression-safe);
``GmIdModel`` adds the LUT-backed primitives plus the geometry inversion and
L-selection policy used by the procedural geometry pass.

Two roles drive the gm/Id policy:

* ``"signal"`` — a gain/transconductance device (input pair, gain-stage signal
  transistor): its gm/Id is set by the required gm; L favours a balance of gain
  and ft.
* ``"current_source"`` — a bias/load device (tail, active load, bias gen): no gm
  target; sized at a nominal gm/Id and a longer L for high output resistance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from . import equations as eq
from .gmid_lut import GmIdLut
from ..models import MosfetParams, TechParams

SIGNAL = "signal"
CURRENT_SOURCE = "current_source"
CASCODE = "cascode"


class DeviceModel(Protocol):
    """Small-signal primitives consumed by requirement-derivation and metrics.

    All geometry arguments are in µm and currents in A; ``dtype`` is ``"nmos"``
    or ``"pmos"``.  :class:`Level1Model` implements these from the square law and
    :class:`GmIdModel` from the LUT, so the topology math in
    :mod:`~circuitgenome.sizer.sizer` can call through one interface.
    """

    def gm(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Transconductance gm in A/V at the device's operating point."""
        ...

    def gds(self, dtype: str, w_um: float, l_um: float, ids: float,
            vds: float | None = None) -> float:
        """Output conductance gds in A/V at the device's operating point.

        ``vds`` is the device's quiescent ``|Vds|`` in V when known; ``None``
        keeps the backend's single-point value.
        """
        ...

    def gmb(self, dtype: str, w_um: float, l_um: float, ids: float,
            vsb: float) -> float:
        """Body transconductance gmbs in A/V at reverse body bias ``vsb`` (V).

        A cascode whose source swings against a fixed bulk adds it to its
        ``gm`` in the ``ro·(1 + gm·R)`` boost.
        """
        ...

    def vds_sat(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Minimum \\|VDS\\| for saturation in V."""
        ...

    def cgs(self, dtype: str, w_um: float, l_um: float) -> float:
        """Gate-source capacitance in F, or ``0.0`` when the tech has no ``cox``.

        Bias-independent in both backends: it comes from the geometry and the
        process ``Cox`` alone (see :func:`~.equations.cgs`), which is why it
        takes no current.
        """
        ...

    def cgg(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Total gate capacitance in F at the device's operating point.

        Unlike :meth:`cgs` it follows the inversion level: a wide device in
        weak inversion has a fraction of ``(2/3)·W·L·Cox`` on its gate (a
        100 µm PTM45 device at gm/Id 24: 34 fF, not 166 fF; issue #274).
        """
        ...

    def cdb(self, dtype: str, w_um: float) -> float:
        """Drain junction capacitance in F, or ``0.0`` when the tech has no
        ``cdb`` (see :func:`~.equations.cdb`)."""
        ...

    def cgdo(self, dtype: str, w_um: float) -> float:
        """Gate-drain overlap capacitance in F, or ``0.0`` when the tech has no
        ``cgdo`` (see :func:`~.equations.overlap_cap`)."""
        ...

    def cgso(self, dtype: str, w_um: float) -> float:
        """Gate-source overlap capacitance in F, or ``0.0`` when the tech has
        no ``cgso`` (see :func:`~.equations.overlap_cap`)."""
        ...

    def id_vds_scale(self, dtype: str, w_um: float, l_um: float, ids: float,
                     vds: float) -> float:
        """Drain current at ``|vds|`` (V) over the backend's single-point
        current, at the gate voltage of the ``(W, L, Id)`` operating point.

        Two of these, at a mirror output's Vds and its reference diode's, give
        the current the output really delivers (issue #278).
        """
        ...

    def vgs(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Gate-source voltage in V (signed: +ve NMOS, −ve PMOS)."""
        ...

    def body_vth_shift(self, dtype: str, vsb: float) -> float:
        """Threshold rise in V at source-bulk reverse bias ``vsb`` (body effect).

        Neither backend's ``vgs`` carries it (the LUT is characterised at
        ``Vsb = 0``); ``0.0`` when the tech supplies no ``gamma``.
        """
        ...

    def gm_ceiling(self, dtype: str, ids: float, l_um: float) -> float:
        """Physical upper bound on gm in A/V (weak-inversion limit)."""
        ...

    def gds_estimate(self, dtype: str, ids: float, role: str) -> float:
        """Geometry-free gds in A/V from the device's ``role``.

        Used during requirement derivation, before geometry is chosen, where
        Level-1 returns ``λ·Id`` and gm/Id uses the role's nominal operating
        point (see :data:`SIGNAL` / :data:`CURRENT_SOURCE`).
        """
        ...

    def realized_gm(self, dtype: str, gm_req: float, ids: float) -> float:
        """The gm in A/V that geometry will actually deliver for ``gm_req``.

        A requirement derived from one stage feeds the next one's requirement
        (PM needs the gm1 the input pair will really have, not the gm1 that was
        asked for).  Where the geometry step rounds up to a discrete grid or
        floors the operating point, the delivered gm overshoots the request and
        the model says so; otherwise ``gm_req`` comes back unchanged.
        """
        ...


def _params(tech: TechParams, dtype: str) -> MosfetParams:
    """Return the :class:`~.models.MosfetParams` for ``dtype`` (nmos/pmos)."""
    return tech.nmos if dtype == "nmos" else tech.pmos


# --------------------------------------------------------------------------- #
# Level-1 (square law) — wraps equations.* with identical numerics
# --------------------------------------------------------------------------- #
class Level1Model:
    """Shichman-Hodges primitives — byte-for-byte the current generic behaviour."""

    def __init__(self, tech: TechParams):
        """Bind the technology whose ``µCox``/``vth``/``λ`` drive the square law."""
        self.tech = tech

    def gm(self, dtype, w_um, l_um, ids):
        """Square-law gm ``√(2·µCox·(W/L)·|Id|)`` (:func:`~.equations.gm`)."""
        return eq.gm(_params(self.tech, dtype).mu_cox, w_um, l_um, ids)

    def gds(self, dtype, w_um, l_um, ids, vds=None):
        """Level-1 gds ``λ·|Id|`` — geometry- and ``vds``-independent."""
        return eq.gd(_params(self.tech, dtype).lam, ids)

    def gmb(self, dtype, w_um, l_um, ids, vsb):
        """``0.0``: the Level-1 card this backend is verified against is
        emitted with ``gamma=0`` (see ``verify.deck._emit_model``)."""
        return 0.0

    def vds_sat(self, dtype, w_um, l_um, ids):
        """Square-law saturation overdrive (:func:`~.equations.vds_sat`)."""
        return eq.vds_sat(_params(self.tech, dtype).mu_cox, w_um, l_um, ids)

    def cgs(self, dtype, w_um, l_um):
        """``(2/3)·W·L·Cox`` from the tech's ``cox`` (:func:`~.equations.cgs`)."""
        return eq.cgs(_params(self.tech, dtype).cox, w_um, l_um)

    def cgg(self, dtype, w_um, l_um, ids):
        """:meth:`cgs`: the square law carries no inversion level."""
        return self.cgs(dtype, w_um, l_um)

    def cdb(self, dtype, w_um):
        """``W·cdb`` from the tech's ``cdb`` (:func:`~.equations.cdb`)."""
        return eq.cdb(_params(self.tech, dtype).cdb, w_um)

    def cgdo(self, dtype, w_um):
        """``W·cgdo`` from the tech's ``cgdo`` (:func:`~.equations.overlap_cap`)."""
        return eq.overlap_cap(_params(self.tech, dtype).cgdo, w_um)

    def cgso(self, dtype, w_um):
        """``W·cgso`` from the tech's ``cgso`` (:func:`~.equations.overlap_cap`)."""
        return eq.overlap_cap(_params(self.tech, dtype).cgso, w_um)

    def id_vds_scale(self, dtype, w_um, l_um, ids, vds):
        """``1.0``: like :meth:`gds`, the Level-1 model is ``vds``-independent."""
        return 1.0

    def vgs(self, dtype, w_um, l_um, ids):
        """Square-law gate-source voltage (:func:`~.equations.vgs_from_ids`)."""
        p = _params(self.tech, dtype)
        return eq.vgs_from_ids(p.mu_cox, w_um, l_um, ids, p.vth)

    def body_vth_shift(self, dtype, vsb):
        """Body-effect ``ΔVth`` from the tech's ``gamma``/``phi`` (:func:`~.equations.body_vth_shift`)."""
        p = _params(self.tech, dtype)
        return eq.body_vth_shift(p.gamma, p.phi, vsb)

    def gm_ceiling(self, dtype, ids, l_um):
        """Weak-inversion gm ceiling ``25·|Id|`` (dtype/L-independent)."""
        return eq.gm_ceiling(ids)

    def gds_estimate(self, dtype, ids, role):
        """Geometry-free gds ``λ·|Id|`` — ``role`` is irrelevant under the square law."""
        return eq.gd(_params(self.tech, dtype).lam, ids)

    def realized_gm(self, dtype, gm_req, ids):
        """``gm_req`` inflated to the integer W grid CP-SAT will round up to.

        CP-SAT constrains ``gm ≥ gm_req`` over integer W steps, so the solved
        pair delivers the ceiling, not the request.  A downstream requirement
        (the PM-derived gm2) must anticipate that or the built amplifier misses
        the spec its own constraints were satisfied against.
        """
        p = _params(self.tech, dtype)
        lhs = 2.0 * p.mu_cox * ids
        l_min, w_step = self.tech.length.min, self.tech.width.step
        w_um = math.ceil(gm_req ** 2 * l_min / (lhs * w_step)) * w_step
        w_um = min(max(w_um, self.tech.width.min), self.tech.width.max)
        return math.sqrt(lhs * w_um / l_min)


# --------------------------------------------------------------------------- #
# gm/Id (lookup table)
# --------------------------------------------------------------------------- #
@dataclass
class GmIdPolicy:
    """L / gm-Id selection policy for the procedural gm/Id sizer.

    Defaults are starting points tuned during SPICE validation.  At L_min the
    45 nm intrinsic gain gm/gds is only ~7, so gain-critical (``signal``) devices
    use a moderate L multiple; current sources use a longer L for output
    resistance and a low gm/Id for headroom.
    """
    signal_l_mult: float = 2.0       # signal-device L as a multiple of L_min
    cs_l_mult: float = 4.0           # current-source L as a multiple of L_min
    cs_gmid: float = 10.0            # nominal current-source gm/Id (1/V)
    signal_nominal_gmid: float = 14.0  # gm/Id used for pre-geometry gds estimate
    cascode_l_mult: float = 3.0      # cascode-device L as a multiple of L_min
    cascode_gmid: float = 8.0        # cascode gm/Id (strong inversion → small Vdsat)


@dataclass
class GeomResult:
    """Geometry computed for one device by :meth:`GmIdModel.geometry_for`.

    :param w_um: Gate width in µm.
    :param l_um: Gate length in µm (from the L-policy, grid-snapped).
    :param gm_id: Operating transconductance efficiency gm/Id in 1/V.
    :param gm_id_capped: ``True`` when the gm/Id target was clamped to the
        table's weak-inversion ceiling (the design will fall short of its gm).
    """
    w_um: float
    l_um: float
    gm_id: float
    gm_id_capped: bool


class GmIdModel:
    """LUT-backed primitives + geometry inversion and L-policy."""

    def __init__(self, tech: TechParams, lut: GmIdLut, policy: GmIdPolicy | None = None):
        """Bind a tech, its gm/Id ``lut``, and an L-policy (default :class:`GmIdPolicy`)."""
        self.tech = tech
        self.lut = lut
        self.policy = policy or GmIdPolicy()

    # -- geometry / grid helpers -------------------------------------------
    def role_length(self, role: str) -> float:
        """Channel length for ``role`` from the L-policy multiplier (µm, snapped)."""
        mult = {CURRENT_SOURCE: self.policy.cs_l_mult,
                CASCODE: self.policy.cascode_l_mult}.get(role, self.policy.signal_l_mult)
        return self.length_for(mult)

    def length_for(self, l_mult: float) -> float:
        """Channel length for an explicit L multiple of ``length.min`` (µm, snapped)."""
        return self.tech.length.snap(l_mult * self.tech.length.min)

    def _role_gm_id(self, role: str) -> float:
        """Nominal gm/Id for a non-signal role (current source / cascode)."""
        return (self.policy.cascode_gmid if role == CASCODE
                else self.policy.cs_gmid)

    def _gm_id_at(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Recover the operating gm/Id from a solved (W, L, Id) via the LUT inverse."""
        if w_um <= 0:
            return self.policy.cs_gmid
        return self.lut.gm_id_from_idw(dtype, abs(ids) / w_um, l_um)

    # -- DeviceModel primitives (geometry known) ---------------------------
    def gm(self, dtype, w_um, l_um, ids):
        """Transconductance ``(gm/Id)·|Id|`` at the geometry's operating point."""
        return self._gm_id_at(dtype, w_um, l_um, ids) * abs(ids)

    def gds(self, dtype, w_um, l_um, ids, vds=None):
        """Output conductance ``gm/(gm/gds)`` read from the LUT at the operating gm/Id.

        The table holds gds at ``Vds = Vdd/2``; a known ``vds`` scales it to
        the device's own drain bias (:meth:`~.gmid_lut.GmIdLut.gds_scale`).
        A device just above its saturation knee -- under a cascode -- has
        several times the Vdd/2 value (issue #273).  The read stops at the
        device's Vdsat: below it the device would be in triode, so the level
        estimate that supplied ``vds`` (every device saturated) has failed,
        and the knee value keeps the gain on the over-predicting side.
        """
        gm_id = self._gm_id_at(dtype, w_um, l_um, ids)
        gm = gm_id * abs(ids)
        gds = gm / self.lut.gm_gds(dtype, gm_id, l_um)
        if vds is not None:
            vds = max(vds, self.lut.vdsat(dtype, gm_id, l_um))
            gds *= self.lut.gds_scale(dtype, gm_id, l_um, vds)
        return gds

    def gmb(self, dtype, w_um, l_um, ids, vsb):
        """Body transconductance ``gm·(gmbs/gm)`` read from the LUT at ``vsb``."""
        gm_id = self._gm_id_at(dtype, w_um, l_um, ids)
        return gm_id * abs(ids) * self.lut.gmb_gm(dtype, gm_id, l_um, vsb)

    def vds_sat(self, dtype, w_um, l_um, ids):
        """Saturation overdrive ``VDS,sat`` read from the LUT (V)."""
        return self.lut.vdsat(dtype, self._gm_id_at(dtype, w_um, l_um, ids), l_um)

    def cgs(self, dtype, w_um, l_um):
        """``(2/3)·W·L·Cox`` from the tech's ``cox`` (:func:`~.equations.cgs`).

        Read from ``cox`` rather than inverted out of the LUT's ``ft``: the two
        agree to within tens of percent on the PDK nodes, and taking the process
        constant keeps this primitive identical across both backends.
        """
        return eq.cgs(_params(self.tech, dtype).cox, w_um, l_um)

    def cgg(self, dtype, w_um, l_um, ids):
        """``gm/(2π·fT)`` read from the LUT at the operating gm/Id -- the
        simulator's own gate capacitance there -- or :meth:`cgs` when the
        table has no ``fT``."""
        gm_id = self._gm_id_at(dtype, w_um, l_um, ids)
        ft = self.lut.ft(dtype, gm_id, l_um)
        if ft <= 0:
            return self.cgs(dtype, w_um, l_um)
        return gm_id * abs(ids) / (2.0 * math.pi * ft)

    def cdb(self, dtype, w_um):
        """``W·cdb`` from the tech's ``cdb`` (:func:`~.equations.cdb`)."""
        return eq.cdb(_params(self.tech, dtype).cdb, w_um)

    def cgdo(self, dtype, w_um):
        """``W·cgdo`` from the tech's ``cgdo`` (:func:`~.equations.overlap_cap`)."""
        return eq.overlap_cap(_params(self.tech, dtype).cgdo, w_um)

    def cgso(self, dtype, w_um):
        """``W·cgso`` from the tech's ``cgso`` (:func:`~.equations.overlap_cap`)."""
        return eq.overlap_cap(_params(self.tech, dtype).cgso, w_um)

    def id_vds_scale(self, dtype, w_um, l_um, ids, vds):
        """:meth:`~.gmid_lut.GmIdLut.id_scale` at the operating gm/Id."""
        return self.lut.id_scale(dtype, self._gm_id_at(dtype, w_um, l_um, ids), l_um, vds)

    def vgs(self, dtype, w_um, l_um, ids):
        """Gate-source voltage from the LUT, signed +ve NMOS / −ve PMOS."""
        mag = self.lut.vgs(dtype, self._gm_id_at(dtype, w_um, l_um, ids), l_um)
        return mag if dtype == "nmos" else -mag

    def body_vth_shift(self, dtype, vsb):
        """Body-effect ``ΔVth`` from the tech's ``gamma``/``phi`` (:func:`~.equations.body_vth_shift`)."""
        p = _params(self.tech, dtype)
        return eq.body_vth_shift(p.gamma, p.phi, vsb)

    def gm_ceiling(self, dtype, ids, l_um):
        """Weak-inversion gm ceiling ``max_gm_id·|Id|`` from the table."""
        return self.lut.max_gm_id(dtype, l_um) * abs(ids)

    def gds_estimate(self, dtype, ids, role):
        """Geometry-free gds from the role's nominal operating point."""
        l_um = self.role_length(role)
        gm_id = (self.policy.signal_nominal_gmid if role == SIGNAL
                 else self._role_gm_id(role))
        gm = gm_id * abs(ids)
        return gm / self.lut.gm_gds(dtype, gm_id, l_um)

    def realized_gm(self, dtype, gm_req, ids):
        """``gm_req``, floored at the table's strongest-inversion gm/Id.

        The forward pass solves W from the gm/Id target, so there is no grid
        ceiling to anticipate — but :meth:`geometry_for` never goes below the
        table's smallest gm/Id, so a request under ``gm_id_axis[0]·|Id|`` is
        delivered at that floor (e.g. gf180 at 10 µA: 60 µS for a 16 µS ask).
        """
        return max(gm_req, float(self.lut.gm_id_axis[0]) * abs(ids))

    # -- geometry inversion (procedural sizer) -----------------------------
    def geometry_for(
        self, dtype: str, ids: float, role: str, gm_target: float | None = None,
        *, gm_id: float | None = None, l_um: float | None = None,
        gm_id_min: float | None = None,
    ) -> GeomResult:
        """Compute (W, L) for a device from its role and (optional) gm target.

        ``current_source``/``cascode`` devices use the policy gm/Id (cascodes at a
        smaller-Vdsat region); ``signal`` devices set gm/Id = gm_target/Id, clamped
        to the table's weak-inversion ceiling.

        ``gm_id`` and ``l_um`` are per-device design-intent overrides (from the
        block registry): an explicit gm/Id region for a non-signal device and an
        explicit channel length.  When ``None`` they fall back to the role policy,
        so the default behaviour is unchanged.  ``gm_id`` is ignored for a signal
        device with a gm target — its gm/Id is always solved from the spec.

        ``gm_id_min`` floors the operating gm/Id regardless of role — used to
        cap VDS_sat for output-swing (weaker inversion = lower Vdsat).  Raising
        a solved signal gm/Id is spec-safe: gm = (gm/Id)·Id only grows.
        """
        l_um = l_um if l_um is not None else self.role_length(role)
        capped = False
        if role != SIGNAL or not gm_target or ids <= 0:
            gm_id = gm_id if gm_id is not None else self._role_gm_id(role)
        else:
            gm_id = gm_target / abs(ids)
            ceiling = self.lut.max_gm_id(dtype, l_um)
            if gm_id > ceiling:
                gm_id, capped = ceiling, True
            gm_id = max(gm_id, float(self.lut.gm_id_axis[0]))
        if gm_id_min is not None:
            gm_id = max(gm_id, gm_id_min)
        idw = self.lut.id_per_w(dtype, gm_id, l_um)
        w_um = abs(ids) / idw if idw > 0 else self.tech.width.max
        return GeomResult(w_um=w_um, l_um=l_um, gm_id=gm_id, gm_id_capped=capped)
