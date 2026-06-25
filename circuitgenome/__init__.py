# Re-export the Topology Synthesizer public API at the package root for
# convenience (e.g. ``from circuitgenome import enumerate_circuits``).  The
# recognizer and sizer layers are reached via their subpackages
# (``circuitgenome.recognizer`` / ``circuitgenome.sizer``) so importing the
# top-level package stays lightweight (no ortools/numpy pulled in eagerly).
from .synthesizer import (
    synthesize,
    enumerate_circuits,
    load_modules,
    load_topologies,
    to_flat_spice,
    to_hierarchical_spice,
    SynthesizedCircuit,
    ModuleVariant,
    TopologyTemplate,
)

__version__ = "0.1.0"
__all__ = [
    "synthesize",
    "enumerate_circuits",
    "load_modules",
    "load_topologies",
    "to_flat_spice",
    "to_hierarchical_spice",
    "SynthesizedCircuit",
    "ModuleVariant",
    "TopologyTemplate",
]
