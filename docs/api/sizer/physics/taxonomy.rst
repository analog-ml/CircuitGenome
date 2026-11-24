Template Taxonomy
=================

.. automodule:: circuitgenome.sizer.physics.taxonomy
   :members:
   :undoc-members:
   :show-inheritance:

**Running example**: `circuit_0010`'s twelve devices, as the sizer sees them (slots from the
recognizer, groups and the signal test from this file):

.. list-table::
   :header-rows: 1

   * - Device
     - Slot
     - Group
     - Gate net
     - ``is_signal_device``
     - Planned current
   * - m1_input_pair, m2_input_pair
     - input_pair
     - HALF
     - in1 / in2
     - True
     - 10 µA each
   * - m1_load, m2_load
     - load
     - HALF
     - net_diff1
     - True
     - 10 µA each
   * - m1_tail_current, m2_tail_current
     - tail_current
     - FULL
     - net_bias7
     - False
     - 20 µA
   * - mn1_second_stage
     - second_stage
     - SECOND
     - net_mid
     - True
     - 50 µA
   * - mp1_second_stage
     - second_stage
     - SECOND
     - net_bias5
     - False
     - 50 µA
   * - mnref, mn5, mn7_bias_gen
     - bias_gen
     - FULL
     - ibias
     - False
     - 20 µA
   * - mp5_bias_gen
     - bias_gen
     - FULL
     - net_bias5
     - False
     - 20 µA
