# candor-agents — effect checking for agent fleets (design exploration)

**Status: published exploration (2026-06-11) — not a product.** The question this answers
executably: *does candor's kernel transfer off programming languages?* The nearest field is agent
fleets — and the test is whether the existing spec envelope, queries, and policy grammar work on an
agent capability graph **without modifying any candor tool**.

## The mapping

candor's kernel is field-agnostic: a closed effect vocabulary × a resolvable invocation graph ×
transitive propagation × an `Unknown` honesty marker × a deterministic policy gate. Agent fleets
satisfy every requirement of the transfer test:

| candor (code) | candor-agents (fleet) |
|---|---|
| function | agent (a subagent type / the main session) |
| call edge | delegation (A can spawn B via the `Agent` tool) |
| leaf classified call (`std::fs::read`) | a tool grant (`Edit`, `WebFetch`, `Bash`, `mcp__…`) |
| the classifier (crate×path → effect) | the tool table (tool name → effect class) |
| `dyn` dispatch → CHA over impls | `Agent` tool with no named target → CHA over all defined agents |
| devirtualization (concrete receiver) | **named-delegation narrowing**: the agent's prompt names its subagents |
| unresolvable call → `Unknown` | an MCP tool with no capability declaration → `Unknown` |
| `unresolved: true` | the agent's reach includes tools nobody classified |
| entry point (`main`, `#[no_mangle]`) | the main session; cron jobs; hook-triggered agents |
| report + callgraph sidecar (spec §2/§2.2) | **identical envelope — candor-query runs unchanged** |
| policy `deny Net api` (spec §6.2) | `deny Net researcher` — the researcher must never reach the network |

## The tool table (Claude Code vocabulary, the PoC target)

Claude Code is the right first target because the fleet is **statically declared**: agent
definitions are markdown files with YAML frontmatter (`tools: Bash, Read, …`), MCP servers are
listed in `.mcp.json`, and omitted `tools:` means *inherit everything* — all machine-readable
without running anything.

