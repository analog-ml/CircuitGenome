Contributing
============

CircuitGenome is an open, evolving project, and contributions of every kind are
welcome — from a one-line typo fix to a new circuit class.  It is built by and
for practitioners, so **your experience using it is itself a contribution**: if a
result looks wrong, a topology does not bias the way theory says it should, or the
docs left you guessing, telling us is valuable.

Ways to contribute
------------------

- **Code.** New module variants, topology templates, sizer improvements, bug
  fixes, tests.  Many extensions need *no* Python at all — module variants and
  templates are plain YAML; see :doc:`extending`.
- **Documentation.** Fixes, clarifications, worked examples, or new theory pages.
  Docs are as important as code here — this project aims to be well-documented for
  practitioners, and that only holds if readers help keep it honest.

  The :doc:`walkthroughs <walkthroughs>` are **living documents**: if your PR
  materially changes behavior that a walkthrough describes, update the
  walkthrough (and the module page) in the same PR.  Cosmetic refactors are
  exempt.  You are welcome to use an LLM to generate or update a walkthrough —
  but you take responsibility for its correctness, exactly as you would for
  code.
- **Ideas and insights.** Design-space suggestions, references, methodology
  feedback, or proposals for new circuit types on the :ref:`roadmap <roadmap>`.
- **"This is electrically wrong" reports.** The most valuable feedback of all.
  If a sized design does not physically work — a stage that cannot bias, an
  optimistic gain the silicon would never deliver, a compensation scheme that is
  unstable in practice, a metric that disagrees with SPICE or with your bench —
  please report it.  Analog correctness is hard, and these reports are how the
  models get better.  Include the topology, spec, technology, and what you
  observed versus what CircuitGenome reported.

How to contribute
-----------------

CircuitGenome is developed on GitHub:
`analog-ml/CircuitGenome <https://github.com/analog-ml/CircuitGenome>`_.

- **Report a bug or request a feature** — open an
  `issue <https://github.com/analog-ml/CircuitGenome/issues>`_.  For a bug,
  include a minimal reproduction (topology, spec YAML, tech, and the command or
  code you ran) and what you expected versus what happened.  For an
  electrical-correctness report, follow the guidance above.
- **Submit a change** — fork the repository, create a branch, and open a
  `pull request <https://github.com/analog-ml/CircuitGenome/pulls>`_.  Set up a
  development install with ``pip install -e .`` (see :doc:`installation`) and run
  the test suite with ``pytest`` before opening the PR.  Small, focused PRs are
  easiest to review.

Not sure where something fits, or want to discuss a larger change before building
it?  Open an issue first — early discussion saves rework.
