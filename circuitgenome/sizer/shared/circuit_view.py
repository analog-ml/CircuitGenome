"""The structural view of a recognised circuit, derived once.

Everything a sizer needs to know about a circuit's *structure* — the per-slot
device lists, the deduplicated ``ref -> (device, slot)`` map, and the
topology-mismatch advisories — comes from :func:`analyze_circuit` and is
read-only from there on.  The extraction steps behind it are private: a caller
that wants the structure asks once and gets all of it consistently, rather than
assembling it from four calls in the right order.

The gm/Id pipeline extends this with its own typed block decomposition; see
:class:`~circuitgenome.sizer.gmid.analyze.GmIdCircuitView`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from circuitgenome.recognizer.models import FunctionalBlockRecognitionResult
from circuitgenome.synthesizer.models import Device, TopologyTemplate

from .taxonomy import STAGE_SLOTS, is_signal_device


@dataclass
class CircuitView:
    """Structural facts about a recognised circuit.

    :param slot_transistors: FBR slot name -> MOSFET devices in that slot.
    :param slot_resistors: FBR slot name -> resistor devices in that slot.
    :param all_transistors: deduplicated ref -> (Device, owning slot); a device
        appearing in several slots is attributed to the highest-priority one.
    :param warnings: topology-mismatch advisories from the structural check.
    """
    slot_transistors: dict[str, list[Device]] = field(default_factory=dict)
    slot_resistors: dict[str, list[Device]] = field(default_factory=dict)
    all_transistors: dict[str, tuple[Device, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _adopt_orphan_mosfets(
    slot_transistors: dict[str, list[Device]],
    fbr_result: FunctionalBlockRecognitionResult,
    topology: TopologyTemplate,
) -> None:
    """Attribute slot-suffixed MOSFETs the FBR left unassigned to their slot.

    Pattern matching can leave a device out of every slot when its structure
    is ambiguous (two identical parallel diodes on one net) or incomplete (a
    bias leg whose expected partner lives in another slot, e.g. a cascode
    tail's reference diode).  Such a device would silently go **unsized** and
    run at the simulator's default W/L.  Synthesized netlists name every
    device ``{ref}_{slot_name}``, so orphans are attributed -- and therefore
    sized -- by their ref suffix; devices whose ref matches no slot (external
    netlists) are left alone.
    """
    assigned = {d.ref for devs in slot_transistors.values() for d in devs}
    slot_names = sorted((s.name for s in topology.slots), key=len, reverse=True)
    candidates = [d for s in fbr_result.unassigned_structures for d in s.devices]
    candidates += list(fbr_result.unrecognized_devices)
    for dev in candidates:
        if dev.type not in ("nmos", "pmos") or dev.ref in assigned:
            continue
        slot = next((s for s in slot_names if dev.ref.endswith("_" + s)), None)
        if slot is not None:
            assigned.add(dev.ref)
            slot_transistors.setdefault(slot, []).append(dev)

def _extract_slot_transistors(
    fbr_result: FunctionalBlockRecognitionResult,
) -> dict[str, list[Device]]:
    """Return {slot_name: [mosfet_Device, ...]} from the FBR assignments."""
    result: dict[str, list[Device]] = {}
    for slot_name, sa in fbr_result.slot_assignments.items():
        mosfets = [d for d in sa.structure.devices if d.type in ("nmos", "pmos")]
        if mosfets:
            result[slot_name] = mosfets
    return result


def _extract_slot_resistors(
    fbr_result: FunctionalBlockRecognitionResult,
) -> dict[str, list[Device]]:
    """Return {slot_name: [resistor_Device, ...]} from the FBR assignments."""
    result: dict[str, list[Device]] = {}
    for slot_name, sa in fbr_result.slot_assignments.items():
        rs = [d for d in sa.structure.devices if d.type == "resistor"]
        if rs:
            result[slot_name] = rs
    return result


# Overdrive (V) used when sizing a load resistor so its DC drop biases the
# driven device into conduction: V_node ≈ Vth + this.


def _check_topology_match(
    slot_transistors: dict[str, list[Device]], topology_name: str
) -> list[str]:
    """Warn when the netlist does not realise the chosen topology.

    Every gain-stage slot of a valid circuit holds exactly one signal
    transistor. A stage slot with **no** signal device (e.g. bias-generator
    leftovers shoehorned into ``second_stage_p`` when a single-ended netlist is
    sized against a fully-differential topology) signals a ``--topology``
    mismatch — which would otherwise silently drop the gain/PM/PSRR metrics.
    """
    warnings: list[str] = []
    for slot in sorted(STAGE_SLOTS):
        devs = slot_transistors.get(slot)
        if devs and not any(is_signal_device(d) for d in devs):
            warnings.append(
                f"stage slot '{slot}' has no signal transistor — the netlist may "
                f"not match topology '{topology_name}' (check --topology, e.g. "
                f"single-ended vs fully-differential)."
            )
    return warnings


def _deduplicate_devices(
    slot_transistors: dict[str, list[Device]],
) -> dict[str, tuple[Device, str]]:
    """Return {ref: (Device, slot_name)} with each ref appearing once.

    When a transistor appears in multiple slots (e.g. the tail mirror
    reference appears in both ``tail_current`` and ``bias_gen``), the
    *first* slot encountered wins for the purpose of iDS assignment.
    Priority order: input_pair > load > tail_current > second_stage > bias_gen.
    """
    priority = ["input_pair", "load", "tail_current",
                "second_stage", "second_stage_p", "second_stage_n",
                "third_stage", "third_stage_p", "third_stage_n",
                "output_stage", "output_stage_p", "output_stage_n", "bias_gen"]
    ordered = sorted(
        slot_transistors.keys(),
        key=lambda s: priority.index(s) if s in priority else len(priority),
    )
    seen: dict[str, tuple[Device, str]] = {}
    for slot in ordered:
        for d in slot_transistors[slot]:
            if d.ref not in seen:
                seen[d.ref] = (d, slot)
    return seen


def analyze_circuit(
    fbr_result: FunctionalBlockRecognitionResult, topology: TopologyTemplate
) -> CircuitView:
    """Build the :class:`CircuitView` from the FBR assignments.

    Orphan adoption runs before deduplication, so a device the FBR could not
    place still reaches the sizer that will size it.
    """
    slot_transistors = _extract_slot_transistors(fbr_result)
    _adopt_orphan_mosfets(slot_transistors, fbr_result, topology)
    return CircuitView(
        slot_transistors=slot_transistors,
        slot_resistors=_extract_slot_resistors(fbr_result),
        all_transistors=_deduplicate_devices(slot_transistors),
        warnings=_check_topology_match(slot_transistors, topology.name),
    )
