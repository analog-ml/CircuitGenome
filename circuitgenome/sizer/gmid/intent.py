"""Design intent for the gm/Id sizer, as an explicit three-level hierarchy.

In the gm/Id methodology the transconductance efficiency ``gm/Id`` of a device is
a **design choice** that selects its inversion region (strong ~5–8 /V, moderate
~10–16 /V, weak ~18–25 /V).  This module makes the *intent* behind those choices
explicit and layered, so the sizing flow reads top-down:

    Circuit intent (spec)            ── SizingSpec: gain, GBW, PM, swing, power …
            │                           (the *what*; lives in shared.models)
            ▼
    Functional-block intent          ── BlockIntent: per building block, the role,
            │                           gm/Id region, L multiple and the *rationale*.
            ▼
    Transistor intent                ── TransistorIntent: the block intent resolved
                                        onto each device (role, gm/Id, L, block, why).

The middle layer is the tunable one and the extension point: each functional
building block carries its own gm/Id region and a human-readable reason, and a
caller (or a future optimizer) can override a single block's region while the
rest fall back to the defaults below.

A functional building block is *not* the same as an FBR slot: one slot can hold
devices of different roles (e.g. a gain stage's signal driver **and** its
current-source load both live in the ``second_stage`` slot).  So a block is keyed
by ``(slot, role)`` via :func:`functional_block`, matching the design-intent
taxonomy (input stage, gain stage, active load, tail source, …).

Design-intent table (defaults):

======================  ===============  ================================================
Building block          gm/Id (1/V)      Design intent
======================  ===============  ================================================
Input stage             solved           Diff voltage → current: high gm, low noise, matching
Gain stage              solved           Increase voltage gain while keeping stability
Output stage            solved           Drive load capacitance and provide slew current
Active / stage load     10 (strong-ish)  Mirror current accurately with high output resistance
Tail current source     10 (strong-ish)  Set bias current with saturation headroom and high rout
Bias generator          10 (strong-ish)  Generate stable, low-sensitivity bias currents
CMFB                     10 (strong-ish)  Regulate the output common-mode voltage
Cascode                 8  (strong)       Increase output resistance with a small Vdsat
======================  ===============  ================================================

"solved" = gm/Id is not a free knob for signal devices; it is set to
``gm_required / Id`` to meet the spec (see :meth:`GmIdModel.geometry_for`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..shared.device_model import CASCODE, CURRENT_SOURCE, SIGNAL

# --- gm/Id inversion regions used as the block defaults (1/V) ----------------
_MODERATE = 14.0        # signal nominal (pre-geometry estimate); real gm/Id is solved
_STRONG = 10.0          # current source: headroom + output resistance
_STRONG_CASCODE = 8.0   # cascode: small Vdsat to preserve stacked headroom

# --- channel length as a multiple of L_min -----------------------------------
_L_SIGNAL = 2.0         # signal: balance gain and ft
_L_CS = 4.0             # current source: longer L → higher output resistance
_L_CASCODE = 3.0        # cascode


@dataclass(frozen=True)
class BlockIntent:
    """Design intent for one functional building block (Level 2).

    :param role: the sizing role — ``SIGNAL`` (gm solved from the spec),
        ``CURRENT_SOURCE`` or ``CASCODE``.
    :param gm_id: target gm/Id in 1/V, or ``None`` when it is *solved* from the
        gm requirement (all signal blocks).  This is the one free knob a caller
        or optimizer overrides per block.
    :param l_mult: channel length as a multiple of ``length.min``.
    :param rationale: why this region/length — surfaced in the sizing result so
        the choice is explainable, not implicit.
    """
    role: str
    gm_id: float | None
    l_mult: float
    rationale: str


@dataclass(frozen=True)
class TransistorIntent:
    """A block intent resolved onto a single device (Level 3).

    The per-device design intent that drives :meth:`GmIdModel.geometry_for`:
    the resolved ``role``/``gm_id``/``l_mult`` plus the functional ``block`` it
    came from and the ``rationale`` for reporting.
    """
    ref: str
    block: str
    role: str
    gm_id: float | None
    l_mult: float
    rationale: str


# --- the default per-building-block intent registry --------------------------
DEFAULT_BLOCK_INTENTS: dict[str, BlockIntent] = {
    "input_stage": BlockIntent(
        SIGNAL, None, _L_SIGNAL,
        "Convert the differential input voltage to current with high gm, low "
        "noise and good matching. gm/Id is solved to meet GBW/gain (moderate "
        "inversion); a small L multiple balances gain and ft."),
    "gain_stage": BlockIntent(
        SIGNAL, None, _L_SIGNAL,
        "Increase voltage gain while maintaining stability. gm/Id is solved "
        "from the required gm; L favours a gain/ft balance."),
    "output_stage": BlockIntent(
        SIGNAL, None, _L_SIGNAL,
        "Drive the load capacitance and provide slew current. gm/Id is solved "
        "from the required gm."),
    "active_load": BlockIntent(
        CURRENT_SOURCE, _STRONG, _L_CS,
        "First-stage active current-mirror load: replicate current accurately "
        "with high output resistance. Strong-ish inversion (low gm/Id) for "
        "headroom and rout; a long L raises rout."),
    "stage_load": BlockIntent(
        CURRENT_SOURCE, _STRONG, _L_CS,
        "Current-source load of a gain/output stage: high output resistance "
        "for gain. Strong-ish inversion, long L."),
    "tail_current": BlockIntent(
        CURRENT_SOURCE, _STRONG, _L_CS,
        "Tail current source: set the input-pair bias current with adequate "
        "saturation headroom and high rout. A low gm/Id (strong inversion) "
        "maximises the headroom."),
    "bias_generator": BlockIntent(
        CURRENT_SOURCE, _STRONG, _L_CS,
        "Generate stable, low-sensitivity reference currents for the mirrors it "
        "drives. Strong inversion, long L to match those mirrors."),
    "cmfb": BlockIntent(
        CURRENT_SOURCE, _STRONG, _L_CS,
        "Common-mode feedback devices: regulate the output common-mode voltage. "
        "(Resistive-sense averaging is set separately by cmfb_sense_r.)"),
    "cascode": BlockIntent(
        CASCODE, _STRONG_CASCODE, _L_CASCODE,
        "Increase output resistance while preserving the stacked headroom. "
        "Strong inversion for a small Vdsat; a moderate L."),
    "current_source": BlockIntent(
        CURRENT_SOURCE, _STRONG, _L_CS,
        "Generic current source: accurate current with high output resistance. "
        "Strong-ish inversion, long L."),
}

# (slot, role) → functional-block name.  Signal devices split by stage; every
# other non-cascode device is a current source of some kind.
_SIGNAL_BLOCK = {
    "input_pair": "input_stage",
    "second_stage": "gain_stage", "second_stage_p": "gain_stage",
    "second_stage_n": "gain_stage",
    "third_stage": "output_stage", "third_stage_p": "output_stage",
    "third_stage_n": "output_stage",
}
_CS_BLOCK = {
    "load": "active_load",
    "tail_current": "tail_current",
    "bias_gen": "bias_generator",
    "cmfb": "cmfb",
    "second_stage": "stage_load", "second_stage_p": "stage_load",
    "second_stage_n": "stage_load",
    "third_stage": "stage_load", "third_stage_p": "stage_load",
    "third_stage_n": "stage_load",
}


def functional_block(slot: str, is_signal: bool, is_cascode: bool) -> str:
    """Functional-building-block name for a device in ``slot`` with its role.

    Signal takes precedence (a cascode is never a signal device), matching the
    role assignment the sizer used before this registry existed.
    """
    if is_signal:
        return _SIGNAL_BLOCK.get(slot, "gain_stage")
    if is_cascode:
        return "cascode"
    return _CS_BLOCK.get(slot, "current_source")


def resolve_transistor_intents(
    all_transistors: dict[str, tuple],   # ref → (Device, slot_name)
    cascode_refs: set[str],
    block_intents: dict[str, BlockIntent] = DEFAULT_BLOCK_INTENTS,
) -> dict[str, TransistorIntent]:
    """Resolve the block registry onto every device (Level 2 → Level 3)."""
    from .blocks import _is_signal_dev
    out: dict[str, TransistorIntent] = {}
    for ref, (device, slot) in all_transistors.items():
        block = functional_block(slot, _is_signal_dev(device), ref in cascode_refs)
        bi = block_intents[block]
        out[ref] = TransistorIntent(ref=ref, block=block, role=bi.role,
                                    gm_id=bi.gm_id, l_mult=bi.l_mult,
                                    rationale=bi.rationale)
    return out


@dataclass(frozen=True)
class GmIdIntent:
    """Circuit-wide gm/Id knobs that are *not* per building block.

    The per-block gm/Id and L choices live in ``block_intents`` (defaulting to
    :data:`DEFAULT_BLOCK_INTENTS`); the role-level fields here remain the fallback
    the device model uses for its pre-geometry ``gds`` estimate (via
    ``GmIdPolicy``), and the two resistor knobs configure the resistor network the
    block registry does not cover.  To retune a single building block, pass a copy
    of ``block_intents`` with that entry replaced.

    :param block_intents: per-functional-block design intent (Level 2).
    :param signal_gm_id: nominal signal gm/Id for the pre-geometry gds estimate.
    :param current_source_gm_id: fallback current-source gm/Id.
    :param cascode_gm_id: fallback cascode gm/Id.
    :param signal_l_mult / current_source_l_mult / cascode_l_mult: fallback L
        multiples per role.
    :param degeneration_factor: ``gm1·R`` for a source-degenerated input pair —
        sets each degeneration resistor to ``factor/gm1`` and the effective input
        transconductance to ``gm1/(1+factor)`` (0 = no degeneration).
    :param cmfb_sense_r: resistance (Ω) of each resistive-sense CMFB averager
        resistor — large so it doesn't load the differential output.
    """
    block_intents: dict[str, BlockIntent] = field(
        default_factory=lambda: dict(DEFAULT_BLOCK_INTENTS))
    signal_gm_id: float = _MODERATE
    current_source_gm_id: float = _STRONG
    cascode_gm_id: float = _STRONG_CASCODE
    signal_l_mult: float = _L_SIGNAL
    current_source_l_mult: float = _L_CS
    cascode_l_mult: float = _L_CASCODE
    degeneration_factor: float = 0.5  # gm1·R for a degenerated input pair (0 = none)
    cmfb_sense_r: float = 1.0e6       # CMFB resistive-sense averager R (Ω, large)


DEFAULT_INTENT = GmIdIntent()
