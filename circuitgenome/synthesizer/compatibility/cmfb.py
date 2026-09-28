"""
CMFB-slot compatibility filter and pruning for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

Every ``fully_differential`` topology has a ``cmfb`` slot, wired
``cmfb.out -> net_cmfb_out -> load.bias_cmfb``. But only 4 of the 12 ``load``
variants -- ``folded_cascode_load_{nmos,pmos}_input_differential_output``
(gating ``mn3``/``mn4`` or ``mp1``/``mp2``) and
``current_source_load_{pmos,nmos}`` (gating both branch devices, issue #112),
the four tagged ``output_cardinality: "differential"`` -- declare
``bias_cmfb`` as ``role: input`` and actually reference it from a device
terminal. The other 8 ``load`` variants declare ``bias_cmfb`` as
``role: optional`` and never reference it, so ``net_cmfb_out`` drives nothing
for those combinations.

Without a filter, every ``cmfb`` variant would be enumerated for every
combination, but for the 8 non-``"differential"`` loads the choice between
them makes zero difference to the assembled circuit -- pure combinatorial
duplication, plus 7-9 dead ``cmfb_*`` devices and an unnecessarily "needed"
bias rail 4 (``cmfb.bias``).

Each CMFB amp exists in a ``*_pmos_mirror`` and a ``*_nmos_mirror`` form: its
output is a diode-connected device that current-mirrors into the load's
CMFB-gated devices (the low-gain CMFB, issue #208), so the diode must match
their type -- a PMOS diode cannot mirror into an NMOS sink.

:func:`is_cmfb_compatible` enforces both rules: for a ``load`` whose
``output_cardinality`` isn't ``"differential"``, only the canonical
:data:`CANONICAL_CMFB_VARIANT` is allowed through; for a consuming load, only
the CMFB forms whose output diode matches the gated devices' type. :func:`prune_cmfb` then
empties that variant's ports/devices for those combinations, so it
contributes no devices and ``cmfb.bias`` is no longer "needed" (see
:func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`).

To extend: tag a new or edited ``load`` variant with
``output_cardinality: "differential"`` (and give it a real
``bias_cmfb: role: input`` consumer) to make it a genuine ``cmfb`` consumer --
no code changes needed here.
"""
from __future__ import annotations
import dataclasses

from ..models import ModuleVariant
from .compensation import stage_inversions

CANONICAL_CMFB_VARIANT = "resistive_sense_cmfb_pmos_mirror"
_CMFB_CONSUMING_CARDINALITY = "differential"


def _gated_device_types(load: ModuleVariant) -> set[str]:
    """Types of the ``load`` devices whose gate is ``bias_cmfb``."""
    return {d.type for d in load.devices if d.terminals.get("g") == "bias_cmfb"}


def _output_diode_type(cmfb: ModuleVariant) -> str | None:
    """Type of the diode-connected device on the CMFB's ``out`` port."""
    return next((d.type for d in cmfb.devices
                 if d.terminals.get("d") == d.terminals.get("g") == "out"), None)


def is_cmfb_compatible(variant_map: dict[str, ModuleVariant]) -> bool:
    """Return ``False`` if the ``cmfb`` variant is a duplicate or cannot drive the load.

    Topologies without a ``cmfb`` slot are unaffected. For a ``load`` whose
    ``output_cardinality`` is ``"differential"``, a ``cmfb`` variant passes
    only when its output diode has the same type as the load devices it
    gates -- the diode is their mirror reference. For every other ``load``,
    ``cmfb.out`` drives nothing, so only :data:`CANONICAL_CMFB_VARIANT` is
    allowed through -- the others would be enumerated as duplicate no-op
    circuits.
    """
    if "cmfb" not in variant_map:
        return True
    load, cmfb = variant_map["load"], variant_map["cmfb"]
    if load.output_cardinality == _CMFB_CONSUMING_CARDINALITY:
        return _gated_device_types(load) == {_output_diode_type(cmfb)}
    return cmfb.name == CANONICAL_CMFB_VARIANT


def has_cm_control(variant_map: dict[str, ModuleVariant]) -> bool:
    """Return ``False`` for a fully-differential combination with no output-CM control.

    Topologies without a ``cmfb`` slot are unaffected. When ``load`` doesn't
    consume ``cmfb.out`` (see :func:`prune_cmfb`), nothing senses or sets the
    output common mode: open-loop the outputs rail or split apart (issue
    #208 -- 216/216 of the two-stage FD ``cmfb_absent`` circuits fail the
    SPICE ``.op`` bias gate on gf180), so
    :func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits` treats
    these combinations as bias-infeasible.
    """
    if "cmfb" not in variant_map:
        return True
    return variant_map["load"].output_cardinality == _CMFB_CONSUMING_CARDINALITY


