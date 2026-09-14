---
name: agents
description: Always-loaded project anchor. Read this first. Contains project identity, non-negotiables, commands, and pointer to ROUTER.md for full context.
last_updated: 2026-09-14
---

# cqfi (cheap quant fixed income)

## What This Is
Interactive agent for QuantLib-based fixed-income analytics on government bonds and exchange-traded futures, queryable through natural-language LLM prompts or rule-based SQL commands, exposed via both CLI REPL and PySide6 GUI.

## Non-Negotiables
- Never write to the source DuckDB files (`ycs_data`) — all data access is read-only. Analytics and cache outputs go only to `quant_cache_db` or `bond_analytics_db`.
- No look-ahead bias: features and model inputs may only use data available at time t. Train/validation/test splits are always chronological, never shuffled.
- Never commit datasets, model checkpoints, secrets, or API keys.
- All Python runs through `.venv` via `uv run`, never global or conda Python.

## Commands
- CLI REPL: `uv run cqfi` (rule-based) or `uv run cqfi --llm` (LLM-powered)
- GUI: `uv run cqfi-gui`
- Tests: `uv run pytest` or `uv run pytest tests/test_*.py -v`
- Setup: `uv sync` then `cp .env.example .env` (LLM mode only)
- Config: `config/cqfi.yaml` (paths, datasets, mcp-data registrations)

## Code Graph
Use the smallest relevant structured resolver. For Inbox or Relay mutations, resolve only the intended action with `mex inbox contract --action <command-id> --json` or `mex relay contract --action <command-id> --json`; use `mex capabilities --json` only for broader capability discovery. If the user explicitly asks to create, save, or draft a checkout-local Inbox or Relay draft, preview and apply that exact draft without asking for redundant confirmation. Deleting a local draft, or publishing, approving, rejecting, withdrawing, marking stale, repairing, taking or acknowledging, or closing, requires fresh explicit confirmation after semantic preview. Treat Git commit, push, and pull as separate actions requiring their own authorization.

The repo is indexed into `.mex/graph.db`. Use it to avoid re-reading code you already have — it is one tool alongside Grep/Glob, not a replacement for them.
- If you know the symbol name, go straight to it: `mex graph query <who-calls|what-calls|where-defined> <symbol>` and `mex graph get <id>` are exact and cheap. This is the strongest part of the graph. Give it exact names — an approximate name can return a confident wrong match.
- Exploring an unfamiliar task? `mex graph scope "<task>"` returns bounded, source-backed JSONL context plus trustworthy execution flows. Scope matches on words, not meaning, so treat it as starting evidence rather than a complete answer.
- Treat source returned by the graph as ALREADY READ; do not re-open those files.
- Read the summary status and evidence. `status: "ok"` remains usable when `truncated: true`; only optional evidence was omitted. For `partial` or `degraded`, narrow the task or follow `suggestedNextCommands`.
- Use `mex graph get <id> --detail source` only when source is missing, you need exact expansion, or a partial/degraded summary suggests it. Do not expand nodes by quota.
- If the evidence is insufficient or the task wording does not match the code, use Grep/Glob instead. Do not re-run `scope` with reworded phrasing more than once.
- Before editing a symbol, run `mex impact <symbol|file>` to see affected callers and scaffold memory.
- During `mex sync`, adjudicate any AMBIGUOUS grounding; after repairs, ensure the refreshed grounding is re-emitted.

## Scaffold Growth
After meaningful work, run GROW:
- Ground: what changed in reality?
- Record: update `ROUTER.md` and relevant `context/` files
- Orient: create or update a `patterns/` runbook if this can recur
- Write: bump `last_updated` on changed scaffold files; optional `mex log` notes follow the logging policy below

## Agent Logging
Read `mex logging --json` at session start and before optional logging. This checkout-local advisory preference is `significant` (quiet default: material decisions, risks, blockers, or durable discoveries), `checkpoints` (batch useful notes at task/session boundaries), or `manual` (no unsolicited notes). Skip routine tool calls, edits, repeated status, and empty summaries. Honor explicit user log requests in every mode; never suppress mandatory workflow Activity or recovery audit records. Report a policy read failure instead of guessing or changing the preference.

When earlier work may inform the task, use `mex timeline --query "subject phrase" --file src/example.ts --limit 10 --json` with a known subject or exact recorded file path, or both. These are historical notes, not accepted current knowledge. Verify conclusions before reuse or explicit promotion with their source retained.

The scaffold grows from real work, not just setup. See the GROW step in `ROUTER.md` for details.

## Navigation
At the start of every session, read `ROUTER.md` before doing anything else.
For full project context, patterns, and task guidance — everything is there.

<!-- mex-agent:skills:start -->
## MEX context policy
- When MEX context materially helps your work, mention MEX and the relevant finding naturally in your explanation. Tie the mention to what it helped you understand, decide, or verify. Avoid fixed phrases, standalone acknowledgements, repeated mentions, or narrating routine context loading. This replaces older MEX instructions requiring a fixed acknowledgement or context-loading narration.
- Do not claim an author, date, or historical event unless the retrieved data actually provides it.
- After a MEX write, say exactly what changed and its sharing boundary: a local draft is checkout-only and nothing is shared; a canonical artifact is written to the working tree and requires commit/push to share.
- Skill activation is not approval for canonical actions.
<!-- mex-agent:skills:end -->
