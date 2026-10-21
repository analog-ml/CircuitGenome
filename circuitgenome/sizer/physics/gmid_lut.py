"""gm/Id lookup table for the procedural PTM sizer.

Loads a committed ``*_gmid.npz`` (produced by ``tools/extract_tech.py --gm-id``)
and provides bilinear interpolation of the canonical gm/Id quantities over a
``(gm/Id, L)`` grid, plus the inverse used at metric-evaluation time.

The table is the source of truth for device physics in the gm/Id path: it
replaces the square-law ``gm``/``gds``/``vds_sat`` and the ``25·Id``
weak-inversion ceiling heuristic with measured BSIM4 behaviour.

Axes (both per polarity, fields shaped ``(n_L, n_gmid)``):
    ``gm_id_axis``  uniform gm/Id grid in 1/V (weak → strong inversion)
    ``l_axis``      channel lengths in µm

Fields: ``id_w`` (A/µm), ``gm_gds`` (V/V), ``ft`` (Hz), ``vdsat`` (V),
``vgs`` (V, magnitude).  All are characterised at one bias point, ``Vds =
Vdd/2`` and ``Vsb = 0``.

Two optional correction tables cover devices far from that point (issue
#273), each a stack of ``(n_L, n_gmid)`` grids over a bias axis:
    ``gds_scale`` over ``vds_axis``  gds at that Vds over gds at Vdd/2, at equal
                                     current density (1 at the reference)
    ``gmb_gm``    over ``vsb_axis``  body transconductance ``gmbs/gm`` at that
                                     reverse body bias
A table without them (an older ``.npz``) reads as ``gds_scale = 1`` and
``gmb_gm = 0``: the single-point behaviour.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_FIELDS = ("id_w", "gm_gds", "ft", "vdsat", "vgs")


def _bracket(axis: np.ndarray, x: float) -> tuple[int, float]:
    """``(j, t)`` with ``x`` clamped into ``axis[j-1] .. axis[j]`` at fraction ``t``."""
    x = float(np.clip(x, axis[0], axis[-1]))
    j = int(np.clip(np.searchsorted(axis, x), 1, len(axis) - 1))
    return j, (x - axis[j - 1]) / (axis[j] - axis[j - 1])


class GmIdLut:
    """Interpolating gm/Id table for both NMOS and PMOS."""

    def __init__(self, path: Path | str):
        """Load the committed ``*_gmid.npz`` table from ``path``."""
        data = np.load(path)
        self.gm_id_axis: np.ndarray = data["gm_id_axis"]
        self.l_axis: np.ndarray = data["l_axis"]
        self._fields: dict[str, dict[str, np.ndarray]] = {
            dtype: {f: data[f"{dtype}_{f}"] for f in _FIELDS}
            for dtype in ("nmos", "pmos")
        }
        self._bias: dict[str, tuple[np.ndarray, dict[str, np.ndarray]]] = {
            field: (data[axis], {dtype: data[f"{dtype}_{field}"]
                                 for dtype in ("nmos", "pmos")})
            for field, axis in (("gds_scale", "vds_axis"), ("gmb_gm", "vsb_axis"))
            if axis in data
        }

    # -- internal helpers ---------------------------------------------------
    def _row(self, dtype: str, field: str, l_um: float) -> np.ndarray:
        """Field as a 1-D array over the gm/Id axis at length ``l_um``.

        Linear interpolation between the two bracketing length rows (clamped to
        the L-axis range).
        """
        la = self.l_axis
        grid = self._fields[dtype][field]
        l = float(np.clip(l_um, la[0], la[-1]))
        j = int(np.searchsorted(la, l))
        if j <= 0:
            return grid[0].copy()
        if j >= len(la):
            return grid[-1].copy()
        l0, l1 = la[j - 1], la[j]
        t = (l - l0) / (l1 - l0) if l1 > l0 else 0.0
        return grid[j - 1] * (1.0 - t) + grid[j] * t

    def _lookup(self, dtype: str, field: str, gm_id: float, l_um: float) -> float:
        """Bilinear interpolation of ``field`` at ``(gm_id, l_um)`` (clamped)."""
        row = self._row(dtype, field, l_um)
        g = float(np.clip(gm_id, self.gm_id_axis[0], self.gm_id_axis[-1]))
        return float(np.interp(g, self.gm_id_axis, row))

    def _bias_lookup(self, dtype: str, field: str, gm_id: float, l_um: float,
                     bias: float, log: bool) -> float | None:
        """``field`` at ``(gm_id, l_um)`` and bias ``bias`` (clamped), or ``None``
        without that table.  Bilinear on each bias layer, then along the bias
        axis -- in ``log`` space for the convex, decade-spanning ``gds_scale``."""
        if field not in self._bias:
            return None
        axis, grids = self._bias[field]
        grid = grids[dtype]                            # (n_bias, n_L, n_gmid)
        la, ga = self.l_axis, self.gm_id_axis
        jl, tl = _bracket(la, l_um)
        rows = grid[:, jl - 1] * (1.0 - tl) + grid[:, jl] * tl
        jg, tg = _bracket(ga, gm_id)
        layers = rows[:, jg - 1] * (1.0 - tg) + rows[:, jg] * tg
        x = float(np.clip(bias, axis[0], axis[-1]))
        if log:
            return float(np.exp(np.interp(x, axis, np.log(layers))))
        return float(np.interp(x, axis, layers))

    # -- public reads -------------------------------------------------------
    def id_per_w(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Drain current per µm of width (A/µm) at the operating point."""
        return self._lookup(dtype, "id_w", gm_id, l_um)

    def gm_gds(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Intrinsic-gain ratio gm/gds (V/V)."""
        return self._lookup(dtype, "gm_gds", gm_id, l_um)

    def gds_scale(self, dtype: str, gm_id: float, l_um: float, vds: float) -> float:
        """gds at ``|vds|`` (V) over the table's Vdd/2 gds, same current density.

        Rises steeply toward the saturation knee: a device ~0.1 V above its
        Vdsat (under a cascode) has several times the Vdd/2 gds.  ``1.0`` when
        the table has no Vds axis.
        """
        v = self._bias_lookup(dtype, "gds_scale", gm_id, l_um, abs(vds), log=True)
        return 1.0 if v is None else v

    def gmb_gm(self, dtype: str, gm_id: float, l_um: float, vsb: float) -> float:
        """Body transconductance ratio ``gmbs/gm`` at reverse body bias ``vsb``
        (V); ``0.0`` when the table has no Vsb axis."""
        v = self._bias_lookup(dtype, "gmb_gm", gm_id, l_um, max(vsb, 0.0), log=False)
        return 0.0 if v is None else v

    def ft(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Transition frequency gm/(2π·Cgg) in Hz."""
        return self._lookup(dtype, "ft", gm_id, l_um)

    def vdsat(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Saturation overdrive ``VDS,sat`` in V."""
        return self._lookup(dtype, "vdsat", gm_id, l_um)

    def vgs(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Gate-source voltage magnitude in V."""
        return self._lookup(dtype, "vgs", gm_id, l_um)

    def max_gm_id(self, dtype: str, l_um: float) -> float:
        """Largest gm/Id the table represents (weak-inversion ceiling), 1/V."""
        return float(self.gm_id_axis[-1])

    def gm_id_from_idw(self, dtype: str, id_w: float, l_um: float) -> float:
        """Recover gm/Id from a current density ``id_w`` (A/µm) at length ``l``.

        Inverse of :meth:`id_per_w`: ``id_w`` decreases monotonically with gm/Id,
        so we invert the per-length curve.  Used to read back the operating point
        from a solved ``(W, L, Id)`` for accurate metric evaluation.
        """
        curve = self._row(dtype, "id_w", l_um)        # decreasing in gm/Id
        xs = curve[::-1]                               # ascending current density
        ys = self.gm_id_axis[::-1]
        return float(np.interp(id_w, xs, ys))
