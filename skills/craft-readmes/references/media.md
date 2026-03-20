# README Media

Use this file when working with screenshots, GIFs, relative links, or responsive images in GitHub READMEs.

## Sources

- [GitHub Docs: Basic writing and formatting syntax](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax)
- [GitHub Docs: Quickstart for writing on GitHub](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/quickstart-for-writing-on-github)
- [GitHub Docs: Attaching files](https://docs.github.com/en/enterprise-server%403.20/get-started/writing-on-github/working-with-advanced-formatting/attaching-files)

## Core rules

- Use relative paths for images that live in the repository.
- Always write meaningful alt text.
- Use `<picture>` when light and dark mode need different hero assets.
- Prefer repository-owned images over hotlinking third-party assets.
- Keep media honest and current.

## When to use each format

### Static screenshot

Use for:
- dashboards
- landing pages
- editor states
- before-and-after comparisons

Best practices:
- show a realistic, well-seeded state
- crop tightly enough to focus attention
- avoid tiny text and cluttered browser chrome unless it adds context
- caption the image with what the reader should notice

### GIF demo

Use for:
- short interactions
- terminal workflows
- drag-and-drop or animation-heavy moments
- "watch this in 5 seconds" onboarding

Best practices:
- keep one idea per GIF
- keep loops short
- avoid giant files and jittery captures
- do not replace all documentation with motion

## Layout guidance

- Prefer one hero visual above the fold and a small set of supporting visuals later.
- Use side-by-side image tables only when both images remain readable on GitHub's narrow content column.
- If many images are required, consider a collapsible section or move the full gallery to docs.

## Recommended asset paths

- `docs/readme/hero.png`
- `docs/readme/feature-1.png`
- `docs/readme/terminal-demo.gif`
- `assets/readme/hero-light.png`
- `assets/readme/hero-dark.png`

## Snippets

### Basic image

```md
![Terminal demo showing fuzzy directory jumping](docs/readme/terminal-demo.gif)
```

### Light and dark mode image

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/readme/hero-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/readme/hero-light.png">
  <img alt="App dashboard showing weekly study progress and active flashcards" src="assets/readme/hero-light.png">
</picture>
```

## Capture checklist

- Confirm the UI state matches the current product.
- Remove secrets, fake customer data, and noisy browser tabs.
- Use consistent theme, spacing, and sample content.
- Verify images still look good in both GitHub light and dark themes when relevant.
- Check that every referenced asset path resolves from the README location.
