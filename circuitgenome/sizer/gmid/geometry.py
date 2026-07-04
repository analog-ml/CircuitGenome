"""Procedural geometry assignment for the gm/Id sizing path.

Replaces CP-SAT for PTM nodes: geometry is *computed*, not searched.  With ``Id``
fixed by KCL and ``gm/Id = gm_req/Id`` fixed, the LUT gives ``Id/W → W``
directly, so this is a deterministic forward pass:

1. per-device ``(W, L)`` from :meth:`GmIdModel.geometry_for`;
2. snap ``W`` to the width grid (``L`` is already grid-aligned by the L-policy);
3. **symmetry** — matched pairs get the anchor's geometry (plain assignment);
4. **mirror ratios** — each output's ``W`` is the *exact* current ratio times the
   diode-connected reference's ``W`` at matched ``L`` (no ``Fraction`` rounding);
5. **load margin** — a single-ended plain current-source load balancing a
   mirrored tail gets ``_LOAD_CS_MARGIN`` extra width (see its docstring).

Symmetry is applied before mirror ratios; on the rare device that is both, the
mirror constraint wins (it is the bias-current-correctness fix).  Both rules are
self-consistent for matched pairs, so the order only matters for resolution, not
for the result.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..shared.device_model import CASCODE, SIGNAL, GmIdModel
from ..shared.models import TechParams, TransistorSizing
from ..shared.taxonomy import STAGE_SLOTS
from .blocks import LoadKind, classify_load

if TYPE_CHECKING:
    from .intent import TransistorIntent

# Same matched-pair slots CP-SAT treats as symmetric (constraints.build_model).
_SYMMETRY_SLOTS = frozenset({"input_pair", "load", "tail_current"})
# FD cross-slot matched pairs (second_stage_p ↔ _n, third_stage_p ↔ _n).
_FD_PAIRS = (("second_stage_p", "second_stage_n"),
             ("third_stage_p", "third_stage_n"))

#: Deliberate strength margin for a plain current-source load that balances a
#: mirrored tail current.  A single-ended first stage with a non-mirror
#: current-source load has no feedback fixing the load-vs-tail current balance
#: (no diode in the signal path, no CMFB): at an exact mirror ratio the fold
#: node drifts until the input pair triodes or the output rails.  Sizing the
#: load mirror slightly strong settles the node toward the load's supply rail,
#: keeping the input pair saturated with the load at the edge of saturation.
_LOAD_CS_MARGIN = 1.05

#: Fraction of the swing-derived Vdsat budget (``vod_max_map``) the plan
#: targets for output-path devices.  The swing bench reads the tracking
#: region out to small-signal slope ≥ 0.7, which cuts off noticeably before
#: the Vdsat point — a device sized exactly at the budget measures ~50 mV
#: short of the spec (issue #126).  Half the budget is the validated margin;
#: devices whose solved/policy Vdsat already fits are untouched.
_SWING_VDSAT_FRACTION = 0.5


def swing_gm_id_floor(
    model: GmIdModel, dtype: str, l_um: float, vod_max: float,
) -> tuple[float, bool]:
    """gm/Id floor so ``vds_sat ≤ _SWING_VDSAT_FRACTION · vod_max``.

    Returns ``(floor, fits_raw_budget)``.  ``floor`` is the smallest LUT
    gm/Id meeting the margin target (weaker inversion = lower Vdsat), or the
    table's weakest-inversion point as best effort when no point reaches the
    target.  ``fits_raw_budget`` is ``False`` when even the weakest inversion
    cannot fit the *raw* budget — the output stage cannot meet the swing
    spec at all.
    """
    axis = [float(g) for g in model.lut.gm_id_axis]
    target = vod_max * _SWING_VDSAT_FRACTION
    for g in axis:
        if model.lut.vdsat(dtype, g, l_um) <= target:
            return g, True
    weakest = axis[-1]
    return weakest, model.lut.vdsat(dtype, weakest, l_um) <= vod_max


def _output_stage_slots(slot_transistors: dict[str, list]) -> frozenset[str]:
    """Slots whose devices sit on the amplifier output node.

    ``compute_requirements`` puts swing bounds on second/third-stage devices
    *and* on the first-stage load; for a multi-stage circuit only the last
    stage touches the output, so the load's entry must not re-bias the first
    stage here.  One-stage circuits keep the load as the output path.
    """
    if any(s in slot_transistors for s in STAGE_SLOTS):
        return STAGE_SLOTS
    return frozenset({"load"})


def _mirror_tied_refs(all_transistors: dict[str, tuple]) -> set[str]:
    """Refs whose W is later overwritten by :func:`_apply_mirror_ratios`.

    A per-device gm/Id floor cannot stick on a mirror *output* — its geometry
    tracks the diode reference's inversion level — so such devices are only
    checked against the raw budget after the mirror pass.
    """
    groups: dict[tuple, list[str]] = {}
    diodes: dict[tuple, str] = {}
    for ref, (device, _slot) in all_transistors.items():
        gate = device.terminals.get("g")
        if not gate:
            continue
        key = (gate, device.type)
        groups.setdefault(key, []).append(ref)
        if device.terminals.get("d") == gate:
            diodes.setdefault(key, ref)
    tied: set[str] = set()
    for key, members in groups.items():
        if len(members) >= 2 and key in diodes:
            tied.update(m for m in members if m != diodes[key])
    return tied


def _apply_symmetry(
    W: dict[str, float], L: dict[str, float], slot_transistors: dict[str, list],
    ids_map: dict[str, float],
) -> None:
    """Matched pairs share the anchor device's geometry (plain assignment).

    Devices match only at the same planned IDS — a folded-cascode load's
    folding sinks (pair + cascode current) and its cascode devices (cascode
    current only) are distinct groups.
    """
    def equalize(refs: list[str]) -> None:
        anchor = refs[0]
        for r in refs[1:]:
            W[r], L[r] = W[anchor], L[anchor]

    for slot, devices in slot_transistors.items():
        if slot not in _SYMMETRY_SLOTS:
            continue
        groups: dict[tuple, list[str]] = {}
        for d in devices:
            if d.type in ("nmos", "pmos") and d.ref in W:
                groups.setdefault((d.type, ids_map.get(d.ref)), []).append(d.ref)
        for grp in groups.values():
            equalize(grp)

    for sp, sn in _FD_PAIRS:
        p_devs, n_devs = slot_transistors.get(sp, []), slot_transistors.get(sn, [])
        if not (p_devs and n_devs):
            continue
        for dtype in ("nmos", "pmos"):
            grp = [d.ref for d in (*p_devs, *n_devs) if d.type == dtype and d.ref in W]
            if grp:
                equalize(grp)


def _apply_mirror_ratios(
    W: dict[str, float],
    L: dict[str, float],
    all_transistors: dict[str, tuple],
    ids_map: dict[str, float],
    snap_w,
) -> None:
    """Current-mirror outputs track the reference W by the exact current ratio.

    MOSFETs are grouped by (gate-net, type); a diode-connected member (g == d)
    is the reference.  Output W is the current ratio times the reference's W at
    matched L (no ``Fraction`` rounding).
    """
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


def _apply_load_current_margin(
    W: dict[str, float], slot_transistors: dict[str, list], snap_w
) -> None:
    """Give a knife-edge current-source load ``_LOAD_CS_MARGIN`` extra width.

    Applies only to the single-ended, no-CMFB case: a ``load`` slot classified
    :attr:`~.blocks.LoadKind.CURRENT_SOURCE` balancing a MOSFET tail's fixed
    current.  This is an explicit design-intent margin — the old uncascoded
    pref branch's ~4% current surplus provided it by accident (issue #103).
    """
    fd = any(s in slot_transistors for s in
             ("second_stage_p", "second_stage_n", "third_stage_p", "third_stage_n"))
    load = [d for d in slot_transistors.get("load", [])
            if d.type in ("nmos", "pmos")]
    tail = [d for d in slot_transistors.get("tail_current", [])
            if d.type in ("nmos", "pmos")]
    if fd or not tail or classify_load(load, []) is not LoadKind.CURRENT_SOURCE:
        return
    for d in load:
        if d.ref in W:
            W[d.ref] = snap_w(W[d.ref] * _LOAD_CS_MARGIN)


def assign_geometry_gmid(
    model: GmIdModel,
    all_transistors: dict[str, tuple],      # ref → (Device, slot_name)
    slot_transistors: dict[str, list],      # slot_name → [Device, ...]
    ids_map: dict[str, float],              # ref → IDS in A
    intents: dict[str, "TransistorIntent"],  # ref → resolved per-device design intent
    gm_target_map: dict[str, float],        # ref → required gm in A/V (signal devices)
    tech: TechParams,
    vod_max_map: dict[str, float] | None = None,  # ref → max VDS_sat in V (swing)
) -> tuple[dict[str, TransistorSizing], list[str], bool]:
    """Return ``({ref: TransistorSizing}, warnings, feasible)`` for the gm/Id path.

    Geometry follows each device's :class:`~.intent.TransistorIntent`: its role,
    its (per-block) gm/Id region and channel length.  Signal devices ignore the
    intent's gm/Id and solve it from ``gm_target_map``.

    ``vod_max_map`` carries the output-swing Vdsat budgets: output-path devices
    get a gm/Id floor targeting ``_SWING_VDSAT_FRACTION`` of their budget.
    Excluded from the floor (but still checked against the raw budget):

    * **mirror outputs** — their inversion tracks the diode reference;
    * **signal devices under a cascode first-stage load** — their ``V_GS`` is
      the stage-interface pin (:mod:`.stage_interface`), and moving it for
      swing breaks the knife-edge saturation window (measured: 18 accepted
      folded-cascode + common_source candidates lost to a blanket floor).
      Those candidates' high loop gain keeps the swing bench tracking well
      past the Vdsat point, so the floor is not needed there.

    ``feasible`` is ``False`` when an output-path device cannot fit its raw
    budget even at the weakest inversion.
    """
    g = tech.width
    warnings: list[str] = []
    feasible = True
    vod_max_map = vod_max_map or {}
    out_slots = _output_stage_slots(slot_transistors)
    mirror_tied = _mirror_tied_refs(all_transistors)
    cascode_load = any(
        intents[d.ref].role == CASCODE
        for d in slot_transistors.get("load", [])
        if d.ref in intents)

    def snap_w(w_um: float) -> float:
        v = round(w_um / g.step) * g.step
        return float(min(max(v, g.min), g.max))

    # --- 1+2: per-device geometry from the LUT (block intent → gm/Id, L), W snapped ---
    W: dict[str, float] = {}
    L: dict[str, float] = {}
    for ref, (device, slot) in all_transistors.items():
        ti = intents[ref]
        gm_id_min = None
        vod = vod_max_map.get(ref)
        if (vod is not None and slot in out_slots and ref not in mirror_tied
                and not (cascode_load and ti.role == SIGNAL)):
            gm_id_min, fits = swing_gm_id_floor(
                model, device.type, model.length_for(ti.l_mult), vod)
            if not fits:
                feasible = False
                warnings.append(
                    f"{ref}: output stage cannot meet the swing spec — even "
                    f"weak-inversion VDS_sat exceeds the {vod:.2f} V budget "
                    f"(rail-to-spec headroom); relax the swing spec or raise "
                    f"the supply.")
        geo = model.geometry_for(
            device.type, ids_map[ref], ti.role, gm_target_map.get(ref),
            gm_id=ti.gm_id, l_um=model.length_for(ti.l_mult),
            gm_id_min=gm_id_min,
        )
        W[ref] = snap_w(geo.w_um)
        L[ref] = geo.l_um
        if geo.gm_id_capped:
            warnings.append(
                f"{ref}: required gm/Id exceeds the weak-inversion ceiling at its "
                f"bias current — increase the stage current or relax GBW/gain "
                f"(the design will fall short).")

    # --- 3: symmetry (matched pairs share the anchor's geometry) ---
    _apply_symmetry(W, L, slot_transistors, ids_map)

    # --- 4: current-mirror ratios (exact, no Fraction approximation) ---
    _apply_mirror_ratios(W, L, all_transistors, ids_map, snap_w)

    # --- 5: deliberate margin for a knife-edge current-source load ---
    _apply_load_current_margin(W, slot_transistors, snap_w)

    # --- 6: build TransistorSizing with final geometry ---
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

    # --- 7: raw swing budget on mirror-tied output devices (post-mirror W) ---
    for ref, (_device, slot) in all_transistors.items():
        vod = vod_max_map.get(ref)
        if (vod is not None and slot in out_slots and ref in mirror_tied
                and sizing[ref].vds_sat_v > vod):
            feasible = False
            warnings.append(
                f"{ref}: output stage cannot meet the swing spec — its mirror "
                f"group's VDS_sat {sizing[ref].vds_sat_v:.2f} V exceeds the "
                f"{vod:.2f} V budget (rail-to-spec headroom).")
    return sizing, warnings, feasible
