---
name: "craft-readmes"
description: "Create and refine polished, media-rich GitHub READMEs for apps, libraries, CLIs, frameworks, and developer tools. Use when Codex needs to write or upgrade a README with strong visual hierarchy, screenshots, GIF demos, badges, quickstart flows, or launch-ready presentation. Prefer this skill when the user asks for a beautiful README, a more professional repo landing page, or a README informed by high-quality comparable projects."
---

# Craft READMEs

## Overview

Use this skill to turn a repository into a README that is clear, visually strong, and convincing without sounding fake. Balance beauty with utility: help a new visitor understand what the project is, why it matters, how to try it, and where to go next.

## References

Read [references/patterns.md](references/patterns.md) when choosing structure, pacing, and inspiration from strong public READMEs.

Read [references/media.md](references/media.md) when embedding screenshots, GIFs, badges, relative image links, or light/dark visual variants.

## Workflow

### 1. Read the repo before writing

- Inspect the codebase, docs, package metadata, scripts, screenshots, and existing README.
- Identify the project type, audience, maturity, setup path, and strongest proof points.
- Infer whether the README should behave like a product page, an installation guide, a developer quickstart, or a mix.

### 2. Research comparable READMEs when quality matters

- Browse the web by default when the user wants a standout README, when the project category is unfamiliar, or when the current README is weak.
- Find 3 to 5 high-quality READMEs from comparable projects and extract patterns instead of copying layouts verbatim.
- Prefer real repositories and official docs over generic advice posts.
- Look for above-the-fold structure, media placement, proof elements, quick links, install flow, and section ordering.

### 3. Plan the information architecture

- Design the first screen carefully: title, one-line value proposition, selective badges, a visual or demo, and a fast path to getting started.
- Decide which sections actually earn space. Common sections include demo, features, quickstart, usage, configuration, architecture, roadmap, FAQ, contributing, and license.
- Tailor the order to the project. A CLI, SDK, SaaS app, and design system should not all read the same way.

### 4. Gather or produce honest media

- Prefer real screenshots, real terminal captures, and real product states.
- Reuse media already in the repo when it is current and high quality.
- If the app can run locally, capture fresh screenshots or GIFs. If it cannot, produce a media plan instead of inventing visuals.
- Use GIFs only when motion teaches something important. Prefer static screenshots for stable UI explanation.
- Keep assets organized with predictable relative paths such as `docs/readme/` or `assets/readme/`.

### 5. Write for scanability and trust

- Lead with concrete value, not hype.
- Keep setup steps copy-pasteable.
- Use headings, bullets, tables, and callouts only when they improve speed of understanding.
- Use badges selectively. A few meaningful badges outperform a badge graveyard.
- Add links to docs, demo, package registry, website, or examples where they reduce friction.

### 6. Polish the finish

- Tighten the opening paragraph until a first-time visitor can explain the project in one sentence.
- Check spacing, heading rhythm, image captions, alt text, and link destinations.
- Remove repeated claims, generic adjectives, and sections that say little.
- Make sure the README still works as plain text when images fail to load.

## Output Modes

### New README

Create a full README from repo context and any available media.

### README Refresh

Keep the existing structure when it is serviceable, but improve hierarchy, visuals, clarity, and onboarding.

### Media Plan

If real screenshots or demos are missing, return an asset capture plan with exact shots, captions, and placement.

### Hero Refresh

If the user only wants the top of the README improved, rewrite the above-the-fold section first.

## Guardrails

- Do not invent screenshots, metrics, testimonials, integrations, or logos.
- Do not overload the page with centered text, giant emoji walls, or decorative badges.
- Do not copy another repository's README structure too closely.
- Do not add animations or GIFs unless they improve comprehension.
- If browsing is unavailable, say that the inspiration set is limited and proceed from repo context plus the bundled references.

## Example Triggers

- "Use $craft-readmes to make this repo README look world-class."
- "Rewrite this README with screenshots, GIFs, and a better first impression."
- "Study a few professional READMEs and then redesign ours."
- "Turn this project into a polished open-source landing page on GitHub."
