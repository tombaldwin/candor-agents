# candor-agents — does candor transfer to agent fleets? (exploration)

**Local exploration, 2026-06-11. Answer so far: yes, with zero changes to any candor tool.**

`scan.py` reads a Claude Code project's *static* fleet declarations — `.claude/agents/*.md`
(YAML frontmatter: `tools:`) and `.mcp.json` — and emits a candor-spec §2 report + §2.2 callgraph
sidecar where **units are agents, edges are delegation, and the classifier is a tool table**
(`Bash`→`Exec`, `WebFetch`→`Net`, `Edit`→`Fs(write)`, uncurated MCP server → `Unknown`).

Because the envelope conforms, the **unmodified `candor-query` binary** from candor-rust answers
`show` / `where` / `callers` / `map` / `whatif` over the fleet, and the **unmodified §6.2 policy
grammar** gates it. Run it:

```sh
bash run.sh        # needs ../candor-rust built (cargo build -p candor-query)
```

What the fixture demo shows (all real mechanics, deliberately seeded):

- **CHA for unnamed delegation**: the `coder` holds the `Agent` tool without naming its subagents →
  it edges to *every* agent and inherits the `mailer`'s `Net`/`Ipc` and the legacy `triage` agent's
  everything — so `deny Net coder` fails **today**, and the fix is exactly devirtualization:
  name the delegation.
- **The non-local consequence**: `whatif mailer Exec` → the **orchestrator**, three hops up,
  violates *its* `deny Exec` rule. candor's whole thesis, on a fleet.
- **The honesty marker**: the un-curated `internal-billing` MCP server marks every agent that can
  reach it `unresolved` with `unknownWhy: ["mcp:internal-billing"]` — nothing in the MCP ecosystem
  declares tool capabilities today, and the report says so instead of guessing.
- **Ambient authority is visible**: the legacy `triage` agent (no `tools:` line = inherits
  everything) reads `{Clock Exec Fs Ipc Net Unknown}` — the report makes the un-confined agent the
  obvious outlier.

See [DESIGN.md](DESIGN.md) for the full mapping, the tool table, and the honest non-transfers
(the Bash capability cliff; grants are may-use upper bounds; dynamic tool surfacing → `Unknown`;
spec 0.4 would want s/function/unit/).

## What this is not (yet)

Not a product, not public, not wired to hooks/cron entry points, no JVM-style runtime validation.
The next real steps, if pursued: a `unitKind` note in candor-spec; an MCP capability-declaration
convention (the `Unknown` killer); scanning a real fleet in the wild.
