"""Small-signal device-model abstraction shared by both sizing paths.

The op-amp *topology* relationships (gain = ∏ gmᵢ·routᵢ, GBW = gm1/2πCc, phase
margin, CMRR, …) are model-independent — only the device *primitives* differ:

* the **Level-1** square law (``equations.gm``/``gd``/``vds_sat``), used for the
  card-less generic tech, and
* the **gm/Id** lookup table (:class:`~.gmid_lut.GmIdLut`), used for PTM nodes.

Both implement :class:`DeviceModel` so ``_compute_requirements`` and
``_evaluate_metrics`` in :mod:`~circuitgenome.sizer.sizer` can stay single-source.
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

from dataclasses import dataclass
from typing import Protocol

from . import equations as eq
from .gmid_lut import GmIdLut
from .models import MosfetParams, TechParams

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

    is_gmid: bool

    def gm(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Transconductance gm in A/V at the device's operating point."""
        ...

    def gds(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Output conductance gds in A/V at the device's operating point."""
        ...

    def vds_sat(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Minimum \\|VDS\\| for saturation in V."""
        ...

    def vgs(self, dtype: str, w_um: float, l_um: float, ids: float) -> float:
        """Gate-source voltage in V (signed: +ve NMOS, −ve PMOS)."""
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


def _params(tech: TechParams, dtype: str) -> MosfetParams:
    """Return the :class:`~.models.MosfetParams` for ``dtype`` (nmos/pmos)."""
    return tech.nmos if dtype == "nmos" else tech.pmos


# --------------------------------------------------------------------------- #
# Level-1 (square law) — wraps equations.* with identical numerics
# --------------------------------------------------------------------------- #
class Level1Model:
    """Shichman-Hodges primitives — byte-for-byte the current generic behaviour."""

    is_gmid = False

    def __init__(self, tech: TechParams):
        """Bind the technology whose ``µCox``/``vth``/``λ`` drive the square law."""
        self.tech = tech

    def gm(self, dtype, w_um, l_um, ids):
        """Square-law gm ``√(2·µCox·(W/L)·|Id|)`` (:func:`~.equations.gm`)."""
        return eq.gm(_params(self.tech, dtype).mu_cox, w_um, l_um, ids)

    def gds(self, dtype, w_um, l_um, ids):
        """Level-1 gds ``λ·|Id|`` — geometry-independent (W/L ignored)."""
        return eq.gd(_params(self.tech, dtype).lam, ids)

    def vds_sat(self, dtype, w_um, l_um, ids):
        """Square-law saturation overdrive (:func:`~.equations.vds_sat`)."""
        return eq.vds_sat(_params(self.tech, dtype).mu_cox, w_um, l_um, ids)

    def vgs(self, dtype, w_um, l_um, ids):
        """Square-law gate-source voltage (:func:`~.equations.vgs_from_ids`)."""
        p = _params(self.tech, dtype)
        return eq.vgs_from_ids(p.mu_cox, w_um, l_um, ids, p.vth)

    def gm_ceiling(self, dtype, ids, l_um):
        """Weak-inversion gm ceiling ``25·|Id|`` (dtype/L-independent)."""
        return eq.gm_ceiling(ids)

    def gds_estimate(self, dtype, ids, role):
        """Geometry-free gds ``λ·|Id|`` — ``role`` is irrelevant under the square law."""
        return eq.gd(_params(self.tech, dtype).lam, ids)


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

    is_gmid = True

    def __init__(self, tech: TechParams, lut: GmIdLut, policy: GmIdPolicy | None = None):
        """Bind a tech, its gm/Id ``lut``, and an L-policy (default :class:`GmIdPolicy`)."""
        self.tech = tech
        self.lut = lut
        self.policy = policy or GmIdPolicy()

    # -- geometry / grid helpers -------------------------------------------
    def _snap_l(self, l_um: float) -> float:
        """Snap a length to the tech length grid, clamped to its bounds."""
        g = self.tech.length
        v = round(l_um / g.step) * g.step
        return float(min(max(v, g.min), g.max))

    def role_length(self, role: str) -> float:
        """Channel length for ``role`` from the L-policy multiplier (µm, snapped)."""
        mult = {CURRENT_SOURCE: self.policy.cs_l_mult,
                CASCODE: self.policy.cascode_l_mult}.get(role, self.policy.signal_l_mult)
        return self.length_for(mult)

    def length_for(self, l_mult: float) -> float:
        """Channel length for an explicit L multiple of ``length.min`` (µm, snapped)."""
        return self._snap_l(l_mult * self.tech.length.min)

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

    def gds(self, dtype, w_um, l_um, ids):
        """Output conductance ``gm/(gm/gds)`` read from the LUT at the operating gm/Id."""
        gm_id = self._gm_id_at(dtype, w_um, l_um, ids)
        gm = gm_id * abs(ids)
        return gm / self.lut.gm_gds(dtype, gm_id, l_um)

    def vds_sat(self, dtype, w_um, l_um, ids):
        """Saturation overdrive ``VDS,sat`` read from the LUT (V)."""
        return self.lut.vdsat(dtype, self._gm_id_at(dtype, w_um, l_um, ids), l_um)

    def vgs(self, dtype, w_um, l_um, ids):
        """Gate-source voltage from the LUT, signed +ve NMOS / −ve PMOS."""
        mag = self.lut.vgs(dtype, self._gm_id_at(dtype, w_um, l_um, ids), l_um)
        vth = _params(self.tech, dtype).vth
        return mag if vth >= 0 else -mag

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

    # -- geometry inversion (procedural sizer) -----------------------------
    def geometry_for(
        self, dtype: str, ids: float, role: str, gm_target: float | None = None,
        *, gm_id: float | None = None, l_um: float | None = None,
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
        idw = self.lut.id_per_w(dtype, gm_id, l_um)
        w_um = abs(ids) / idw if idw > 0 else self.tech.width.max
        return GeomResult(w_um=w_um, l_um=l_um, gm_id=gm_id, gm_id_capped=capped)


def build_device_model(tech: TechParams) -> DeviceModel:
    """Select the gm/Id model when the tech carries a LUT, else Level-1."""
    if getattr(tech, "gmid_lut", None):
        return GmIdModel(tech, GmIdLut(tech.gmid_lut))
    return Level1Model(tech)
