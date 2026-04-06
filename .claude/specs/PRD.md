# PRD — Grove
**Version:** 0.5  
**Author:** Simon Allen / Digital Wonderlab  
**Date:** 2026-04-03  
**Status:** Phase 1 in development

---

## Problem Statement

Researchers, consultants, and analysts accumulate large document collections they can't synthesise at scale. Existing LLM tools offer ephemeral chat (RAG / Projects) or manual note-taking with AI assist. Neither produces durable, structured, compounding knowledge.

The proven second-brain frameworks (PARA, Zettelkasten, Evergreen Notes, MOCs) all work — but fail because humans can't maintain them at scale. The maintenance cost is the bottleneck.

---

## Solution

Grove is a knowledge compiler. The LLM is the author and maintainer of a structured markdown wiki. Users ingest sources; the LLM compiles articles, maintains cross-references, and detects contradictions. Queries can be filed back in. Knowledge compounds across every cycle.

**Three paradigms:**
1. RAG — search and retrieve. Ephemeral.
2. Conversation (Claude Projects) — persist chat. No structure.
3. **Compilation (Grove) — the LLM writes and maintains a second brain.**

---

## Target Users

### Primary: Researchers & Analysts
50–500 source documents. Need synthesis, not search.  
*Door-opener: "I have 200 papers and I can't keep it all in my head."*

### Secondary: Consultants & Strategists
Knowledge compounding across engagements.  
*Door-opener: "Every engagement starts with the same research phase."*

### Tertiary: Developer-Researchers
CLI-comfortable, markdown-native.  
*Door-opener: "I want my LLM to actually understand this domain."*

### Anti-Personas
- People who want a note-taking app → use Obsidian
- People who want chat Q&A over a few docs → use Claude Projects
- People who don't already use LLMs → Grove enhances an existing habit

---

## Product Form (Phased)

### Phase 1: Obsidian Plugin + CLI Engine (Current)
- Open-source Python compilation engine (CLI)
- TypeScript Obsidian plugin (compile, query, file commands)
- BYOK (bring your own API key)
- Local filesystem, git-native
- Free

### Phase 2: Web Platform + Clipper
- Hosted groves, web UI, browser extension
- Hosted compilation (no BYOK required for hosted tier)
- MCP server for power users
- Grove-to-grove queries
- Pro (£15/mo) and Team (£25/user/mo) tiers

### Phase 3: Ecosystem
- Community prompt library / marketplace
- Partner integrations (Readwise, Zotero)
- Incremental compilation with dependency graph
- Enterprise pilot features

---

## Success Metrics

### Phase 1 Launch
- 100+ plugin installs in first month
- 20+ users complete at least one compilation
- Domain expert rates ≥80% of compiled articles as "useful"
- Zero data-integrity incidents

### Phase 2
- 500+ web platform signups
- 50+ active web users
- 20+ Pro subscribers
- £300+ MRR

---

## Validated Assumptions (Spikes)

| Spike | Question | Result |
|-------|----------|--------|
| Spike 0 | Does knowledge compound? | PASSED — 23% improvement (4.72 vs 3.84 / 5.0) |
| Spike 1 | Can compiler produce good articles? | PASSED — 73–91% usefulness, 0–2% hallucination across 3 domains |
| Spike 2 | Incremental compilation quality | NOT YET RUN |
| Spike 3 | Local model viability | NOT YET RUN |

---

## Scale Limits (Honest)

| Range | Approach | Cost |
|-------|----------|------|
| 10–100 sources | Brute force (1M context) | ~$0.50 full compile |
| 100–500 sources | Incremental + dependency graph | ~$0.15 incremental |
| 500–2,000 sources | Hierarchical indexes, sub-wikis | Degraded quality |
| 2,000+ sources | Wrong tool — export to RAG | — |

---

## Business Model

**Open-source core (MIT):** Compilation engine, CLI, prompts, quality ratchet, provenance system. Free forever.

**Hosted platform (Phase 2):**
- Free: 1 grove, 50 sources, BYOK
- Pro: £15/mo — unlimited groves, web clipper, hosted compilation
- Team: £25/user/mo — shared groves, admin controls
- Enterprise: custom

**Consulting funnel:** Enterprise Grove adopters become DWL AI strategy leads. Higher value than SaaS.

---

## Key Risks

| Risk | Mitigation |
|------|-----------|
| Articles mediocre | Validated by spikes. Ongoing prompt iteration |
| Obsidian community doesn't adopt | Validate with 10 trusted users before launch |
| Setup friction | `grove init` creates structure; demo vault shipped |
| Well-funded clone | First-mover + open source + consulting funnel |
| Compilation shifts, not solves, maintenance burden | Monitor meta-maintenance load in dogfood phase |
