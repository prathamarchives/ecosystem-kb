# ecosystem-kb — agent usage instructions

Place this in your client's global/project rules (the `kb connect` command
prints where). It tells the agent when to retrieve and when to capture.

---

Before substantial work on any task:

1. Call `kb_context` with the task description and, when known, the project
   and repository. Treat source-backed CURRENT decisions and requirements it
   returns as constraints, not suggestions.
2. If KB context conflicts with the current repository state or a newer
   verified source, surface the conflict in your answer; do not blindly
   follow stale memory.
3. Capture durable outcomes through `kb_capture`: decisions (with status),
   important corrections, resolved problems, findings worth keeping.
4. Do NOT capture transient chatter, secrets, speculative claims presented
   as facts, or entire transcripts.
5. When you captured something durable, include the KB receipt id in your
   final answer.

Retrieved items carry epistemic status (proposed/accepted/superseded/
disputed) — respect it when reasoning about what is established. `kb_capture`
records knowledge as unreviewed agent-reported content; it never makes
something an approved fact by itself.
