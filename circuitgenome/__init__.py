# Re-export each layer's public API at the package root for convenience, so
# users can write e.g. ``from circuitgenome import enumerate_circuits`` or
# ``from circuitgenome import size_circuit`` without reaching into subpackages.
# The same symbols remain available via their subpackages
# (``circuitgenome.synthesizer`` / ``circuitgenome.recognizer`` /
# ``circuitgenome.sizer``).

# Layer 1 - Topology Synthesizer
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

# Layer 2 - Recognizer
from .recognizer import (
    parse,
    recognize,
    assign_slots,
    group_by_category,
    ParsedNetlist,
    RecognizedStructure,
    SubcircuitRecognitionResult,
    PatternDef,
    PatternDevice,
    SlotAssignment,
    FunctionalBlockRecognitionResult,
    CategoryGroupResult,
)

# Layer 3 - Initial Sizer
from .sizer import (
    size_circuit,
    load_spec,
    load_tech,
    SizingResult,
    SizingSpec,
    TechParams,
    TransistorSizing,
    UnsupportedTechError,
)

# Layer 4 - Designer
from .designer import (
    design,
    DesignReport,
    DesignSolution,
    TemplateStats,
)


__version__ = "0.1.0"
__all__ = [
    # Synthesizer public API
    "synthesize",
    "enumerate_circuits",
    "load_modules",
    "load_topologies",
    "to_flat_spice",
    "to_hierarchical_spice",
    "SynthesizedCircuit",
    "ModuleVariant",
    "TopologyTemplate",

    # Recognizer public API
    "parse",
    "recognize",
    "assign_slots",
    "group_by_category",
    "ParsedNetlist",
    "RecognizedStructure",
    "SubcircuitRecognitionResult",
    "PatternDef",
    "PatternDevice",
    "SlotAssignment",
    "FunctionalBlockRecognitionResult",
    "CategoryGroupResult",

    # Sizer public API
    "size_circuit",
    "load_spec",
    "load_tech",
    "SizingResult",
    "SizingSpec",
    "TechParams",
    "TransistorSizing",
    "UnsupportedTechError",

    # Designer public API
    "design",
    "DesignReport",
    "DesignSolution",
    "TemplateStats",
]
