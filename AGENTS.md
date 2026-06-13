# Using candor-agents (instructions for an AI coding agent)

You are working with a Claude Code **agent fleet**. **candor-agents** is a candor engine over the
fleet itself: agents are the units, delegation is the call graph, tool grants are the classified
leaves. It answers what the fleet MAY do, what it actually DID, and the drift between the two. The
language-agnostic consumption contract is
[candor-spec/AGENTS.md](https://github.com/tombaldwin/candor-spec/blob/main/AGENTS.md); this file
is the fleet-specific surface.

> **This document ships inside the package.** `candor-agents --agents` prints the contract for the
> *installed* version — always prefer that over a vendored or fetched copy, which can describe a
> different candor-agents than the one you are running.

## Produce a report

```sh
pipx install git+https://github.com/tombaldwin/candor-agents   # recommended — isolated (plain pip
                                                               # works too, but only into a venv)

candor-agents scan    <project-dir> [--out <prefix>]   # DECLARED: agents, .mcp.json, settings,
                                                       #           slash-commands, skills, cron
candor-agents observe <project-dir> [--out <prefix>]   # OBSERVED: the session transcripts
candor-agents drift   <project-dir> [--strict]         # declared vs observed (least-privilege)
candor-agents guard   <policy-file> [<project-dir>]    # ENFORCED: deny-policy -> permissions.deny
```

`scan` reads the fleet's declarations and writes `<prefix>.<fleet>.Fleet.json` (+ a callgraph
sidecar) — a candor-spec §2 envelope, so the **unmodified `candor-query` binary** answers
`show`/`where`/`callers`/`map`/`whatif` over it, and a §6.2 policy file gates it. `observe` reads
the project's Claude Code transcripts (`~/.claude/projects/<slug>/`) and writes
`<prefix>.<fleet>.Observed.json` in the same shape: units aggregate by agent TYPE, delegation
edges come from subagent metadata, effects classify from observed tool_use events. `drift`
compares the two DIRECT sets per agent: granted-but-unused → a least-privilege trim candidate;
observed-outside-declaration → an anomaly to read (`--strict` exits 1 on it).

## How to read the report

- Units are agent types, `command:`/`skill:`/`cron:` units, and the `session` root; the session and
  each scheduled task are `entryPoint: true` (the autonomous roots). `hash` is `<fleet>#<unit>`.
  Every unit carries `unitKind` (spec 0.5 draft, informative): `agent`, `command`, `skill`, `cron`,
  `session`, or `hooks` — a fleet's units are not functions, and the field keeps a merged prefix
  (fleet + code reports) readable.
- `inferred` is the transitive effect set (delegation propagates a delegate's effects to its
  delegator); `direct` is the agent's own grants/actions.
- **Grants are MAY-use upper bounds**: a declared effect says the agent *can* reach it, never that
  it did — that is `observe`'s job.
