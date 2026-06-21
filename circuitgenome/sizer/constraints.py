"""
OR-Tools CP-SAT model builder for transistor sizing.

Translates performance requirements into integer linear constraints over
discrete W and L variables (in integer multiples of the grid step).

Key linearisation: the gm lower-bound constraint

    gm ≥ gm_req
    ↔  √(2·µCox·(W/L)·IDS) ≥ gm_req
    ↔  2·µCox·IDS·W  ≥  gm_req²·L          [linear in W, L]

and the VDS_sat upper-bound constraint

    √(2·IDS·L/(µCox·W)) ≤ VDS_sat_max
    ↔  2·IDS·L  ≤  µCox·VDS_sat_max²·W     [linear in W, L]

are both linear once W and L are separate integer variables.  All
floating-point coefficients are scaled by _SCALE = 10¹² before rounding
to integers so that µA/V² and µA magnitudes map to small integer values.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from .models import MosfetParams, TechParams

# Scale factor: converts A²/V² coefficient products to tidy integers.
# With µCox ≈ 90e-6 A/V², IDS ≈ 5e-6 A:
#   2·µCox·IDS·_SCALE = 2·90e-6·5e-6·1e12 = 900  (small integer ✓)
_SCALE = 10**12

# Integer step sizes for W and L are both 1 (in grid-step units).
# Actual µm = integer_value × tech.width.step (or tech.length.step).
# Since steps cancel in the W/L ratio constraints, the constraints are
# independent of the step size.


def _coeff(value: float) -> int:
    """Round a physical coefficient to its integer CP-SAT representation."""
    return round(value * _SCALE)


def build_model(
    transistors: dict[str, tuple],         # ref → (Device, slot_name)
    slot_transistors: dict[str, list],     # slot_name → [Device, ...]
    ids_map: dict[str, float],             # ref → IDS in A
    gm_req_map: dict[str, float],          # ref → required gm in A/V (0 = unconstrained)
    vod_max_map: dict[str, float],         # ref → max VDS_sat in V (inf = unconstrained)
    tech: TechParams,
    symmetry_slots: frozenset[str] = frozenset({"input_pair", "load", "tail_current"}),
) -> tuple[cp_model.CpModel, dict[str, cp_model.IntVar], dict[str, cp_model.IntVar]]:
    """Build the CP-SAT sizing model.

    :param transistors: Deduplicated map of all transistors to size,
        keyed by device reference.
    :param slot_transistors: Per-slot transistor lists (for symmetry grouping).
    :param ids_map: Quiescent drain-source current in A for each transistor.
    :param gm_req_map: Required transconductance lower bound in A/V.
        Entries with value ≤ 0 are skipped.
    :param vod_max_map: Maximum VDS_sat (overdrive) in V.
        Entries with value ≥ a large number are skipped.
    :param tech: Technology parameters supplying µCox, Vth, λ and grids.
    :param symmetry_slots: Slot names within which same-type transistors are
        constrained to equal W and equal L (matched pairs).
    :returns: ``(model, W_vars, L_vars)`` where ``W_vars`` and ``L_vars``
        are dicts mapping device reference to the corresponding
        :class:`cp_model.IntVar` (in integer grid steps, not µm).
    """
    model = cp_model.CpModel()

    w_step = tech.width.step
    l_step = tech.length.step
    w_min_int = round(tech.width.min / w_step)
    w_max_int = round(tech.width.max / w_step)
    l_min_int = round(tech.length.min / l_step)
    l_max_int = round(tech.length.max / l_step)

    # --- Decision variables ---
    W: dict[str, cp_model.IntVar] = {}
    L: dict[str, cp_model.IntVar] = {}
    for ref in transistors:
        W[ref] = model.new_int_var(w_min_int, w_max_int, f"W_{ref}")
        L[ref] = model.new_int_var(l_min_int, l_max_int, f"L_{ref}")

    # --- gm lower-bound constraints: 2·µCox·IDS·W ≥ gm_req²·L ---
    for ref, (device, _slot) in transistors.items():
        gm_req = gm_req_map.get(ref, 0.0)
        if gm_req <= 0.0:
            continue
        ids_a = ids_map.get(ref, 0.0)
        if ids_a == 0.0:
            continue
        params: MosfetParams = tech.nmos if device.type == "nmos" else tech.pmos
        lhs = _coeff(2.0 * params.mu_cox * abs(ids_a))   # coefficient of W
        rhs = _coeff(gm_req ** 2)                         # coefficient of L
        if lhs > 0 and rhs > 0:
            model.add(lhs * W[ref] >= rhs * L[ref])

    # --- VDS_sat upper-bound constraints: 2·IDS·L ≤ µCox·VDS_sat_max²·W ---
    for ref, (device, _slot) in transistors.items():
        vod_max = vod_max_map.get(ref, float("inf"))
        if vod_max == float("inf") or vod_max <= 0.0:
            continue
        ids_a = ids_map.get(ref, 0.0)
        if ids_a == 0.0:
            continue
        params = tech.nmos if device.type == "nmos" else tech.pmos
        lhs = _coeff(2.0 * abs(ids_a))                   # coefficient of L
        rhs = _coeff(params.mu_cox * vod_max ** 2)        # coefficient of W
        if lhs > 0 and rhs > 0:
            model.add(lhs * L[ref] <= rhs * W[ref])

    # --- Symmetry constraints: matched pairs within designated slots ---
    for slot_name, devices in slot_transistors.items():
        if slot_name not in symmetry_slots:
            continue
        mosfets = [d for d in devices if d.type in ("nmos", "pmos")]
        # Group by device type; within each group all transistors are matched.
        for dtype in ("nmos", "pmos"):
            group = [d for d in mosfets if d.type == dtype and d.ref in W]
            for i in range(1, len(group)):
                model.add(W[group[i].ref] == W[group[0].ref])
                model.add(L[group[i].ref] == L[group[0].ref])

    # --- Cross-slot symmetry: second_stage_p ↔ second_stage_n (FD only) ---
    ss_p = slot_transistors.get("second_stage_p", [])
    ss_n = slot_transistors.get("second_stage_n", [])
    if ss_p and ss_n:
        for dtype in ("nmos", "pmos"):
            p_group = [d for d in ss_p if d.type == dtype and d.ref in W]
            n_group = [d for d in ss_n if d.type == dtype and d.ref in W]
            if p_group and n_group:
                anchor = p_group[0].ref
                for d in p_group[1:]:
                    model.add(W[d.ref] == W[anchor])
                    model.add(L[d.ref] == L[anchor])
                for d in n_group:
                    model.add(W[d.ref] == W[anchor])
                    model.add(L[d.ref] == L[anchor])

    # --- Objective: minimise total gate width (proxy for power and area) ---
    model.minimize(sum(W.values()))

    # --- Branching heuristic: bias_gen transistors first, then others ---
    bias_refs = [
        ref for ref, (_d, slot) in transistors.items() if slot == "bias_gen"
    ]
    other_refs = [ref for ref in transistors if ref not in bias_refs]

    priority_vars = [v for ref in bias_refs for v in (W[ref], L[ref])]
    rest_vars = [v for ref in other_refs for v in (W[ref], L[ref])]

    if priority_vars:
        model.add_decision_strategy(
            priority_vars,
            cp_model.CHOOSE_FIRST,
            cp_model.SELECT_MIN_VALUE,
        )
    if rest_vars:
        model.add_decision_strategy(
            rest_vars,
            cp_model.CHOOSE_FIRST,
            cp_model.SELECT_MIN_VALUE,
        )

    return model, W, L
