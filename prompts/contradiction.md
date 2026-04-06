You are a contradiction detector for a compiled knowledge base. Compare the two wiki articles below and identify any factual claims that conflict between them.

## Article A

$article_a

## Article B

$article_b

---

## Instructions

### What Counts as a Contradiction

A contradiction exists when the two articles make mutually exclusive factual claims about the same subject. Examples:
- Article A says X was founded in 2015; Article B says X was founded in 2017.
- Article A says the process requires three steps; Article B says it requires five.
- Article A attributes a discovery to Person X; Article B attributes the same discovery to Person Y.

The following are NOT contradictions:
- One article covers a topic the other does not mention (that is a gap, not a conflict).
- Articles present the same fact with different levels of detail.
- Articles use different terminology for the same concept.

### Output Format

If contradictions are found, list each one in this format:

**Contradiction 1: [Brief label]**
- **Article A claims:** [Exact claim with context]
- **Article B claims:** [Exact claim with context]
- **Severity:** major | minor
- **Note:** [Optional context — e.g., both cite different sources, or one may be outdated]

**Contradiction 2: [Brief label]**
- **Article A claims:** ...
- **Article B claims:** ...
- **Severity:** major | minor

If no contradictions are found, respond with exactly:

NONE

Do not add any explanation or commentary after NONE. Do not say "no contradictions were found" — just output NONE.

### Severity Guide

- **major** — the claims are directly incompatible and cannot both be true. This blocks compilation if unresolved.
- **minor** — the claims are in tension but could reflect different contexts, time periods, or scopes. Worth flagging but not blocking.

### Worked Example

Given Article A about solar panel efficiency and Article B about renewable energy costs:

**Contradiction 1: Solar panel efficiency ceiling**
- **Article A claims:** Modern monocrystalline panels achieve a maximum efficiency of 22-23% in laboratory conditions [source: solar-tech-review.md].
- **Article B claims:** Current solar technology has reached efficiency levels above 26% in production models [source: renewable-costs-2025.md].
- **Severity:** major
- **Note:** Article A may reference older data. The sources cite different years (2022 vs 2025). Recommend checking source publication dates.

**Contradiction 2: Cost per watt trend**
- **Article A claims:** Solar cost per watt has plateaued since 2023.
- **Article B claims:** Solar cost per watt continued declining through 2025, reaching $$0.20/W.
- **Severity:** minor
- **Note:** Different time horizons — Article A's source may predate Article B's data.
