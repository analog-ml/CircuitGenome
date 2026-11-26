---
name: create-pr
description: Create well-structured pull requests by analyzing git diffs, determining appropriate PR metadata, and using the GitHub CLI. This skill should be used when the user wants to create a pull request from their current branch changes, needs help composing PR descriptions, or wants to streamline the PR creation process.
---

# Create PR

## Overview

This skill enables intelligent pull request creation by analyzing repository changes, determining appropriate PR metadata, and composing comprehensive PR descriptions using the GitHub CLI.

## Workflow

### Step 1: Verify Prerequisites

Before proceeding, check that the GitHub CLI is installed and authenticated:

```bash
# Check if gh is installed
which gh || command -v gh
```

If `gh` is not installed:
1. Inform the user that the GitHub CLI is required
2. Provide installation instructions based on their platform:
   - macOS: `brew install gh`
   - Linux: See https://github.com/cli/cli/blob/trunk/docs/install_linux.md
   - Windows: `winget install --id GitHub.cli` or `choco install gh`
3. After installation, guide them to authenticate: `gh auth login`
4. Wait for user confirmation before proceeding

### Step 2: Analyze Repository State

Gather comprehensive information about the current branch and changes:

```bash
# Check current branch and its tracking status
git status

# Check if current branch tracks a remote
git branch -vv | grep "^\\*"

# List recent commits on this branch compared to base
git log --oneline origin/main..HEAD 2>/dev/null || git log --oneline origin/master..HEAD 2>/dev/null || git log --oneline HEAD~5..HEAD

# View the full diff to understand changes
git diff origin/main...HEAD 2>/dev/null || git diff origin/master...HEAD 2>/dev/null || git diff HEAD~5..HEAD
```

### Step 3: Check for Unpushed Changes

Determine if there are local commits that need to be pushed:

```bash
# Check for unpushed commits
git log --oneline @{u}..HEAD 2>/dev/null || echo "Branch not tracking remote"
```

If unpushed commits exist:
1. Inform the user about the unpushed commits
2. Ask if they want to push the changes now
3. If yes, push with upstream tracking: `git push -u origin <branch-name>`
4. If no, inform them that the PR cannot be created until changes are pushed

### Step 4: Analyze Changes and Determine PR Metadata

Based on the diff analysis, determine:

1. **PR Type** - Analyze the changes to categorize:
   - Feature: New functionality added
   - Fix: Bug fixes or corrections
   - Refactor: Code restructuring without behavior changes
   - Docs: Documentation updates
   - Test: Test additions or modifications
   - Chore: Maintenance tasks, dependency updates
   - Perf: Performance improvements

2. **PR Title** - Create a concise, descriptive title following conventional commit format:
   - `feat: Add user authentication system`
   - `fix: Resolve memory leak in data processor`
   - `refactor: Simplify payment gateway integration`

3. **Breaking Changes** - Check for:
   - API changes that remove or rename public methods
   - Database schema changes
   - Configuration format changes
   - Dependency version major upgrades

4. **Test Requirements** - Identify what testing is needed:
   - Unit tests for new functions
   - Integration tests for API changes
   - Manual testing steps for UI changes

### Step 5: Compose PR Description

Create a comprehensive PR description using this template structure:

```markdown
## Summary
[1-3 sentences explaining what this PR does and why]

## Changes
- [Bulleted list of specific changes made]
- [Group related changes together]
- [Be specific about what was added, modified, or removed]

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Performance improvement
- [ ] Refactoring (no functional changes)

## Testing
- [ ] [Describe testing performed]
- [ ] [List any new tests added]
- [ ] [Note any manual testing steps]

## Breaking Changes
[If applicable, describe any breaking changes and migration steps]

## Related Issues
[Reference any related issues: Fixes #123, Relates to #456]

## Screenshots
[If applicable, add screenshots to help explain your changes]

## Additional Context
[Any other context about the PR that reviewers should know]
```

### Step 6: Confirm with User

Present the composed PR details to the user:

1. Show the determined PR type and title
2. Display the full PR description
3. Show the target branch (typically main or master)
4. Ask for confirmation or modifications

Allow the user to:
- Modify the title or description
- Change the target branch
- Add reviewers or labels
- Cancel the PR creation

### Step 7: Create the Pull Request

Once confirmed, create the PR using the GitHub CLI:

```bash
# Create PR with composed title and body
gh pr create \
  --title "PR Title Here" \
  --body "PR Description Here" \
  --base main  # or master, or user-specified branch

# Optionally add reviewers if requested
gh pr create \
  --title "..." \
  --body "..." \
  --reviewer "username1,username2"

# Optionally add labels if requested
gh pr create \
  --title "..." \
  --body "..." \
  --label "enhancement,needs-review"
```

After creation:
1. Display the PR URL
2. Offer to open it in the browser: `gh pr view --web`
3. Show next steps (review process, CI checks, etc.)

## Best Practices

### Effective PR Titles
- Keep under 72 characters
- Use imperative mood ("Add feature" not "Added feature")
- Be specific but concise
- Include ticket/issue numbers if applicable

### Comprehensive Descriptions
- Explain the "why" not just the "what"
- Include context for reviewers
- Link to relevant documentation or discussions
- Add screenshots for UI changes
- List manual testing steps

### PR Size Guidelines
- Keep PRs focused on a single concern
- Aim for <500 lines of code changes
- Split large features into multiple PRs
- Create draft PRs for work in progress

## References

For detailed PR best practices and additional context, refer to:
- `references/pr-best-practices.md` - Comprehensive guide for writing effective PRs

## Error Handling

Common issues and solutions:

1. **No remote tracking**: Set upstream with `git push -u origin branch-name`
2. **Authentication failed**: Run `gh auth login` to re-authenticate
3. **PR already exists**: Check existing PRs with `gh pr list`
4. **Base branch not found**: Verify branch names with `git branch -r`
5. **Merge conflicts**: Resolve locally first with `git merge origin/main`
