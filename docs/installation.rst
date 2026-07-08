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

Building the documentation
--------------------------

Install the documentation dependencies (Sphinx and the Furo theme), then
build:

.. code-block:: bash

   pip install -r docs/requirements.txt
   cd docs
   make html

The generated site appears in ``docs/_build/html/``.
