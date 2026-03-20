---
name: "brainstorm"
description: "Research-backed product brainstorming for project use cases, feature expansion, and high-leverage quality-of-life improvements. Use when Codex needs to discover strong use cases for a project, identify valuable features, compare competitors, surface unmet user needs, or propose roadmap ideas that materially improve usability, retention, workflow speed, or delight. Prefer this skill for product ideation, feature brainstorming, market-informed planning, and cases where Codex should search the web deeply before answering."
---

# Brainstorm

## Overview

Use this skill to turn a vague product idea or an existing project into a concrete, research-backed set of opportunities. Ground recommendations in repo context plus deep web research before proposing use cases or features.

## Workflow

### 1. Understand the product before ideating

- Inspect the project, prompt, repo, landing page, docs, or screenshots first.
- Identify the product category, target user, existing capabilities, constraints, and likely success metric.
- Ask at most one blocking clarification question only when the answer would meaningfully change the research direction. Otherwise state the assumption and continue.

### 2. Search the web by default and search deeply

- Use web search before giving substantive recommendations unless the user explicitly forbids browsing.
- Search across multiple angles, not just one query. Cover as many of these as fit:
  - official product sites and docs
  - direct and adjacent competitors
  - review sites, testimonials, and complaint-heavy discussions
  - Reddit, Hacker News, GitHub issues, forums, Discord docs, or app store reviews when relevant
  - changelogs, public roadmaps, help centers, and feature request boards
  - case studies, integration pages, templates, marketplaces, and launch directories
- Prefer primary sources first, then triangulate with secondary commentary.
- Go past the first obvious result set when the space is crowded or the evidence is shallow.
- Pull out repeated pain points, missing workflows, surprising use cases, failed expectations, and features users praise disproportionately.

### 3. Look for exponential quality-of-life gains

- Favor ideas that remove repeated work, collapse multi-step flows, improve defaults, preserve context, automate handoffs, reduce setup, catch errors early, or make collaboration smoother.
- Look for compounding improvements across the whole workflow: discover -> decide -> act -> review -> repeat.
- Avoid generic filler ideas unless the evidence shows they matter for this product and audience.
- Treat "exponential QOL" as leverage, not novelty. The best ideas usually save time repeatedly, reduce cognitive load, or unlock adjacent value.

### 4. Synthesize opportunities into clear buckets

- Separate findings into:
  - core use cases the project should clearly support
  - adjacent or underserved use cases worth testing
  - high-leverage quality-of-life features
  - strategic bets or differentiators
- For each idea, explain why it matters, who it helps, and what evidence supports it.
- Distinguish sourced observations from your own inference.

### 5. Prioritize instead of dumping ideas

- Rank ideas by impact, effort, confidence, and differentiation when the user needs a roadmap.
- Call out quick wins separately from heavier bets.
- Recommend the smallest set of features that meaningfully changes the product trajectory.
- If useful, propose an order such as "ship now", "validate next", and "watchlist".

## Response Shape

Use a crisp structure such as:

- Product read
- Research signals
- Best use cases
- Highest-leverage features
- Recommended next moves

Include links or named sources when available. Be explicit when a recommendation is an inference from the evidence rather than directly stated by a source.

## Guardrails

- Do not answer with generic brainstorm filler if the web search has not happened yet.
- Do not rely on a single source or a single competitor snapshot when making product bets.
- Do not confuse "more features" with better leverage; prefer ideas that improve the whole experience.
- If browsing is unavailable, say that the output is provisional and based on local context only.

## Example Triggers

- "Use $brainstorm to find the strongest use cases for this project."
- "Research competitors and propose features that would make this app dramatically more useful."
- "Figure out what adjacent workflows we should support next."
- "Search the web deeply and tell me which QOL upgrades would compound the most."
