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
``vgs`` (V, magnitude).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_FIELDS = ("id_w", "gm_gds", "ft", "vdsat", "vgs")


class GmIdLut:
    """Interpolating gm/Id table for both NMOS and PMOS."""

    def __init__(self, path: Path | str):
        data = np.load(path)
        self.gm_id_axis: np.ndarray = data["gm_id_axis"]
        self.l_axis: np.ndarray = data["l_axis"]
        self._fields: dict[str, dict[str, np.ndarray]] = {
            dtype: {f: data[f"{dtype}_{f}"] for f in _FIELDS}
            for dtype in ("nmos", "pmos")
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

    # -- public reads -------------------------------------------------------
    def id_per_w(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Drain current per µm of width (A/µm) at the operating point."""
        return self._lookup(dtype, "id_w", gm_id, l_um)

    def gm_gds(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Intrinsic-gain ratio gm/gds (V/V)."""
        return self._lookup(dtype, "gm_gds", gm_id, l_um)

    def ft(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Transition frequency gm/(2π·Cgg) in Hz."""
        return self._lookup(dtype, "ft", gm_id, l_um)

    def vdsat(self, dtype: str, gm_id: float, l_um: float) -> float:
        """Saturation overdrive |VDS,sat| in V."""
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
