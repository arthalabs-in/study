# Launch Demo Flow

Use this as the single public demo path for README, terminal recording, or launch posts.

## Goal

Show the magic of Study TUI in under a minute:
- it finds the right document from a real library
- it turns that document into structured study actions
- it remembers progress instead of acting like disposable chat

## Recommended setup

- provider already configured
- document directory already set
- start with one clean, legible textbook or paper chapter
- prefer a source with obvious chapter structure and factual material

## Demo script

Use a short terminal session with no setup footage.

```text
load chapter 1 physics
what are the main ideas here?
quiz me on the weak points
make flashcards from the misses
save a note with the weak areas
how am i doing on this chapter?
```

## What the audience should see

1. Natural-language loading
- The app resolves a fuzzy request like `load chapter 1 physics`
- If ambiguous, it asks a useful clarification instead of silently guessing

2. Grounded answer
- Ask one short comprehension question
- The response should clearly read as document-grounded, not generic tutoring

3. Quiz handoff
- `quiz me on the weak points`
- Show the interactive quiz UI, not raw JSON or solved output

4. Flashcard handoff
- `make flashcards from the misses`
- Show the flashcard UI directly

5. Progress continuity
- Ask `how am i doing on this chapter?`
- Show document-linked progress instead of a generic answer

## Recording notes

- Keep the session under 60 seconds
- Use one provider/model only
- Avoid long waits, setup screens, or theme switching
- Do not feature-dump every command
- Prefer one strong chapter over many documents
- If export is included, show one quick note export or Anki/PDF path and move on

## If the demo drifts

Reset and rerun. The launch demo should only use the strongest repeatable happy path:
- load
- ask
- quiz
- flashcards
- progress
