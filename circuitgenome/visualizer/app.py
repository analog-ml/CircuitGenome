"""
Streamlit UI for visualizing CircuitGenome topologies as block diagrams.

Run via ``circuitgenome visualize`` (requires the ``viz`` extra:
``pip install circuitgenome[viz]``), or directly with
``streamlit run circuitgenome/visualizer/app.py``.

Two tabs:

- **Topology Explorer** -- pick a topology and a module variant for each of
  its slots; renders the resulting block diagram (via
  :mod:`circuitgenome.visualizer.graph`) and, for valid combinations, the
  assembled SPICE netlist. Invalid combinations show why
  :func:`~circuitgenome.synthesizer.synthesizer.build_circuit` rejected them.
- **Module Browser** -- lists every module variant by category with its
  ports and device count.
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from circuitgenome.synthesizer.loader import load_modules, load_topologies
from circuitgenome.synthesizer.netlist import to_flat_spice
from circuitgenome.synthesizer.synthesizer import build_circuit
from circuitgenome.visualizer.graph import VizGraph, explain_incompatibility, topology_to_graph

CATEGORY_COLORS = {
    "input_pair": "#8ecae6",
    "load": "#ffb703",
    "tail_current": "#fb8500",
    "bias_generation": "#bde0fe",
    "cmfb": "#cdb4db",
    "compensation": "#a8dadc",
    "second_stage": "#90be6d",
}
PRUNED_COLOR = "#d3d3d3"


@st.cache_data
def _load_data():
    return load_modules(), load_topologies()


def _render_graph(graph: VizGraph) -> str:
    net = Network(height="550px", width="100%", directed=False)
    for node in graph.nodes:
        color = PRUNED_COLOR if node.is_pruned else CATEGORY_COLORS.get(node.category, "#cccccc")
        title = f"{node.category}: {node.variant_name}"
        if node.is_pruned:
            title += " (pruned)"
        net.add_node(node.id, label=f"{node.id}\n{node.label}", color=color, title=title)
    for edge in graph.edges:
        net.add_edge(
            edge.source,
            edge.target,
            label=edge.net,
            title=f"{edge.source}.{edge.source_port} ↔ {edge.net} ↔ {edge.target}.{edge.target_port}",
            font={"size": 7},
        )
    return net.generate_html()


def _topology_explorer(modules, topologies) -> None:
    topology = st.sidebar.selectbox("Topology", topologies, format_func=lambda t: t.name)

    variant_map = {}
    for slot in topology.slots:
        variant_map[slot.name] = st.sidebar.selectbox(
            f"{slot.name} ({slot.category})",
            modules[slot.category],
            format_func=lambda v: v.display_name,
            key=f"{topology.name}:{slot.name}",
        )

    circuit = build_circuit(topology, variant_map)
    if circuit is None:
        st.warning("This combination doesn't assemble into a circuit:")
        for reason in explain_incompatibility(topology, variant_map):
            st.write(f"- {reason}")
        graph = topology_to_graph(topology, variant_map)
    else:
        graph = topology_to_graph(topology, circuit.variant_map)

    components.html(_render_graph(graph), height=570)

    if circuit is not None:
        with st.expander("SPICE netlist"):
            st.code(to_flat_spice(circuit, name=circuit.name), language=None)


def _module_browser(modules) -> None:
    category = st.selectbox("Category", sorted(modules.keys()))
    for variant in modules[category]:
        with st.expander(f"{variant.display_name} ({variant.name})"):
            st.write(f"**Polarity:** {variant.polarity}")
            st.write(f"**Output cardinality:** {variant.output_cardinality}")
            st.write(f"**Ports:** {sorted(variant.port_names())}")
            st.write(f"**Devices:** {len(variant.devices)}")


def main() -> None:
    st.set_page_config(page_title="CircuitGenome Visualizer", layout="wide")
    st.title("CircuitGenome Topology Visualizer")

    modules, topologies = _load_data()
    tab_topo, tab_browser = st.tabs(["Topology Explorer", "Module Browser"])
    with tab_topo:
        _topology_explorer(modules, topologies)
    with tab_browser:
        _module_browser(modules)


main()
