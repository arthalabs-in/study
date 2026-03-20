# README Patterns

Use this file when choosing structure, pacing, and visual direction for a README.

## Sources

- [Awesome README](https://github.com/matiassingers/awesome-readme)
- [zoxide](https://github.com/ajeetdsouza/zoxide)
- [shadcn/ui](https://github.com/shadcn-ui/ui)
- [GitHub Docs: Basic writing and formatting syntax](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax)
- [GitHub Docs: Quickstart for writing on GitHub](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/quickstart-for-writing-on-github)

## What strong READMEs consistently do

- Establish the product in one sentence immediately.
- Put proof near the top: demo visual, docs link, install command, stars, package badge, or live site.
- Make the first interaction obvious with a quickstart or "start here" path.
- Use visuals to teach, not decorate.
- Give visitors escape hatches to docs, examples, contribution guides, and license details.

## Patterns worth borrowing

### Above the fold: zoxide

`zoxide` works because the top of the README does four jobs fast: it states the promise, shows a small badge cluster, gives a short supporting explanation, and exposes quick navigation links before the long-form content begins. Borrow this pattern for CLIs, SDKs, and tools where installation and first use matter more than storytelling.

Use when:
- the project is a tool, package, or command-line utility
- the visitor mostly wants install and first-run speed

### Hero-first minimalism: shadcn/ui

`shadcn/ui` stays restrained: strong title, short value statement, a single hero visual, and a clean path to docs. Borrow this when the project already has strong brand or product visuals and does not need a wall of copy.

Use when:
- the product has polished visuals already
- the user journey should flow from visual impact to docs or demo

### Curated pattern bank: Awesome README

`Awesome README` is useful as a pattern catalog. The strongest recurring ingredients it highlights are banners, screenshots, GIF demos, compact tables of contents, quick links, contributor proof, and polished install instructions. Use it as a menu of options, not a checklist to blindly apply.

Use when:
- the project is missing presentation ideas
- the repo needs inspiration for layout or media use

## Project-type guidance

### CLI or library

- Lead with a one-line promise and install command.
- Show one concise terminal demo or code example near the top.
- Keep badges small and meaningful.
- Push detailed API or configuration content lower.

### App, SaaS, or dashboard

- Lead with a visual that makes the interface legible.
- Pair screenshots with captions that explain user value.
- Add a fast feature summary and a clear demo or deploy path.

### Framework, component library, or design system

- Lead with brand clarity and a high-quality hero image.
- Route quickly to docs.
- Keep the README focused on orientation, not exhaustive reference material.

## Anti-patterns

- Too many badges before the visitor understands the project.
- Huge paragraphs above the fold.
- Decorative GIFs that teach nothing.
- Generic feature lists without proof, screenshots, or examples.
- README sections copied from another repo regardless of project type.
