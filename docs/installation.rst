Installation
============

Requirements
------------

- Python 3.9 or later
- `PyYAML <https://pyyaml.org/>`_ ≥ 6.0 (installed automatically)

Install from PyPI
------------------

.. code-block:: bash

   pip install circuitgenome

Install from source
-------------------

.. code-block:: bash

   git clone https://github.com/analog-ml/CircuitGenome.git
   cd CircuitGenome
   pip install -e .

Running the tests
-----------------

The test runner, pytest, is declared in the ``dev`` dependency group.  With
`uv <https://docs.astral.sh/uv/>`_, which installs that group by default:

.. code-block:: bash

   uv run pytest tests/

With pip 25.1 or later:

.. code-block:: bash

   pip install -e . --group dev
   pytest tests/

Tests that simulate with ngspice are skipped when ``ngspice`` is not on your
``PATH``.

Building the documentation
--------------------------

Install the documentation dependencies (Sphinx and the Furo theme), then
build:

.. code-block:: bash

   pip install -r docs/requirements.txt
   cd docs
   make html

The generated site appears in ``docs/_build/html/``.
