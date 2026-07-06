References
==========

The design of CircuitGenome draws on the following publications:

1. **A Data-Driven Analog Circuit Synthesizer with Automatic Topology
   Selection and Sizing** — S. Poddar, A. F. Budak, L. Zhao, C.-H. Hsu,
   S. Maji, K. Zhu, Y. Jia, and D. Z. Pan. *Design, Automation & Test in
   Europe Conference (DATE)*, 2024. Inspired the performance-dataset
   generation workflow using enumerated topology variants.

2. **FUBOCO: Structure Synthesis of Basic Op-Amps by FUnctional BlOck
   COmposition** — I. Abel and H. Graeb. *ACM Transactions on Design
   Automation of Electronic Systems (TODAES)*, 2022. The primary reference
   for functional block composition, canonical module interfaces, and
   topology templates.

3. **A Functional Block Decomposition Method for Automatic Op-Amp Design** —
   I. Abel, M. Neuner, and H. Graeb. *Integration, the VLSI Journal*
   (Elsevier), 2022. Used for the functional block recognition rules and the
   hierarchical decomposition of flat netlists.

4. **Constraint-Programmed Initial Sizing of Analog Operational Amplifiers** —
   I. Abel, M. Neuner, and H. Graeb. *IEEE International Conference on Computer
   Design (ICCD)*, 2019. The basis for the constraint-programming (CP-SAT)
   formulation used by the Sizer (SZ).

Full PDFs are available in the ``docs/papers/`` directory of the repository.
