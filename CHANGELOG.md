# Changelog

All notable changes to candor-agents are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and the family convention (candor-rust's CHANGELOG):
candor is pre-1.0, so minor versions may include behavioural changes — always in the
soundness-increasing direction (the §4 trust contract) — and a **⚠** marks an entry that affects
report bytes, drift results, or gate verdicts (regenerate baselines / expect verdict changes across it).

candor-agents is the family's **domain engine** (SPEC §4): its units are *agents*, not functions, and it
rides the spec ladder on its own schedule (it never holds the four code engines' floor back). Its
major.minor tracks the spec it declares — `0.15.x` declares spec `0.15`.

## [0.15.0] — 2026-07-15

### spec 0.15 — floor alignment

candor-agents now declares **spec `0.15`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it): the floor ratchets to the **coverage-envelope + host-resolution rung** — the coverage envelope is a
code-engine report field and host-resolution recall is a code-engine classifier improvement (see the
candor-spec 0.15 CHANGELOG for the spec-side detail). This is a **declared-version alignment only**,
keeping the floor consistent across the family — the fleet's reports COULD carry an uncovered
tool-surface coverage envelope in a future rung, but no such field lands here, and host resolution has no
fleet-side analogue (its leaves are tool grants, not call sites).
**No fleet-surface behaviour change**: report bytes, drift results, and gate verdicts are byte-identical to
0.14.0 — only the declared `spec` string moves `0.14`→`0.15`. A consumer pinning `spec == "0.14"` must
accept `0.15`.

No engine-local change accompanies this rung — the `v0.14.0..HEAD` range was empty; this version bump is
the entire delta.

## [0.14.0] — 2026-07-14

### spec 0.14 — floor alignment

candor-agents now declares **spec `0.14`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it): the floor ratchets to the **top-level-initializer rung** — a code-engine fix where a module's
top-level effects were dropped as false-pure (see the candor-spec 0.14 CHANGELOG for the spec-side detail).
This is a **declared-version alignment only**, keeping the floor consistent across the family — the
top-level-initializer fix is a code-engine surface (call sites / top-level code) and the domain engine has
no such surface (its leaves are tool grants, not call sites), so no new surface lands here.
**No fleet-surface behaviour change**: report bytes, drift results, and gate verdicts are byte-identical to
0.13.0 — only the declared `spec` string moves `0.13`→`0.14`. A consumer pinning `spec == "0.13"` must
accept `0.14`.

No engine-local change accompanies this rung — the `v0.13.0..HEAD` range is the version bump alone.

## [0.13.0] — 2026-07-14

### spec 0.13 — floor alignment

candor-agents now declares **spec `0.13`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it): the floor ratchets to the **`Llm`-effect rung** — the new §6.1 boundary effect for a call whose sink
is a model API (a data-exfiltration surface, always alongside `Net`, gate-able / watchable / tour-able;
see the candor-spec CHANGELOG for the spec-side detail). This is a **declared-version alignment only**,
keeping the floor consistent across the family — `Llm` is a code-engine sink classification and the domain
engine has no `Llm` surface (its leaves are tool grants, not call sites), so no new surface lands here.
**No fleet-surface behaviour change**: report bytes, drift results, and gate verdicts are byte-identical to
0.12.0 — only the declared `spec` string moves `0.12`→`0.13`. A consumer pinning `spec == "0.12"` must
accept `0.13`.

No engine-local change accompanies this rung — the `v0.12.0..HEAD` range is the version bump alone.

## [0.12.0] — 2026-07-14

### spec 0.12 — floor alignment

candor-agents now declares **spec `0.12`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it): the floor ratchets to the gains-origin rung — the §3.1 `gains` `origin` field, the supply-chain
existing/new/unknown split (see the candor-spec CHANGELOG for the spec-side detail). This is a
**declared-version alignment only**, keeping the floor consistent across the family — candor-agents does
not expose a `gains` verb, so no new surface lands here. **No fleet-surface behaviour change**: report
bytes, drift results, and gate verdicts are byte-identical to 0.11.0 — only the declared `spec` string
moves `0.11`→`0.12`. A consumer pinning `spec == "0.11"` must accept `0.12`.

