# Jarvis Memory V3

Jarvis uses a **local SQLite database** at `config.data_dir()/memory.sqlite3` (overrideable by constructing `MemoryStore(path)`). SQLite is the single source of truth; connections are short-lived per operation, use WAL mode, a busy timeout and parameterized SQL.

## What is stored

- **Facts** are explicit notes/preferences with an ID, category, source, owner and installation scope, timestamps and deterministic local features.
- **Episodes** preserve bounded user and assistant turn text per conversation across restarts. Individual inputs are capped at 8,000/16,000 characters, but historic rows are not silently truncated by a prompt budget.
- **Events** are an audit trail containing operation type, ID, category, scopes and safe metadata. Events intentionally do not contain full note or conversation text.

Retrieval combines SQLite FTS5 OR matching (up to 200 candidates), deterministic hashed word features and a small recency term. Rare lexical matches retain priority even when a natural-language query also contains common words. These features are **not neural embeddings** and no external model or account is contacted by the memory store. Search limits are clamped to 50 and FTS terms are quoted and parameterized.

Every fact, episode and event is filtered by both `owner_scope` and `installation_scope`. The SQLite database may be shared by a native UI process and a bridge process, but one scope cannot read or forget another scope's rows. A revision counter is incremented in the same transaction as each write event; searches check it on every call so per-instance caches notice writes made by another process.

## Safety

Secrets, credentials, passwords, API keys, OTP/PIN/TAN values and payment-card-like numbers are rejected for direct notes. Conversation episodes are redacted before insertion. Tool credentials and tokens are never written by memory tools. `forget(id)` removes the fact, FTS row, cache entries and retrieval vector; the deletion event retains only safe metadata.

The old Claude filesystem memory tool remains available for compatibility and its files are not deleted. Safe legacy files are imported once as `legacy:*` facts, with traversal constrained to the resolved memory root, a maximum file size of 8,000 bytes and at most 1,000 files; credential-bearing files are skipped. The migration marker is stored in the target database, not as a global filesystem marker, so independent databases do not suppress one another's imports. Native tools are `wissen_speichern`, `wissen_suchen` and `wissen_vergessen`.

## Prompt integration

Each Agent has a conversation-specific memory store. It retrieves a bounded context on every request and marks it as **untrusted reference material**, never as instructions. The first prompt can use the stable persona/profile baseline plus this dynamic context. Last meaningful episodes are restored as prompt context (up to 12 pairs), while the full episode history remains queryable in SQLite. Completed and interrupted turns are recorded before an Agent request closes.

Memory is additive and local; it does not weaken the existing approval pipeline for external actions, and failure to write memory cannot make a valid assistant response fail. There is no automatic age-based retention or pruning: history remains available until `forget(id)` (or an explicit application-level cleanup) removes it.