- **The Bash cliff**: `Bash` classifies as `Exec`, but a shell can do anything — treat any
  Bash-holding agent as having the capability floor, not a tight bound. Two refinements (spec §4
  ⟨0.5⟩): (1) when a **literal sub-command head** is known — a `Bash(curl:*)` specifier or a `!`curl``
  line — its effect is added (`curl`→`Net`, `candor*`→`Fs`/`Env`, the latter guaranteed by the
  analyzer self-boundary); `Exec` stays (a subprocess still spawned) and an unknown head keeps the
  bare cliff. (2) Combined mode (`scan --link <code-report-prefix>`) edges Bash-holding agents into
  the linked code report's entry points to inherit their *measured* effects — but a command whose
  heads are all known external tools is exempt (running candor *over* the code reads `Fs`, it doesn't
  perform the code's `Net`/`Db`).
- An **uncurated MCP server** or unknown tool reads `Unknown` with a named origin in `unknownWhy`
  (`mcp-uncurated:<server>`, `tool-unknown:<name>`) — never silence. A `.mcp.json` server can
  declare its effects via the `candorEffects` convention (see DECLARING.md); declared-not-verified
  trust, curated table outranks.
- A missing `tools:` line is **ambient authority** (everything + Unknown); `tools: []` is
  maximally confined. A frontmatter-less `.md` in `.claude/agents/` is not an agent and is
  skipped with disclosure.
- **`permissions.deny` is subtracted** (settings.json): a wholly-denied tool or MCP server
  (`"WebFetch"`, `"mcp__github"`) is hard-enforced by the harness, so its effect is *removed* from
  every unit — the one place the may-analysis tightens on sound data. A **scoped** deny
  (`Bash(curl:*)`, one mcp tool, a path glob) removes only a subset of a tool's uses, so it is
  disclosed on the receipt but **not** subtracted (the tool stays usable; Exec survives the cliff).
  `allow`/`ask` don't expand capability and are ignored. Hooks bypass permissions, so a `deny Bash`
  never strips the `hooks` unit's Exec.
- **Scope: the PROJECT `.claude/` only.** The surface is read from the project's
  `.claude/settings.json` + `.claude/settings.local.json`, `.claude/agents|commands|skills`, and
  `.mcp.json` — *not* the analyzing user's global `~/.claude/settings.json` (nor enterprise managed
  settings), which vary per machine and would make the report non-reproducible. So a hook or
  `permissions.deny` declared **user-globally** is out of scope: a global `PostToolUse` hook that runs
  `Exec` won't appear in the `hooks` unit (a blind spot in the under-report direction — the report is
  the fleet *as committed*, not as it runs on one machine). If you need the effective runtime surface,
  scan a tree with the global settings merged into its `.claude/`.
- **`guard` is the dual — may → ENFORCED** (spec §6.2 ⟨0.5⟩): `candor-agents guard <policy>` compiles
  a `deny <Effect>` rule into the `permissions.deny` the harness enforces *natively*, so the boundary
  holds at runtime, not just in CI. scan READS `permissions.deny` to subtract; guard WRITES it. It is
  honest about the §4 cliff — denying the directly-Net tools doesn't bind a `Bash` that can curl, so
  it warns and tells you to add `deny Exec`; a per-agent scope a project-wide `permissions.deny` can't
  express is reported as unenforceable (tighten that agent's grants instead).
- **Slash commands and skills are units**: `.claude/commands/**/*.md` (`command:<name>`) and
  `.claude/skills/*/SKILL.md` (`skill:<name>`) carry their own `allowed-tools` (effects classify
  from it; `Bash(git:*)` is still Exec) and a command's `!`-shell adds Exec + its command heads.
  The session root invokes them. Unlike an agent, an **absent `allowed-tools` is pure** (a
  prompt-only command), not ambient.
- **Hooks are capability surface**: `.claude/settings.json` / `settings.local.json` hook commands
  run *automatically* — they appear as a `hooks` unit (Exec + the command heads as `cmds`). A
  **tool-event** hook (`PreToolUse`/`PostToolUse`) fires on the matching tool use of any agent, so
  each agent whose granted tools match the hook's `matcher` **edges to `hooks`** and inherits its
  Exec — `forbid reviewer -> Exec` catches a hook that execs on the reviewer's edits. Lifecycle
  hooks (`Stop`/`SessionStart`/…) edge from the session root only. A project with hooks but no
  agents still scans. A hook type the scanner doesn't know reads Unknown. (User-global hooks are out
  of scope — see the scope note above.)
- **Scheduled tasks are autonomous entry points**: a *durable* cron job (`CronCreate durable:true`)
  persists to `.claude/scheduled_tasks.json` and fires on its own schedule with no human or caller.
  Each is a `cron:<id>` unit (`entryPoint: true`, `loc` carries the cron expression) that drives a
  full session, so it inherits the whole fleet's reach — `deny Net cron:<id>` gates what can fire
  autonomously. Non-durable cron is in-memory only (never on disk) and is correctly invisible: the
  report describes what is declared to persist.
- `observe` is **best-effort over an internal format**: the receipt discloses unparseable
  lines/files; literal surfaces (`cmds`/`paths`/`hosts`) are the decidable subset of observed tool
  inputs — absence is never a claim of absence.

## The trust rule — do not skip this

Never conclude an agent is safe from a declared report alone (grants are upper bounds and the scan
cannot see runtime tool surfacing); never conclude a grant is unnecessary from one observed
session (drift advice strengthens with coverage). `Unknown` means *read the named origin*, not
"probably fine".
