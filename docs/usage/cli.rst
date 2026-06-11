CLI Usage
=========

All commands go through the ``circuitgenome synthesize`` subcommand.

.. code-block:: bash

   circuitgenome synthesize [OPTIONS]

.. tip::
   If ``circuitgenome`` is not found, run it as
   ``python3 -m circuitgenome.cli synthesize ...`` or see
   :doc:`../installation` for the PATH fix.

Listing available resources
---------------------------

.. code-block:: bash

   # Show all topology templates
   circuitgenome synthesize --list-topologies

.. code-block:: text

     one_stage_opamp  (stages=1, output=single_ended)
     two_stage_opamp_single_ended  (stages=2, output=single_ended)
     two_stage_opamp_fully_differential  (stages=2, output=fully_differential)
     three_stage_opamp_nmc_single_ended  (stages=3, output=single_ended, compensation=nested_miller)
     three_stage_opamp_rnmc_single_ended  (stages=3, output=single_ended, compensation=reversed_nested_miller)
     three_stage_opamp_nmc_fully_differential  (stages=3, output=fully_differential, compensation=nested_miller)
     three_stage_opamp_rnmc_fully_differential  (stages=3, output=fully_differential, compensation=reversed_nested_miller)

.. code-block:: bash

   # Show all module variants, grouped by category
   circuitgenome synthesize --list-modules

.. code-block:: text

   [input_pair]
     differential_pair_pmos — PMOS Differential Pair
     differential_pair_nmos — NMOS Differential Pair
     ...

Generating circuits
-------------------

.. code-block:: bash

   # 1-stage, flat SPICE (default format), written to ./circuits/
   circuitgenome synthesize --stages 1 --output-dir ./circuits/

   # 2-stage single-ended, both flat and hierarchical SPICE
   circuitgenome synthesize \
     --stages 2 \
     --output-type single_ended \
     --format both \
     --output-dir ./circuits/

   # Fully differential topology only
   circuitgenome synthesize \
     --topology two_stage_opamp_fully_differential \
     --format hierarchical \
     --output-dir ./circuits/

   # 3-stage, Reversed Nested Miller Compensation, single-ended
   circuitgenome synthesize \
     --topology three_stage_opamp_rnmc_single_ended \
     --output-dir ./circuits/

   # Dry run — count without writing any files
   circuitgenome synthesize --stages 2 --dry-run

Sample output::

   Topology: two_stage_opamp_single_ended
     Generated 4050 circuits

   Total: 4050 circuits written to ./circuits/

Output filenames follow the pattern ``circuit_NNNN_flat.ckt`` /
``circuit_NNNN_hier.ckt``, numbered sequentially within each topology.

Options reference
-----------------

.. list-table::
   :header-rows: 1
   :widths: 35 45 20

   * - Flag
     - Description
     - Default
   * - ``--stages 1|2|3``
     - Filter to topologies with this many stages
     - all
   * - ``--output-type``
     - ``single_ended`` or ``fully_differential``
     - all
   * - ``--topology NAME``
     - Use one specific topology by name
     - all
   * - ``--format flat|hierarchical|both``
     - SPICE output format
     - ``flat``
   * - ``--output-dir PATH``
     - Directory for output files (created if absent)
     - ``.``
   * - ``--dry-run``
     - Count circuits without writing files
     - off
   * - ``--list-topologies``
     - Print topology names and exit
     - —
   * - ``--list-modules``
     - Print module variants and exit
     - —