| tool | effect | note |
|---|---|---|
| `Bash` | `Exec` | the subprocess boundary, same trade as Rust's `Command::new` — what the child does is beyond the static map |
| `Edit` / `Write` / `NotebookEdit` | `Fs` (write) | |
| `Read` / `Glob` / `Grep` | `Fs` (read) | |
| `WebFetch` / `WebSearch` | `Net` | |
| `SendUserFile` / `PushNotification` | `Ipc` | crosses to the user's device |
| `ScheduleWakeup` / `Cron*` | `Clock` | |
| `Agent` | (no effect — it's the edge) | delegation, not an action |
| `TodoWrite` / `Task*` / `Skill` / `ExitPlanMode` … | pure | internal state / routing |
| `mcp__<server>__<tool>` | curated table, else **`Unknown`** | an MCP server that doesn't declare its capabilities is exactly an unresolvable call — `unknownWhy: ["mcp:<server>"]` |
| `tools:` absent (inherit all) | union of everything + `Unknown` | the ambient-authority case — the report makes it visible |

The curated MCP table is deliberately small (gmail → `Net`+`Ipc`, slack → `Net`+`Ipc`,
atlassian/github → `Net`, filesystem → `Fs`, …): the same under-report-and-say-so posture as the
crate classifier. Unknown-by-default is the honesty marker doing its job — today **nothing** in the
MCP ecosystem forces a server to declare what its tools reach.

## Edges (delegation)

- Any agent granted `Agent` may spawn subagents. Which type is chosen is a *runtime string* —
  statically this is `dyn` dispatch. Resolution ladder, mirroring the code engine:
  1. **Named-delegation narrowing** (the devirt analog): if the agent's own definition text names
     specific agent types ("spawn the `researcher`…"), edge only to those.
  2. **CHA fallback**: edge to every defined agent type (sound over-approximation, documented).
- The **main session** is the entry point: it holds every tool and edges to every spawnable agent.
- Hooks and cron jobs are additional entry points (not in the PoC).

## What the PoC must demonstrate

1. `scan.py <project-dir>` → `report.<fleet>.Fleet.json` + `.callgraph.json` in the **spec §2/§2.2
   envelope**, unmodified.
2. The **existing `candor-query` binary** (from candor-rust, untouched) answers `show` / `where` /
   `callers` / `map` / `whatif` over that report.
3. A **§6.2 policy file** gates the fleet: e.g. `deny Net researcher`, `forbid Ipc in coder` — and
   `whatif researcher Net` returns the blast radius + verdict before anyone edits the fleet.
4. The `Unknown` contract: an MCP server outside the curated table marks every agent that can reach
   it `unresolved`, with `unknownWhy`.

## What does NOT transfer cleanly (honest list)

- **Bash is a capability cliff.** In code, `Exec` is one effect among ten; in an agent fleet, Bash
  is *everything* (a shell reaches the network, the filesystem, the clock…). The code engine has the
  same blind spot at the subprocess boundary, but fleets hit it constantly. An honest fleet report
  may want `Exec` to imply a documented "transitively anything" footnote — or the policy grammar
  grows a `deny Exec` idiom as the *first* rule of any confined agent. The PoC keeps the code
  engine's trade (Exec is Exec) and documents it.
- **Prompts are not code.** A tool *grant* is static; whether the agent ever *uses* it is
  behavioral. The report is a may-analysis (an upper bound), which is the right direction for a
  gate — but the marketing must say "can reach", never "does".
- **Dynamic tool surfacing** (ToolSearch-style deferred tools, runtime MCP connects) is the
  reflection analog → `Unknown`.
- **The spec says "function".** The envelope carries the mapping fine, but candor-spec 0.4 would
  want s/function/unit/ language and a `unitKind` hint before any of this is real.

## Real-fleet validation (wshobson/agents, 36.6k stars, 2026-06-11)

Scanned the most popular Claude Code agent collection (192 agents across its plugins) — 2.4s scan,
candor-query answers in ~25ms over the result. Three findings:

1. **The ecosystem default is ambient authority**: 182/192 agents declare no `tools:` line.
   The report makes the un-confined fleet instantly visible — this is the product insight.
2. **Real data corrected the model twice.** (a) `tools: []` (inline-YAML empty list) means
   *maximally confined* — pure — not a tool named `[]`. (b) The ambient set must NOT include
   `Agent`: stock Claude Code subagents cannot nest-spawn, so delegation exists only where `Agent`
   is explicitly granted (`--nested-spawn` opts harnesses that allow nesting back in). With the
   correction the marketplace graph went from a 19,968-edge all-reaches-all smear to 383 honest
   edges, and exactly one agent (`team-lead`, explicit grant, unnamed → CHA) can delegate.
3. **The Unknown contract fired on real data**: an uncurated MCP server (`mcp:meigen`) and two
   unrecognized tools (`TeamCreate`/`TeamDelete`) correctly mark their holders unresolved.

## Combined mode: fleet + code in ONE world (validated)

Both engines write the same envelope, and candor-query merges every report/sidecar under one
prefix (the normal multi-crate case) — so a project that has BOTH agent definitions AND code gets
one queryable world with no new machinery:

```sh
candor-scan .                --out .candor/report          # the code
scan.py .  --fleet myfleet   --out .candor/report \
           --link .candor/report                           # the fleet, LINKED to the code
```

`--link` is the Exec-boundary refinement, and it is exactly the cross-crate inheritance move
(CANDOR_DEPS on the JVM): an agent holding `Bash` can run the project's own binaries, and the code
report already KNOWS what those binaries do — so the agent edges to each code `entryPoint` and
inherits its recorded transitive effects. Verified end to end with the unmodified candor-query:

- `show coder` → the fleet agent carries `Clock Env` it could only have inherited from the crate's
  `main` (the code's *measured* effects, not the Bash cliff's "anything").
- `callers now_ms` → a CODE function's blast radius climbs through `main` into the FLEET
  (coder, orchestrator, triage, session).
- `whatif now_ms Exec` → exit 1: a code-level edit flags the ORCHESTRATOR's `deny Exec` rule —
  the gate verdict crosses the code/fleet boundary.

One naming rule fell out: the fleet's session root is named `session` (not `main`) so it can't
collide with a crate's `fn main` under a merged prefix. The general lesson for spec 0.4: combined
mode needs unit names to be namespace-disjoint across engines (a `unitKind` field is the fix).

## Verdict criteria

This exploration succeeds if the demo in `run.sh` produces correct, useful answers (the gate
verdict above all) with **zero changes to candor-rust/candor-spec**. It fails honestly if the
envelope or grammar needed surgery — that result goes in the report too.