def prune_cmfb(variant: ModuleVariant, load: ModuleVariant) -> ModuleVariant:
    """Return an empty placeholder if *load* doesn't consume ``cmfb.out``.

    If *load*'s ``output_cardinality`` is ``"differential"``, *variant* is
    returned unchanged. Otherwise, returns a copy of *variant* with no ports
    and no devices -- it contributes nothing to the assembled circuit, and
    ``cmfb.bias`` is no longer "needed" by
    :func:`~circuitgenome.synthesizer.bias_construction.required_rail_kinds`.
    """
    if load.output_cardinality == _CMFB_CONSUMING_CARDINALITY:
        return variant
    return dataclasses.replace(variant, name="cmfb_absent", ports=[], devices=[])


# Per-variant gate rewiring that flips the CMFB amplifier's comparison
# polarity: the sensed CM moves from the m1/m3 gates (the d1/da branch) to the
# m2/m4 gates and vref the other way, so a rising sensed CM *lowers* cmfb.out
# -- in both mirror forms (stock: sense up -> out up).
_RS_SWAP = {"m1": "vref", "m2": "sense"}
_DDA_SWAP = {"m1": "vref", "m2": "in1", "m3": "vref", "m4": "in2"}
_INVERTING_GATE_SWAP: dict[str, dict[str, str]] = {
    "resistive_sense_cmfb_pmos_mirror": _RS_SWAP,
    "resistive_sense_cmfb_nmos_mirror": _RS_SWAP,
    "dda_cmfb_pmos_mirror": _DDA_SWAP,
    "dda_cmfb_nmos_mirror": _DDA_SWAP,
}


def _cm_loop_inversions(topology, variant_map: dict[str, ModuleVariant]) -> int | None:
    """Return the total common-source inversion count along the stage chain
    from the first-stage (``load``) outputs to the CMFB's sensed nets, or
    ``None`` if the chain cannot be classified.

    Walks ``amplification_stage``/``output_stage`` slots by their ``in``/
    ``out`` nets (same composition as the compensation parity filter) from
    ``load.out1``/``out2`` until a sensed net (``cmfb.in1``/``in2``) is
    reached, summing each traversed variant's
    :func:`~.compensation.stage_inversions`.
    """
    cmfb_conns = topology.slot_connections("cmfb")
    sense_nets = {cmfb_conns.get("in1"), cmfb_conns.get("in2")} - {None}
    load_conns = topology.slot_connections("load")
    stage_by_in_net: dict[str, tuple[str, str]] = {}
    for slot in topology.slots:
        if slot.category not in ("amplification_stage", "output_stage"):
            continue
        conns = topology.slot_connections(slot.name)
        if "in" in conns and "out" in conns:
            stage_by_in_net[conns["in"]] = (slot.name, conns["out"])

    for start in (load_conns.get("out1"), load_conns.get("out2")):
        inversions, net = 0, start
        visited: set[str] = set()
        while net is not None and net not in sense_nets:
            if net in visited or net not in stage_by_in_net:
                net = None
                break
            visited.add(net)
            stage_name, stage_out = stage_by_in_net[net]
            stage_variant = variant_map.get(stage_name)
            stage_inv = (stage_inversions(stage_variant)
                         if stage_variant is not None else None)
            if stage_inv is None:
                net = None
                break
            inversions += stage_inv
            net = stage_out
        if net is not None:
            return inversions
    return None


def orient_cmfb(variant: ModuleVariant, topology,
                variant_map: dict[str, ModuleVariant]) -> ModuleVariant:
    """Return the polarity-correct CMFB orientation for *topology* (issue #165).

    The CMFB senses the external outputs (``outp``/``outn``) and drives the
    first-stage load gates, so the CM loop traverses every stage after the
    first.  All four CMFB-consuming loads respond the same way (``cmfb.out``
    up → first-stage output CM down), so the loop sign depends on the *net
    inversion parity* of the chosen stage chain (:func:`_cm_loop_inversions`
    over *variant_map* — NOT the stage count: both three-stage chains pair
    one ``noninverting_stage`` with one common source — NMC puts it at gm2,
    RNMC at gm3 — the same odd parity as a two-stage despite their three
    stages):

    - **odd** parity (net-inverting chain — every shipped FD template): the
      loop is positive with the stock amp orientation — swap the sense/vref
      gates (``<name>_inverting``) so a rising output CM lowers ``cmfb.out``;
    - **even** parity (a net non-inverting chain, e.g. CS+CS): the stock
      orientation is already negative — returned unchanged.

    An unclassifiable chain is assumed net-inverting, like every shipped
    chain.  ``cmfb_absent`` placeholders pass through untouched.
    """
    if not variant.devices:
        return variant
    swap = _INVERTING_GATE_SWAP.get(variant.name)
    if swap is None:
        return variant
    inversions = _cm_loop_inversions(topology, variant_map)
    if inversions is None:
        inversions = 1
    if inversions % 2 == 0:
        return variant
    devices = [
        dataclasses.replace(d, terminals={**d.terminals, "g": swap[d.ref]})
        if d.ref in swap else d
        for d in variant.devices
    ]
    return dataclasses.replace(variant, name=f"{variant.name}_inverting",
                               devices=devices)
