"""Procedural geometry assignment for the gm/Id sizing path.

Replaces CP-SAT for PTM nodes: geometry is *computed*, not searched.  With ``Id``
fixed by KCL and ``gm/Id = gm_req/Id`` fixed, the LUT gives ``Id/W → W``
directly, so this is a deterministic forward pass:

1. per-device ``(W, L)`` from :meth:`GmIdModel.geometry_for`;
2. snap ``W`` to the width grid (``L`` is already grid-aligned by the L-policy);
3. **symmetry** — matched pairs get the anchor's geometry (plain assignment);
4. **mirror ratios** — each output's ``W`` is the *exact* current ratio times the
   diode-connected reference's ``W`` at matched ``L`` (no ``Fraction`` rounding).

Symmetry is applied before mirror ratios; on the rare device that is both, the
mirror constraint wins (it is the bias-current-correctness fix).  Both rules are
self-consistent for matched pairs, so the order only matters for resolution, not
for the result.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from ..shared.models import TechParams, TransistorSizing

# Same matched-pair slots CP-SAT treats as symmetric (constraints.build_model).
_SYMMETRY_SLOTS = frozenset({"input_pair", "load", "tail_current"})
# FD cross-slot matched pairs (second_stage_p ↔ _n, third_stage_p ↔ _n).
_FD_PAIRS = (("second_stage_p", "second_stage_n"),
             ("third_stage_p", "third_stage_n"))


def assign_geometry_gmid(
    model: GmIdModel,
    all_transistors: dict[str, tuple],      # ref → (Device, slot_name)
    slot_transistors: dict[str, list],      # slot_name → [Device, ...]
    ids_map: dict[str, float],              # ref → IDS in A
    role_map: dict[str, str],               # ref → SIGNAL | CURRENT_SOURCE
    gm_target_map: dict[str, float],        # ref → required gm in A/V (signal devices)
    tech: TechParams,
) -> tuple[dict[str, TransistorSizing], list[str]]:
    """Return ``({ref: TransistorSizing}, warnings)`` for the gm/Id path."""
    g = tech.width
    warnings: list[str] = []

    def snap_w(w_um: float) -> float:
        v = round(w_um / g.step) * g.step
        return float(min(max(v, g.min), g.max))

    # --- 1+2: per-device geometry from the LUT, W snapped ---
    W: dict[str, float] = {}
    L: dict[str, float] = {}
    for ref, (device, _slot) in all_transistors.items():
        role = role_map[ref]
        geo = model.geometry_for(
            device.type, ids_map[ref], role, gm_target_map.get(ref)
        )
        W[ref] = snap_w(geo.w_um)
        L[ref] = geo.l_um
        if geo.gm_id_capped:
            warnings.append(
                f"{ref}: required gm/Id exceeds the weak-inversion ceiling at its "
                f"bias current — increase the stage current or relax GBW/gain "
                f"(the design will fall short).")

    # --- 3: symmetry (matched pairs share the anchor's geometry) ---
    def equalize(refs: list[str]) -> None:
        anchor = refs[0]
        for r in refs[1:]:
            W[r], L[r] = W[anchor], L[anchor]

    for slot, devices in slot_transistors.items():
        if slot not in _SYMMETRY_SLOTS:
            continue
        for dtype in ("nmos", "pmos"):
            grp = [d.ref for d in devices if d.type == dtype and d.ref in W]
            if grp:
                equalize(grp)

    for sp, sn in _FD_PAIRS:
        p_devs, n_devs = slot_transistors.get(sp, []), slot_transistors.get(sn, [])
        if not (p_devs and n_devs):
            continue
        for dtype in ("nmos", "pmos"):
            grp = [d.ref for d in (*p_devs, *n_devs) if d.type == dtype and d.ref in W]
            if grp:
                equalize(grp)

    # --- 4: current-mirror ratios (exact, no Fraction approximation) ---
    # Group MOSFETs by (gate-net, type); a diode-connected member (g == d) is the
    # reference.  Output W tracks the current ratio at matched L.
    groups: dict[tuple, list[str]] = {}
    for ref, (device, _slot) in all_transistors.items():
        gate = device.terminals.get("g")
        if gate:
            groups.setdefault((gate, device.type), []).append(ref)
    for members in groups.values():
        if len(members) < 2:
            continue
        diodes = [m for m in members
                  if all_transistors[m][0].terminals.get("g")
                  == all_transistors[m][0].terminals.get("d")]
        if not diodes:
            continue
        ref0 = diodes[0]
        i_ref = ids_map.get(ref0, 0.0)
        if i_ref <= 0:
            continue
        for m in members:
            if m == ref0:
                continue
            i_m = ids_map.get(m, 0.0)
            if i_m <= 0:
                continue
            L[m] = L[ref0]
            W[m] = snap_w((i_m / i_ref) * W[ref0])

    # --- 5: build TransistorSizing with final geometry ---
    sizing: dict[str, TransistorSizing] = {}
    for ref, (device, _slot) in all_transistors.items():
        ids = ids_map[ref]
        sizing[ref] = TransistorSizing(
            ref=ref,
            w_um=W[ref],
            l_um=L[ref],
            ids_a=ids,
            vgs_v=model.vgs(device.type, W[ref], L[ref], ids),
            vds_sat_v=model.vds_sat(device.type, W[ref], L[ref], ids),
        )
    return sizing, warnings
