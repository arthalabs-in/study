# Hosted Demo

Study TUI is a terminal app, so the public demo runs it inside a browser terminal with WeTTY.

The Docker image launches:

```bash
study-tui /app/demo/leph101.pdf
```

That means the demo PDF is loaded as soon as the app starts. Users can still load another document from inside Study TUI with `/load`, the file picker, or the normal document tools.

## Hugging Face Spaces

Create a new Space:

- SDK: Docker
- Port: `7860`
- Repository: this repo/branch

The Dockerfile already sets:

- `STUDY_SKIP_AUTO_SETUP=1` so first-run setup does not block the demo
- `STUDY_DOCS_DIR=/app/demo` so document discovery starts in the bundled demo folder
- `STUDY_TUI_DEMO_FILE=/app/demo/leph101.pdf`

If you want live AI responses, add the provider key as a Space secret, for example `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `ANTHROPIC_API_KEY`. For a public hackathon demo, use a low-limit key and rotate it after judging.
