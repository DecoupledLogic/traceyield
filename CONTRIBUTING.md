# Contributing to TraceYield

Thank you for helping improve TraceYield.

TraceYield is both a discipline for managing the cost and efficacy of LLM interactions and an open-source reference tool that implements that discipline. Contributions should help teams understand their usage, diagnose waste, predict outcomes, recommend improvements, or verify that a change worked.

This guide explains how to propose, build, test, and submit changes.

## Ways to contribute

Useful contributions include:

- Fixing bugs
- Improving provider ingestion
- Adding support for new coding agents or transcript formats
- Strengthening the canonical data model
- Improving reports, diagnostics, forecasts, and recommendations
- Adding tests and synthetic fixtures
- Improving documentation
- Identifying privacy, security, performance, or data-quality risks
- Proposing new TraceYield operations or measurable optimization levers

Small fixes may be submitted directly. For significant changes, open an issue or discussion first so the problem, scope, and approach can be aligned before substantial work begins.

## Before starting a significant change

Please describe:

1. The problem being solved
2. The users or workflows affected
3. The proposed behavior
4. Any changes to the canonical data model
5. Privacy and security implications
6. Compatibility implications
7. How the change will be tested
8. How success will be measured

A significant change includes a new provider, schema change, new runtime dependency, new command, major report section, pricing behavior change, or anything that changes how cost, savings, or efficacy is calculated.

## Project principles

Contributions should preserve these principles.

### Optimize value per token

The goal is not simply to minimize token usage. A lower-cost interaction that fails the task is not an improvement. Features and recommendations should consider usefulness, quality, cost, latency, and the likelihood of successful completion.

### Keep the core provider-neutral

Provider-specific data should be translated into the canonical TraceYield model. Provider details should not leak into shared analysis unless the distinction is necessary and explicitly modeled.

New providers should produce the same neutral concepts used by the rest of the system, including sessions, turns, tool calls, segments, and raw events.

### Treat interaction material as sensitive

Prompts, reasoning, responses, tool calls, file paths, repository names, and user identifiers may contain private or proprietary information.

Contributions must:

- Keep data local by default
- Avoid transmitting interaction data without explicit user action
- Minimize collection to what the feature requires
- Redact or omit secrets and personal information from tests, examples, screenshots, and issues
- Use synthetic fixtures instead of real transcripts
- Document any new data collection, storage, export, or network behavior

Never commit API keys, credentials, private transcripts, customer data, or proprietary source code.

### Preserve evidence

Counts identify where to look. Interaction material explains why something happened. Diagnostics and recommendations should retain enough evidence for a user to inspect and challenge the result.

Do not present an inference as a fact. Label estimates, assumptions, confidence, and upper bounds clearly.

### Close the loop

TraceYield should move beyond observation. Where practical, changes should help the user:

1. Describe what happened
2. Diagnose why it happened
3. Predict what is likely to happen
4. Prescribe a change
5. Remediate and measure the result

Not every contribution must implement all five operations, but it should fit coherently into the loop.

### Prefer a small, understandable core

TraceYield is currently built as a standard-library-only Python tool. Avoid adding runtime dependencies unless the value clearly exceeds the operational and supply-chain cost.

A dependency proposal should explain:

- Why the standard library is insufficient
- The dependency's maintenance and security posture
- Its license
- Its effect on installation and distribution
- Whether it is optional or required

## Development workflow

1. Fork the repository.
2. Create a focused branch from the current default branch.
3. Make one coherent change.
4. Add or update tests and fixtures.
5. Update affected documentation.
6. Run the relevant checks locally.
7. Open a pull request with the required context.

The `main` branch is protected: all changes land through a pull request, and the `ci` check (build + test suite, see below) must pass before a PR can be merged.

Keep pull requests small enough to review. Separate unrelated refactoring from behavioral changes whenever practical.

### How work is tracked

Open work is surfaced as GitHub Issues so you can follow and claim it, but the source of truth is the repository's roadmap (`roadmap.csv` and the `docs/delivery` companions). Issues are a one-way mirror of that roadmap, maintained by the maintainer via `scripts/sync-issues.py`; a contributor never needs to touch the roadmap. To pick up an issue, comment to claim it and open a PR that references it (`Closes #123`). When your PR merges, the maintainer reconciles the roadmap. You do not edit `roadmap.csv` or the companions in your PR.

## Testing expectations

Every behavioral change should include evidence that it works.

Use deterministic tests for parsing, normalization, pricing, aggregation, schema behavior, and report calculations. Use synthetic or sanitized fixtures for provider transcripts.

Tests should cover the normal path and meaningful failure conditions, including:

