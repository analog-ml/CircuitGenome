import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "CircuitGenome"
author = "CircuitGenome Contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = False
napoleon_numpy_docstring = False

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

html_theme = "furo"
html_theme_options = {
    "light_logo": "logo_transparent.png",
    "dark_logo": "logo_transparent.png",
    "source_repository": "https://github.com/analog-ml/CircuitGenome",
    "source_branch": "main",
    "source_directory": "docs/",
}
html_static_path = ["_static", "images"]
html_css_files = ["custom.css"]
html_title = "CircuitGenome"

autodoc_preserve_defaults = True
