# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

**Explaination**
- Explain any issues, problems, or decisions that arise during coding in layman's terms.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

# Git Commit Guidelines

When generating or executing git commits via `/commit` or Bash tools, you must explicitly use `Assisted-by` instead of `Co-Authored-By`. 

## Commit Message Rules
- Use the Conventional Commits format.
- Never append a `Co-Authored-By:` trailer.
- Always include the tool provenance at the bottom of the commit message using the exact format below:

Contributions should include an Assisted-by tag in the following format:

Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]
Where:

AGENT_NAME is the name of the AI tool or framework
MODEL_VERSION is the specific model version used
[TOOL1] [TOOL2] are optional specialized analysis tools used (e.g., coccinelle, sparse, smatch, clang-tidy)
Basic development tools (git, gcc, make, editors) should not be listed.

Example:

Assisted-by: Claude:claude-3-opus coccinelle sparse

# Code Drift Prevention Guidelines

You must ensure that tests, documentation, and walkthrough tutorials never drift from implementation or bug fix changes.

## Enforcement Protocol
- **Analyze Impact Before Coding:** Before modifying any implementation or fixing a bug, locate and analyze all relevant tests, Markdown documentation, and walkthrough tutorials.
- **Synchronized Updates:** You are required to update tests, internal documentation, and tutorials in the same atomic change or pull request as the code modification.
- **Verify Testing:** Always write or update corresponding tests for new features or bug fixes. Run the test suite using available Bash tools to verify that they pass before considering a task complete.
- **Tutorial Audit:** If a walkthrough tutorial exists for the modified feature, step through the tutorial logic mentally or via tests to ensure the instructions, code snippets, and expected outputs remain 100% accurate.

## Docstring & Inline Documentation Rules
- **Sync Docstrings Instantly:** When changing a function, class, or method signature, you must immediately update its docstring to reflect accurate parameter types, return values, raised exceptions, and behavioral descriptions.
- **Remove Stale Comments:** Delete or rewrite inline comments that no longer accurately describe the surrounding code logic. Do not leave obsolete "TODO" or "FIXME" blocks behind after resolving an issue.
- **Self-Documenting Code First:** Prioritize writing clean, self-documenting code over excessive inline comments. Reserve docstrings for public APIs, complex algorithmic logic, and unexpected edge cases.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
