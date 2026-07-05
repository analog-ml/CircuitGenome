"""Sphinx directive that tabulates how many circuits each topology template
enumerates.

The counts are produced at documentation build time by running the real
:func:`~circuitgenome.synthesizer.enumerate_circuits` on every template loaded
by :func:`~circuitgenome.synthesizer.load_topologies`, so the table can never
drift from the code.  Usage in an ``.rst`` file::

    .. topology-counts::
"""

from docutils import nodes
from docutils.parsers.rst import Directive
from docutils.statemachine import ViewList


def _count_per_template():
    """Return ``[(template_name, circuit_count), ...]`` under the default
    enumeration config (``unsupported``/``bias_infeasible`` variants excluded).
    """
    from circuitgenome.synthesizer.loader import load_modules, load_topologies
    from circuitgenome.synthesizer import enumerate_circuits

    modules = load_modules()
    return [
        (t.name, sum(1 for _ in enumerate_circuits(t, modules)))
        for t in load_topologies()
    ]


class TopologyCountsDirective(Directive):
    has_content = False

    def run(self):
        rows = _count_per_template()
        total = sum(n for _, n in rows)

        lines = [
            ".. list-table::",
            "   :header-rows: 1",
            "   :widths: 70 30",
            "",
            "   * - Template name",
            "     - Circuits generated",
        ]
        for name, n in rows:
            lines.append(f"   * - ``{name}``")
            lines.append(f"     - {n:,}")
        lines.append("   * - **Total**")
        lines.append(f"     - **{total:,}**")

        view = ViewList(lines, source="<topology-counts>")
        container = nodes.Element()
        self.state.nested_parse(view, self.content_offset, container)
        return container.children


def setup(app):
    app.add_directive("topology-counts", TopologyCountsDirective)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
