# Declaring MCP server capabilities — the `Unknown` killer

**The problem, measured:** nothing in the MCP ecosystem declares what a server's tools can reach.
To an effect checker, every uncurated MCP server is an unresolvable call — honest `Unknown`, never
silently pure — and on real fleets that's the dominant noise source: scanning the most popular
Claude Code agent collection (192 agents), every agent that could reach an uncurated server carried
`Unknown` for it. The honesty is correct; the *information gap* is the ecosystem's.

## Tier 1 — the project-side declaration (works today)

Annotate the server's entry in your project's `.mcp.json` with a `candorEffects` array:

```json
{ "mcpServers": {
    "billing":  { "command": "node", "args": ["./billing-mcp.js"],
                  "candorEffects": ["Net", "Db"] },
    "notes":    { "command": "npx",  "args": ["notes-mcp"],
                  "candorEffects": [] }
} }
```

- The vocabulary is candor-spec §1: `Net`, `Fs`, `Db`, `Exec`, `Env`, `Clock`, `Ipc`, `Log`,
  `Rand`, `Clipboard`.
- `"candorEffects": []` declares a **pure** server (it computes; it reaches nothing).
- An unknown effect name **voids the declaration** — the server stays `Unknown` with
  `unknownWhy: ["mcp-decl-invalid:<server>:<name>"]`. A typo (`"net"`) must never silently narrow
  the reported surface; that is the §4 trust contract's forbidden direction.
- Unrecognized keys are ignored by every MCP client we know of, so the annotation is inert outside
  candor.

**Trust semantics — declared, not verified.** This is the project owner's claim, exactly like the
code engines' project-supplied classifier rules (`classify_extra`) and cross-report trust
(`CANDOR_DEPS`): candor propagates it faithfully and the report is only as true as the declaration.
candor's own curated table (gmail, slack, github, …) outranks a declaration when both exist —
candor's claim is the one it can stand behind.

## Tier 2 — the server-published declaration (the proposal)

The right home for this fact is the **server itself**, not every consuming project. MCP servers
already return metadata at `initialize`; an `effects` field there (same vocabulary, same
empty-means-pure rule) would let any effect-aware client classify every connected server with no
curation and no per-project annotation — and would let registries display a capability badge the
way package registries display licenses.

Until something like that exists in the MCP spec, Tier 1 is the bridge: server authors can ship a
recommended `candorEffects` line in their README's install snippet, which puts the declaration in
the right hands (the author's) even though it travels in the project's config.

## What this kills, and what it doesn't

A declared fleet can be **fully resolved**: every `Unknown` left in the report is then a real
finding (an undeclared server, a typo'd declaration, an unrecognized tool, an ambient agent) rather
than ecosystem noise. It does **not** verify behavior — a server that lies (or drifts) defeats it,
the same way a wrong `classify_extra` rule defeats the code engines. Runtime verification (the
strace-oracle analog for MCP traffic) is the natural follow-up and is out of scope here.
