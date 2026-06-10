Installation
============

Requirements
------------

- Python 3.9 or later
- `PyYAML <https://pyyaml.org/>`_ ≥ 6.0 (installed automatically)

Install from source
-------------------

.. code-block:: bash

   git clone https://github.com/analog-ml/CircuitGenome.git
   cd CircuitGenome
   pip install -e .

CLI not found after install?
-----------------------------

On macOS, ``pip install --user`` places scripts in
``~/Library/Python/3.x/bin/``, which is not on the default ``zsh`` PATH.
You will see::

   zsh: command not found: circuitgenome

Fix it by adding the directory to your PATH once:

.. code-block:: bash

   echo 'export PATH="$HOME/Library/Python/3.9/bin:$PATH"' >> ~/.zshrc
   source ~/.zshrc

Alternatively, invoke the CLI through the Python module directly:

.. code-block:: bash

   python3 -m circuitgenome.cli synthesize --list-topologies

Building the documentation
--------------------------

Install Sphinx and the Alabaster theme, then build:

.. code-block:: bash

   pip install sphinx
   cd docs
   make html

The generated site appears in ``docs/_build/html/``.
