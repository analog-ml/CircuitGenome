"""
Untapped-load-branch compatibility filter for
:func:`~circuitgenome.synthesizer.synthesizer.enumerate_circuits`.

In every ``single_ended`` topology only one of the first stage's two branch
nodes is tapped: ``load.out``/``out2`` land on the stage-output net (inside
the feedback loop, or at least observed by the next stage), while
``load.in1``/``out1`` (``net_diff1``) is *untapped* -- nothing outside the
first stage senses or drives it. That node's DC voltage must therefore be
defined by the load itself. A load branch that is a plain, rail-referenced
current source (issue #112: ``current_source_load_*`` -- gate on a bias rail,
no diode connection) leaves the untapped node **high-impedance between two
series current sources**: the load device on one side, the input-pair half
plus tail on the other. Any mismatch between the two currents -- and there is
always some -- collapses the node until one device leaves saturation; no
sizing can fix it, because there is no mechanism (diode, resistor, CMFB, or
feedback) to absorb the difference. This is the textbook reason plain
current-source loads are only used with common-mode control.

The check is *structural* (actual device terminal references, no YAML tags,
same approach as :mod:`~circuitgenome.synthesizer.second_stage_compatibility`):
the untapped branch node (port ``in1``) counts as DC-defined when the load
contains

- a diode-connected MOSFET on it (``g == d == in1`` -- the mirror loads'
  reference side, ``active_load_*``),
- a resistor touching it (``resistor_load_*``), or
- a MOSFET *source* terminal on it (the cascode loads' folding/cascode
  devices: the device's gate rail pins the node one ``V_GS`` away).

Loads whose devices never put a MOSFET drain on ``in1`` are not constrained
(no current is forced into the node, so nothing needs to absorb a mismatch).
``fully_differential`` topologies tap both branches (``net_loadout1``/
``net_loadout2``) and are out of scope here -- there the common-mode
definition is the CMFB loop's job (see
:mod:`~circuitgenome.synthesizer.cmfb_compatibility`).

To extend: nothing to tag -- a new ``load`` variant is classified by what its
devices connect to ``in1``, and a new topology is covered by its
``output_type`` (``single_ended`` templates leave ``in1``'s net untapped by
the wiring convention documented in ``config/opamp_topologies.yaml``).
"""
from __future__ import annotations
from .models import ModuleVariant, TopologyTemplate

#: The load port wired to the untapped branch node in ``single_ended``
#: topologies (``net_diff1``; ``out1`` aliases it on the non-cascode loads).
_UNTAPPED_BRANCH_PORT = "in1"

_MOS_TYPES = ("nmos", "pmos")


def untapped_branch_is_dc_defined(load: ModuleVariant) -> bool:
    """Return ``True`` if *load* defines the DC voltage of its untapped
    branch node (port ``in1``), or forces no current into it at all -- see
    the module docstring for the electrical rationale.
    """
    drain_on_node = False
    for dev in load.devices:
        terms = dev.terminals
        if dev.type == "resistor":
            if _UNTAPPED_BRANCH_PORT in (terms.get("t1"), terms.get("t2")):
                return True
        elif dev.type in _MOS_TYPES:
            if terms.get("s") == _UNTAPPED_BRANCH_PORT:
                return True  # cascode/folding device rides the node
            if terms.get("d") == _UNTAPPED_BRANCH_PORT:
                if terms.get("g") == _UNTAPPED_BRANCH_PORT:
                    return True  # diode-connected reference side
                drain_on_node = True
    return not drain_on_node


def is_load_branch_compatible(
    topology: TopologyTemplate,
    variant_map: dict[str, ModuleVariant],
) -> bool:
    """Return ``False`` if *topology* leaves the ``load``'s ``in1`` branch
    node untapped (``output_type: single_ended``) and the load doesn't
    define its DC voltage (see the module docstring for the electrical
    rationale).
    """
    if topology.config.get("output_type") != "single_ended":
        return True
    return untapped_branch_is_dc_defined(variant_map["load"])
