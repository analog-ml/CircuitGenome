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

html_theme = "alabaster"
html_theme_options = {
    "logo": "logo_transparent.png",
    "description": "Analog circuit topology synthesis and recognition",
    "github_user": "analog-ml",
    "github_repo": "CircuitGenome",
    "github_button": True,
    "github_type": "star",
    "fixed_sidebar": True,
    "sidebar_width": "240px",
    "body_max_width": "960px",
    "font_size": "15px",
    "code_font_size": "0.85em",
}
html_sidebars = {
    "**": [
        "about.html",
        "navigation.html",
        "relations.html",
        "searchbox.html",
    ]
}
html_static_path = ["_static", "images"]
html_title = "CircuitGenome"
