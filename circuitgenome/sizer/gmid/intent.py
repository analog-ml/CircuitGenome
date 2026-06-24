"""Design intent for the gm/Id sizer: the per-role inversion-region choices.

In the gm/Id methodology the transconductance efficiency ``gm/Id`` of each
device is a **design choice** that selects its inversion region:

* **strong** inversion (~5–8 /V) — high current density, small W, low intrinsic
  gain, large overdrive (good for headroom-critical current sources and for
  matching);
* **moderate** inversion (~10–16 /V) — the usual sweet spot for signal devices
  (balances gain, ``ft`` and noise);
* **weak** inversion (~18–25 /V) — maximum efficiency / lowest overdrive, low
  ``ft`` (good when headroom is tight or current is precious).

:class:`GmIdIntent` collects these choices per role and the channel-length
multiplier per role (longer L → higher intrinsic gain ``gm/gds`` at the cost of
``ft``).  It replaces the old ad-hoc ``GmIdPolicy`` constants and is the single
knob a designer (or a per-node config) tunes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GmIdIntent:
    """Per-role gm/Id (inversion region) and L choices.

    :param signal_gm_id: gm/Id for signal/transconductance devices (input pair,
        gain-stage driver) when their gm/Id is not already pinned by the spec
        (used as the nominal for pre-geometry gds estimates).
    :param current_source_gm_id: gm/Id for bias/load current sources — strong
        inversion by default for headroom and output resistance.
    :param cascode_gm_id: gm/Id for cascode devices (strong inversion: small
        Vdsat to preserve the stacked headroom).
    :param signal_l_mult: signal-device L as a multiple of ``length.min``
        (longer L trades ``ft`` for gain).
    :param current_source_l_mult: current-source L multiple (longer → higher
        output resistance).
    :param cascode_l_mult: cascode-device L multiple.
    :param degeneration_factor: ``gm1·R`` for a source-degenerated input pair —
        sets each degeneration resistor to ``factor/gm1`` and the effective input
        transconductance to ``gm1/(1+factor)`` (0 = no degeneration).
    :param cmfb_sense_r: resistance (Ω) of each resistive-sense CMFB averager
        resistor — large so it doesn't load the differential output.
    """
    signal_gm_id: float = 14.0
    current_source_gm_id: float = 10.0
    cascode_gm_id: float = 8.0
    signal_l_mult: float = 2.0
    current_source_l_mult: float = 4.0
    cascode_l_mult: float = 3.0
    degeneration_factor: float = 0.5  # gm1·R for a degenerated input pair (0 = none)
    cmfb_sense_r: float = 1.0e6       # CMFB resistive-sense averager R (Ω, large)


DEFAULT_INTENT = GmIdIntent()
