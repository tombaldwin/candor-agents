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

candor-agents scan    <project-dir> [--out <prefix>]   # DECLARED: .claude/agents/*.md + .mcp.json
candor-agents observe <project-dir> [--out <prefix>]   # OBSERVED: the session transcripts
candor-agents drift   <project-dir> [--strict]         # declared vs observed (least-privilege)
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

- Units are agent types plus the `session` root (`entryPoint: true`); `hash` is `<fleet>#<agent>`.
  Every unit carries `unitKind` (spec 0.5 draft, informative): `agent`, `session`, or `hooks` —
  a fleet's units are not functions, and the field keeps a merged prefix (fleet + code reports)
  readable.
- `inferred` is the transitive effect set (delegation propagates a delegate's effects to its
  delegator); `direct` is the agent's own grants/actions.
- **Grants are MAY-use upper bounds**: a declared effect says the agent *can* reach it, never that
  it did — that is `observe`'s job.
- **The Bash cliff**: `Bash` classifies as `Exec`, but a shell can do anything — treat any
  Bash-holding agent as having the capability floor, not a tight bound. Combined mode
  (`scan --link <code-report-prefix>`) refines this: Bash-holding agents edge into the linked code
  report's entry points and inherit their *measured* effects.
- An **uncurated MCP server** or unknown tool reads `Unknown` with a named origin in `unknownWhy`
  (`mcp-uncurated:<server>`, `tool-unknown:<name>`) — never silence. A `.mcp.json` server can
  declare its effects via the `candorEffects` convention (see DECLARING.md); declared-not-verified
  trust, curated table outranks.
- A missing `tools:` line is **ambient authority** (everything + Unknown); `tools: []` is
  maximally confined. A frontmatter-less `.md` in `.claude/agents/` is not an agent and is
  skipped with disclosure.
- **Hooks are capability surface**: `.claude/settings.json` / `settings.local.json` hook commands
  run *automatically* on tool events — they appear as a `hooks` unit (Exec + the command heads as
  `cmds`), edged from the session root. A project with hooks but no agents still scans. A hook
  type the scanner doesn't know reads Unknown. User-level (`~/.claude`) hooks are out of scope:
  the report describes the project.
- `observe` is **best-effort over an internal format**: the receipt discloses unparseable
  lines/files; literal surfaces (`cmds`/`paths`/`hosts`) are the decidable subset of observed tool
  inputs — absence is never a claim of absence.

## The trust rule — do not skip this

Never conclude an agent is safe from a declared report alone (grants are upper bounds and the scan
cannot see runtime tool surfacing); never conclude a grant is unnecessary from one observed
session (drift advice strengthens with coverage). `Unknown` means *read the named origin*, not
"probably fine".
