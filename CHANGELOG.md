# Changelog

All notable changes to candor-agents are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and the family convention (candor-rust's CHANGELOG):
candor is pre-1.0, so minor versions may include behavioural changes — always in the
soundness-increasing direction (the §4 trust contract) — and a **⚠** marks an entry that affects
report bytes, drift results, or gate verdicts (regenerate baselines / expect verdict changes across it).

candor-agents is the family's **domain engine** (SPEC §4): its units are *agents*, not functions, and it
rides the spec ladder on its own schedule (it never holds the four code engines' floor back). Its
major.minor tracks the spec it declares — `0.8.x` declares spec `0.8`.

## [0.8.3] — 2026-07-10

### `digest` dogfood fixes (report wording — no data/verdict change)

Found running the owner `digest` over real engine output (candor-java's own bytecode) for the first time:

- **"Held the line" now itemizes every caught change.** The header counted all blocked changes but listed
  only the `AS-EFF` policy codes, so a change blocked for *introducing a new effect* (the edit-loop's
  non-policy block — no code) was counted yet unexplained. A code-less block now reads
  "*introduced a new effect (Net) not in the baseline — blocked for review*", and the header reconciles.
  Not double-counted as "allowed through" — it was blocked, not clean.
- **The time line reports only timed checks.** "candor's own time … across N checks" used the total turn
  count, but a jar/CI record carries no `reviewMs` — implying timings never taken. Now "across N timed
  checks", counting only measured runs.

## [0.8.2] — 2026-07-10

### `digest` + `log-gate` — the owner-facing protection report and its CI feeder

- **`candor-agents digest [<dir>]`** renders an aggregate, plain-language `CANDOR-REPORT.md` over the
  activity log the edit-time gate already writes: the catches held before merge, the capabilities allowed
  through, the coverage it could not fully resolve (disclosed, never passed off as clean), and the quiet
  majority that crossed no boundary. Aggregate counts only — never file paths. Single-sourced on the same
  `stats._load`/`_summary` the `stats` command reads.
- **`candor-agents log-gate <gate.json> [<report.json>]`** turns a jar `--gate-json` CI run into one
  activity record in the same shape (path-free by construction — a CI gate has no transcript), so the
  pure-jar PR gate feeds the digest too. A parity test pins its record keys to the review-path writer's.

## [0.8.1] — 2026-07-09

- Fail-closed fixes on the enforcement surface: `guard` no longer fails open on a bad policy path; the
  positional-argument parse no longer silently swallows a misplaced flag.
- `scan.py` split out of the monolith; the §6.2 policy-matcher pins tightened.

## [0.8.0] — 2026-07-09

### The 0.8 domain-engine surface — config, gate verdict, κ ledger

Rides the ladder behind the four code engines' 0.8 floor (SPEC §8):

- **`.candor/config`** (SPEC §3.4) — the checked-in alternative to the `CANDOR_*` env wiring, shared key
  vocabulary with the code engines.
- **`--gate-json`** (SPEC §3.3) — the structured gate verdict `{ spec, ok, violations }`, the machine
  analog of the console diagnostics from the same check that sets the exit code.
- The **item-14 κ ledger** and the MCP name-trust bound: an MCP server named but uncurated resolves to a
  disclosed `Unknown`, never a silently-trusted effect set. Origin vocabulary aligned with the spec.

## [0.7.0] — 2026-06-19

candor-agents joins the unified version line as the sixth engine, declaring spec `0.7` — the canonical
`unknownWhy` vocabulary (`reflect:`/`native:`/`dispatch:`/`callback:`) and the required command-line
surface, applied to the agent-fleet domain (scan / observe / drift / guard).

## [0.4.1 – 0.4.11] — 2026-06-12 … 06-13

The pre-unification 0.4 series — the agent-fleet analyzer maturing against real public fleets:

- **⚠ drift's first real-fleet run** surfaced a fabrication class (a prompt mention trusted as proof of
  the spawn set) — narrowing made sound (allowlist → disclosed `Unknown` for a bare `Agent`), gated.
- Hooks recognized as a capability surface (the recorded blind spot, closed); `permissions.deny` honoured;
  slash-commands, skills, and scheduled tasks (cron units) added as autonomous entry points.
- The curated tables grew (MCP servers + command heads); the `Exec` cliff refined by known sub-command
  head; `unknownWhy` vocabulary unified between `scan` and `observe`. Docs recommend `pipx` (isolated)
  over bare `pip` — top-level module names can clash in a shared environment.

Older tags and the per-commit detail: see the
[GitHub releases](https://github.com/tombaldwin/candor-agents/releases).
