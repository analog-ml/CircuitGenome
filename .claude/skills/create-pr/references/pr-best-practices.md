# Pull Request Best Practices

## PR Philosophy

A pull request is a conversation about a change. It should tell a complete story about why the change is necessary, what it does, and how it was tested.

## Writing Effective PR Descriptions

### The Summary Section
The summary should answer three questions:
1. **What** is being changed?
2. **Why** is this change necessary?
3. **How** does this solve the problem?

Good example:
> This PR adds rate limiting to the authentication API to prevent brute-force attacks. After analyzing our logs, we identified multiple attempted attacks that could have been prevented with proper rate limiting. This implementation uses a sliding window algorithm to limit users to 5 login attempts per minute.

### The Changes Section
- Group related changes together
- Use present tense ("Add" not "Added")
- Be specific about modifications
- Explain non-obvious changes

Example:
```markdown
## Changes
Authentication:
- Add rate limiting middleware to /auth/login endpoint
- Implement sliding window counter in Redis
- Add rate limit headers to responses

Configuration:
- Add RATE_LIMIT_WINDOW and RATE_LIMIT_MAX_ATTEMPTS env variables
- Update .env.example with new configuration

Testing:
- Add unit tests for rate limiting logic
- Add integration tests for authentication flow with rate limits
```

### Breaking Changes
Always clearly document breaking changes:
- What will break
- Why it's necessary
- Migration steps
- Timeline if deprecating

Example:
```markdown
## Breaking Changes
The `/api/v1/users` endpoint now requires authentication.

**Migration Steps:**
1. Update all API calls to include Authorization header
2. Obtain API tokens from /auth/token endpoint
3. Update client libraries to version 2.0+

**Deprecation Timeline:**
- 2024-01-15: Deprecation warning added
- 2024-02-15: Authentication enforced
```

## PR Patterns by Type

### Feature PRs
Focus on:
- User value and use cases
- Documentation updates needed
- Feature flags or gradual rollout strategy
- Performance implications
- Security considerations

### Bug Fix PRs
Include:
- Root cause analysis
- Steps to reproduce the bug
- How the fix addresses the root cause
- Regression test details
- Affected versions/environments

### Refactoring PRs
Explain:
- Why refactoring is needed now
- What stays the same (behavior/API)
- What improves (performance/maintainability)
- Risk assessment
- Testing strategy to ensure no regressions

### Documentation PRs
Ensure:
- Accuracy of technical details
- Clarity for target audience
- Proper formatting and structure
- Working code examples
- Updated diagrams if applicable

## Commit Message Formats

### Conventional Commits
```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Code style (formatting, missing semicolons)
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `perf`: Performance improvement
- `test`: Adding missing tests
- `chore`: Maintenance tasks
- `build`: Changes to build system
- `config`: Changes to CI configuration

Examples:
- `feat(auth): add OAuth2 authentication`
- `fix(api): resolve memory leak in data processor`
- `docs(readme): update installation instructions`
- `refactor(payments): simplify transaction handling`

### Scope Guidelines
- Use component/module names
- Keep it concise (one word when possible)
- Be consistent across the project
- Common scopes: api, ui, auth, db, config, deps

## Review Readiness Checklist

Before marking PR ready for review:

### Code Quality
- [ ] Code follows project style guidelines
- [ ] No commented-out code
- [ ] No debug prints or console.logs
- [ ] Meaningful variable and function names
- [ ] Complex logic has comments

### Testing
- [ ] All tests pass locally
- [ ] New features have tests
- [ ] Bug fixes have regression tests
- [ ] Edge cases are tested
- [ ] Performance impact measured if applicable

### Documentation
- [ ] API changes documented
- [ ] README updated if needed
- [ ] Inline comments for complex logic
- [ ] CHANGELOG updated
- [ ] Migration guide for breaking changes

### PR Hygiene
- [ ] PR has descriptive title
- [ ] PR has comprehensive description
- [ ] Related issues are linked
- [ ] Commits are logical and atomic
- [ ] No merge commits (rebase instead)
- [ ] PR is focused on single concern
- [ ] Sensitive data is not exposed

## Tips for Faster Reviews

1. **Keep PRs Small**
   - Aim for <500 lines of changes
   - Split large features into multiple PRs
   - Create "stacked" PRs that build on each other

2. **Make It Easy to Review**
   - Add review comments explaining complex parts
   - Include before/after screenshots for UI changes
   - Provide testing instructions
   - Link to relevant documentation

3. **Be Responsive**
   - Address feedback promptly
   - Ask questions if feedback is unclear
   - Re-request review after making changes

4. **Use Draft PRs**
   - Create early for visibility
   - Get feedback on approach
   - Mark ready when complete

## Common Anti-Patterns to Avoid

### The Monster PR
- Too many changes in one PR
- Multiple unrelated changes
- Difficult to review thoroughly
- Higher risk of bugs

### The Mystery PR
- Vague title like "Fix stuff"
- No description
- No context for reviewers
- Unclear what problem it solves

### The Never-Ending PR
- Keeps growing with new changes
- Review feedback leads to scope creep
- Becomes stale and conflicts arise
- Consider closing and starting fresh

### The Rushed PR
- Skipped tests
- No documentation
- Bypassed CI checks
- "I'll fix it later" mentality

## Template Examples

### Feature PR Template
```markdown
## Summary
This PR implements [feature name] to address [user need/problem].

## Motivation
- Current limitation: [describe current state]
- User impact: [how users are affected]
- Proposed solution: [brief overview]

## Implementation Details
- [Key technical decisions]
- [Architecture changes]
- [New dependencies]

## Testing
- [x] Unit tests for [components]
- [x] Integration tests for [flows]
- [x] Manual testing steps completed
- [x] Performance benchmarks run

## Documentation
- [x] API documentation updated
- [x] User guide updated
- [x] Migration guide (if applicable)

## Screenshots/Demo
[Include relevant visuals]

## Rollout Plan
- [ ] Feature flag: `enable_new_feature`
- [ ] Gradual rollout to X% of users
- [ ] Monitoring alerts configured
```

### Bug Fix PR Template
```markdown
## Summary
Fixes [issue description] that was causing [user impact].

## Root Cause
[Explain what was broken and why]

## Solution
[Explain how the fix works]

## Reproduction Steps
1. [Step to reproduce]
2. [Another step]
3. [Expected vs actual behavior]

## Testing
- [x] Regression test added
- [x] Manually verified fix in [environment]
- [x] No side effects identified

## Affected Versions
- Introduced in: v2.3.0
- Affects: v2.3.0 - v2.5.1
- Fixed in: v2.5.2
```

## Post-PR Best Practices

### After Approval
1. Squash commits if needed
2. Update branch with latest changes
3. Verify CI still passes
4. Monitor after deployment

### After Merge
1. Delete feature branch
2. Update related documentation
3. Close related issues
4. Notify stakeholders if needed
5. Monitor for issues
