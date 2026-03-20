# Security Best Practices Patch Map

## Executive summary
Using the current threat model assumptions for Study TUI (single-user desktop app, localhost-only Zotero webhook, personal documents by default), the remaining security work is mostly about confidentiality and local integrity rather than internet-facing RCE. The highest-value patches are: protect sensitive local state on non-Windows, reduce exposure of the Zotero webhook secret, add an explicit privacy mode for remote providers handling personal material, and make exports safer-by-default for sensitive sessions.

## High severity

### SBP-001: Plaintext fallback for personal notes/history/context on non-Windows
Impact: personal study material, note bodies, chat history, and compacted session context can be stored in plaintext on non-Windows systems.

Evidence:
- [src/secure_storage.py:74](src/secure_storage.py#L74) returns plaintext from `encrypt_text()` on non-Windows.
- [src/secure_storage.py:86](src/secure_storage.py#L86) returns plaintext values or empty strings depending on prefix/platform behavior.
- [src/chat_history.py:138](src/chat_history.py#L138) stores encrypted-or-plaintext chat content.
- [src/chat_history.py:229](src/chat_history.py#L229) persists session-level context state.
- [src/notes.py:181](src/notes.py#L181) stores note titles and bodies using the same encryption helper.

Why this matters:
- Your validated usage model treats loaded documents as personal material.
- On Linux/macOS, compromise of the user profile, backups, or synced home directories exposes notes/history/context without OS-bound protection.

Patch map:
1. Add a cross-platform encrypted-at-rest fallback for `src/secure_storage.py`.
   - Preferred: `keyring`-backed envelope key or a locally generated AES-GCM key stored via OS keychain when available.
   - Minimal acceptable fallback: a user-scoped passphrase/key file under `~/.study-tui/` with restrictive permissions plus explicit opt-in.
2. Encrypt `settings.json` fields that are sensitive operational secrets.
   - At minimum: `zotero_webhook_secret`, any OAuth refresh tokens if they ever land there, and future sync/integration secrets.
3. Add a startup warning when secure persistence is unavailable.
   - Example: “Sensitive local data will be stored in plaintext on this platform until secure storage is configured.”

Recommended implementation targets:
- `src/secure_storage.py`
- `src/chat_history.py`
- `src/notes.py`
- `src/app.py`

### SBP-002: Remote providers receive personal document content without a strong privacy boundary
Impact: personal documents and derived study content can be sent to third-party providers during normal use.

Evidence:
- [src/app.py:549](src/app.py#L549) system prompt assumes loaded documents are available to the model and tool loop.
- [src/context_engine.py:231](src/context_engine.py#L231) assembles prompt state from model history and compact memories for provider calls.
- [src/agents/provider.py:1303](src/agents/provider.py#L1303) sends `messages=messages` into provider requests.
- [src/agents/provider.py:1393](src/agents/provider.py#L1393) builds OpenAI-compatible requests with current prompt state.

Why this matters:
- This is the top confidentiality risk in the threat model because the app is intentionally used on personal material.
- Context compaction reduces size, but not sensitivity.

Patch map:
1. Add an explicit privacy mode in `src/app.py`.
   - Modes:
     - `local_only`: block remote providers when documents are loaded.
     - `confirm_remote_docs`: require explicit one-time per-session approval before sending document-derived context to remote providers.
     - `standard`: current behavior.
2. Mark prompt-state entries by sensitivity in `src/context_engine.py`.
   - Categories like `document_chunk`, `notes`, `quiz_results`, `flashcards`, `search_results`, `library_metadata`.
   - Allow the privacy mode to exclude categories from remote provider context.
3. Add visible provider-boundary UX.
   - Show when the current prompt includes loaded-doc context and the active provider is remote.
   - Show approximate payload sensitivity in `/context`.

Recommended implementation targets:
- `src/app.py`
- `src/context_engine.py`
- `src/agents/provider.py`

## Medium severity

### SBP-003: Zotero webhook secret is stored in plaintext settings and fully printed in the UI
Impact: any same-user local process or shoulder-surfed UI capture can obtain the webhook secret and spoof webhook events.

Evidence:
- [src/app.py:87](src/app.py#L87) `SettingsManager` writes `settings.json` as plaintext JSON.
- [src/app.py:965](src/app.py#L965) generates and stores `zotero_webhook_secret` in settings.
- [src/app.py:988](src/app.py#L988) prints the full callback URL on startup.
- [src/app.py:1008](src/app.py#L1008) prints the full callback URL in status output.
- [src/zotero_webhook.py:53](src/zotero_webhook.py#L53) uses exact secret-path matching as the main authenticator.

Why this matters:
- The webhook is correctly localhost-only, so this is not an internet-grade issue.
- But the current trust model is “whoever knows the URL can post events.”

Patch map:
1. Move `zotero_webhook_secret` out of plaintext `settings.json`.
   - Store it via `secure_storage` or a separate secret store rather than general settings.
2. Stop printing the full callback URL after initial creation.
   - Show only once on enable, then mask the secret on later status screens.
   - Example: `http://127.0.0.1:23121/zotero/webhook/abcd...wxyz`
3. Add lightweight replay resistance.
   - Track recent payload hashes/timestamps in memory and drop duplicate bursts.
4. If Zotero supports custom headers, add optional secondary shared-secret header verification.

Recommended implementation targets:
- `src/app.py`
- `src/zotero_webhook.py`
- `src/secure_storage.py`

### SBP-004: Exports are saved as readable files with no sensitivity guardrails
Impact: sensitive notes, summaries, chats, PDFs, CSVs, and Anki decks are written into user-readable folders where other local software or sync tools can collect them.

Evidence:
- [src/exporter.py:16](src/exporter.py#L16) defaults exports to `~/Documents/StudyTUI-Exports`.
- [src/exporter.py:22](src/exporter.py#L22) creates export directories unconditionally.
- [src/notes.py:285](src/notes.py#L285) exports note markdown to the same default folder.
- [src/notes.py:356](src/notes.py#L356) exports note PDFs to the same default folder.
- [src/agents/agent_manager.py:404](src/agents/agent_manager.py#L404) routes `destination=documents_dir`, which may place exports next to source study material.

Why this matters:
- Exports are intentionally readable, so this is not a bug in the strict sense.
- But for personal material, the safest default is not necessarily a broadly discoverable Documents path.

Patch map:
1. Add an export privacy setting.
   - Options:
     - default readable exports (current)
     - private export directory under `~/.study-tui/exports`
     - per-export override
2. Add a sensitivity banner in approval prompts for exports from notes/chat/document-derived content.
3. Apply restrictive file permissions where the platform supports it.
   - Example: best-effort owner-only permissions on non-Windows private export mode.
4. Add `/exports` or recent-export visibility so users can find and clean up sensitive artifacts easily.

Recommended implementation targets:
- `src/exporter.py`
- `src/notes.py`
- `src/agents/agent_manager.py`
- `src/app.py`

### SBP-005: The localhost webhook accepts any correctly addressed POST without stronger provenance checks
Impact: once the secret path is known, arbitrary local software can inject believable events.

Evidence:
- [src/zotero_webhook.py:53](src/zotero_webhook.py#L53) authenticates only by exact path.
- [src/zotero_webhook.py:68](src/zotero_webhook.py#L68) accepts any JSON object as payload.
- [src/app.py:958](src/app.py#L958) treats the event as a simple trusted update and surfaces it to the user.

Why this matters:
- Same-machine attacker precondition keeps this medium, not high.
- Still worth hardening because the app now has a local network listener.

Patch map:
1. Validate a minimal schema for incoming events before surfacing them.
   - Require expected fields like event/type/source when available.
2. Add recent-event deduplication and rate limiting.
3. Optionally require `Content-Type: application/json`.
4. Consider binding the webhook lifecycle to explicit user action only, not persisted auto-start, for the most privacy-sensitive mode.

Recommended implementation targets:
- `src/zotero_webhook.py`
- `src/app.py`

## Low to medium severity

### SBP-006: Context compaction helps token cost, but still retains some sensitive library/document metadata longer than necessary
Impact: old private content may persist in model-facing history longer than required and continue crossing the provider boundary.

Evidence:
- [src/context_engine.py:124](src/context_engine.py#L124) compacts assistant outputs instead of dropping them.
- [src/context_engine.py:185](src/context_engine.py#L185) compacts tool results structurally but still retains summaries.
- [src/context_engine.py:231](src/context_engine.py#L231) assembles memory blocks and recent model history into future prompt state.
- [src/chat_history.py:229](src/chat_history.py#L229) persists session-level context state.

Why this matters:
- This is not an immediate exploit, but it increases confidentiality exposure and token spend in long sessions.

Patch map:
1. Add category-specific retention policies.
   - Drop Calibre/Zotero search metadata after the immediate answer unless pinned.
   - Keep flashcards as deck summaries only.
   - Keep quiz results as weak areas only.
2. Add a “sensitive session” compaction mode.
   - Summarize older personal-content turns more aggressively.
3. Surface category sizes in `/context` so users can see what is actually being retained.

Recommended implementation targets:
- `src/context_engine.py`
- `src/app.py`

## Suggested fix order
1. SBP-001: secure non-Windows at-rest handling
2. SBP-002: remote-provider privacy mode
3. SBP-003: Zotero webhook secret handling and URL masking
4. SBP-004: safer export defaults/options
5. SBP-005: webhook schema/rate-limit hardening
6. SBP-006: category-based context retention tightening

## Notes
- I did not treat lack of TLS as a finding because this is a local desktop/TUI app and the validated webhook model is localhost-only.
- I did not inflate the Zotero webhook to critical risk because you explicitly said it is intended to remain local and single-user.
- Existing controls that materially help already include:
  - localhost-only webhook binding
  - random secret-path webhook URL
  - approval-gated writes for notes/exports
  - rooted relative-path model file loading
  - web-fetch safety checks
  - transcript/model-history separation and compaction