- Missing or malformed fields
- Unknown event types
- Schema drift
- Duplicate or replayed events
- Partial sessions
- Unsupported models or pricing records
- Empty datasets
- Large sessions
- Privacy-sensitive fields
- Cross-provider equivalence

A provider contribution should include at least:

- A minimal valid fixture
- A representative multi-turn fixture
- A malformed or partial fixture
- Expected canonical records
- A regression test for any provider-specific edge case

Changes to reports or recommendations should include stable input data and expected calculations. Screenshots may supplement tests, but they do not replace them.

Do not regenerate expected results blindly. Review changed output and explain why it is correct.

## Canonical data model changes

The canonical store is the shared contract between ingestion and analysis. Treat changes to it carefully.

A schema change should include:

- The reason the existing model is insufficient
- The proposed field or entity semantics
- Migration or compatibility behavior
- Effects on existing providers
- Effects on reports and saved databases
- Tests proving old and new data remain understandable

Prefer additive changes. Do not discard raw source events merely because the current model does not understand them.

## Pricing and savings calculations

Pricing changes can materially affect historical analysis and business decisions.

Any change involving cost, projected savings, burn rate, routing, caching, or optimization must document:

- The pricing source and effective date
- Units and conversion rules
- Input, output, cache-write, and cache-read treatment
- Assumptions
- Rounding behavior
- Whether the result is actual, estimated, projected, or an upper bound

Avoid language that presents estimated savings as guaranteed savings.

## Documentation

Update documentation in the same pull request when behavior changes.

Use:

- **TraceYield** for the discipline, framework, and brand
- **traceyield** for the repository, package, scripts, commands, and technical identifiers

Write plainly. Prefer concrete examples and explain why a number or recommendation matters.

## AI-assisted contributions

AI-assisted contributions are welcome. The contributor remains responsible for the submitted work.

Before submitting AI-assisted code or documentation:

- Review and understand every change
- Run the relevant tests
- Verify licenses and provenance
- Remove generated secrets, fabricated references, and unsupported claims
- Confirm the contribution does not contain third-party proprietary material
- Disclose material AI assistance in the pull request when it affected the design or produced a substantial portion of the change

An AI-generated patch is not evidence that the change is correct.

## Pull request checklist

A pull request should:

- Explain the problem and why it matters
- Describe the chosen approach
- Identify important alternatives or tradeoffs
- Link the related issue when one exists
- Include tests or explain why tests are not applicable
- Update relevant documentation
- Identify schema, privacy, compatibility, pricing, or performance effects
- Avoid unrelated formatting or refactoring
- Confirm that no sensitive data is included

Maintainers may ask for a change to be split into smaller pull requests.

## Commit guidance

Use clear, imperative commit messages that describe the change.

Examples:

```text
Add Codex tool-call normalization
Handle partial Claude transcript records
Label routing savings as an upper bound
Preserve unknown provider events
Document canonical schema migration
```

A perfect commit format is less important than a reviewable history.

## Licensing

Unless explicitly stated otherwise, contributions submitted to this repository are licensed under the Apache License 2.0.

By submitting a contribution, you represent that you have the right to submit it and to license it under the project's license. You retain copyright in your original contribution.

Do not submit code, fixtures, documentation, or other material that you do not have the right to contribute.

See [LICENSE](./LICENSE) for the software license.

## Trademarks and forks

The Apache License 2.0 permits use, modification, redistribution, repackaging, and commercial use of the software. It does not grant rights to the TraceYield name, logo, wordmark, or other brand assets.

Forks and derivative products may truthfully state that they are based on TraceYield, but they must use their own product name and visual identity unless they have written permission to use the TraceYield brand.

See [TRADEMARKS.md](./TRADEMARKS.md) before naming or distributing a fork, hosted service, package, or commercial product.

## Security reports

Do not open a public issue for a vulnerability that could expose private interaction data, credentials, local files, or remote systems.

Use the repository's private security-reporting channel when available. If no private channel is configured, contact the maintainer privately before publishing technical details.

Include:

- The affected version or commit
- Reproduction steps
- Expected and actual behavior
- Potential impact
- Any suggested mitigation

## Review and acceptance

Maintainers may decline a contribution because of scope, complexity, compatibility, maintenance cost, privacy risk, insufficient evidence, or conflict with the project's direction.

Submission does not guarantee acceptance. A declined pull request may still contain useful work, and maintainers should explain the decision clearly.

## Conduct

Be direct, respectful, and evidence-driven. Critique the work, not the person. Assume good intent, but do not avoid necessary technical disagreement.

The goal is to build a trustworthy system that helps teams improve how they use LLM interactions.
