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
  `Rand`, `Clipboard`, `Llm`. (`Llm` has been a §1 effect since spec ⟨0.13⟩ but was missing from this
  engine's copy of the table until ⟨0.24⟩, so a server declaring it was voided as a typo — read §1,
  don't count this list.) Declaring `Llm` also reports `Net`: a model-provider call is an outbound
  request in every instance, so §6.1 has the two co-emitted. `Db` is *not* co-emitted with `Net` —
  an embedded, file-backed store has no egress.
- `"candorEffects": []` declares a **pure** server (it computes; it reaches nothing).
- An unknown effect name **voids the declaration** — the server reads `Unknown` with
  `unknownWhy: ["mcp-decl-invalid:<server>:<name>"]`, *in addition to* anything the curated table
  already knows about that name. A typo (`"net"`) must never silently narrow the reported surface;
  that is the §4 trust contract's forbidden direction.
- Unrecognized keys are ignored by every MCP client we know of, so the annotation is inert outside
  candor.

**Trust semantics — declared, not verified.** This is the project owner's claim, exactly like the
code engines' project-supplied classifier rules (`classify_extra`) and cross-report trust
(`CANDOR_DEPS`): candor propagates it faithfully and the report is only as true as the declaration.

**When both exist, the two tiers are UNIONED, not ranked.** Each is a *lower bound* on the server's
surface — candor's curated claim about a conventionally-named server, and the project's claim about
the server it actually runs — and the sound combination of two lower bounds is their union. So a
declaration can only ever **add**:

- it cannot subtract candor's claim: `github` declaring `["Fs"]` still reports (and is still gated
  on) `Net`, and `"candorEffects": []` on a curated server is inert. A project-controlled file must
  not be able to narrow the reported surface — the paragraph above is the whole reason.
- it *can* add what candor's table does not know: `filesystem` declaring `["Fs","Net"]` reports both.

This corrects a "curated outranks a declaration" rule that was stated as a total order and was
under-protective in the widening direction. Measured 2026-08-30: two `.mcp.json` files differing only
in the server's *name*, both declaring `["Fs","Net"]`, under one `deny Net worker` — the uncurated
name exited 1 and the **curated one exited 0 with `policy ✓`**, disclosed nowhere. Naming a server
after a curated one silenced the project's own declaration and turned a red gate green, which is
exactly the direction the voiding rule above exists to forbid.

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