### Added

- **`stats` aggregates `deepestPropagation`** from the activity log's new `maxHops` record field — the
  companion to `largestBlastRadius`. The review scripts now log the change's graph-depth as `maxHops`,
  and `stats` reports the maximum with the human line `deepest propagation seen: N hop(s) from a new
  source`. Records with an absent or non-integer `maxHops` are ignored (the established junk-tolerance
  pattern).

## [0.11.0] — 2026-07-13

### spec 0.11 — floor alignment

candor-agents now declares **spec `0.11`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it): the floor ratchets to the surprising-reach + corrupt-report-loudness rung (see the candor-spec
CHANGELOG for the spec-side detail). This is a **declared-version alignment only**, keeping the floor
consistent across the family. **No fleet-surface behaviour change**: report bytes, drift results, and gate
verdicts are byte-identical to 0.10.0 — only the declared `spec` string moves `0.10`→`0.11`. A consumer
pinning `spec == "0.10"` must accept `0.11`.

Note: this is also the **first tagged release** to carry the coverage-ledger marker rename
(`κ doesn't know` → `classifier doesn't cover`) — that change landed on main after the `v0.10.0` tag was
cut, so its entry sits under 0.10.0 below but ships here.

## [0.10.0] — 2026-07-12

### coverage-ledger marker renamed `κ doesn't know` → `classifier doesn't cover`

The per-scan item-14 coverage-ledger line now carries the **cross-engine marker `classifier doesn't
cover`** (the grep target every candor engine shares) and drops the Greek `κ` from its
**user- and agent-facing** output — the glyph was unexplained and confusing in the receipt. The line
keeps its fleet domain nouns (uncurated MCP servers / unknown tools / unlisted command heads) and its
INVISIBLE-not-a-purity-claim framing; the reviewed-pure disclosure now reads *"the classifier relies
on…"*. AGENTS.md, README.md, and the embedded `--agents` contract are re-synced to the new wording.
`κ` remains **internal maintainer vocabulary only** — the `kappa_ledger`/`collect_kappa` identifiers,
the `κ = TOOL_EFFECTS/…` classifier shorthand in docstrings, and this CHANGELOG's history are
unchanged. No report bytes / drift / gate-verdict change — receipt text only.

### spec 0.10 — floor alignment to the §3.3.1 rung

candor-agents now declares **spec `0.10`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it), following the ratcheting family floor. 0.10 pins the §3.3.1 query-grammar rung — which candor-agents
satisfies *vacuously*: it is a **fleet surface** (`scan`/`observe`/`drift`/`guard`) and exposes **no §3.1
report-query verbs**, so there is no query grammar to conform. This is a **declared-version alignment only**,
keeping the floor consistent across the family. **No fleet-surface behaviour change**: report bytes, drift
results, and gate verdicts are byte-identical to 0.9.0 — only the declared `spec` string moves `0.9`→`0.10`.
A consumer pinning `spec == "0.9"` must accept `0.10`.

## [0.9.0] — 2026-07-11

### spec 0.9 — rides the remedial-loop rung

candor-agents now declares **spec `0.9`** (`SPEC` in `scan.py`; the envelope + `--gate-json` verdict carry
it). 0.9 is a **tier-2 (pinned-tool-surface) rung** (candor-spec §"Conformance tiers") led by the code
engines — the remedial loop (`fix`/`unverified`/gate auto-disclosure) becomes the pinned §3.1/§3.3 contract
*for the code engines*; the domain engine rides the rung for floor alignment (its units are agents, not the
functions those tools operate on). **No behaviour change here**: report bytes, drift results, and gate
verdicts are byte-identical to 0.8.3 — only the declared `spec` string moves `0.8`→`0.9`. A consumer
pinning `spec == "0.8"` must accept `0.9`.

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
