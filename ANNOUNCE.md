# Announcement draft — the fleet finding (for distribution)

Honest copy for the candor-agents follow-up announcement. Same rules as the main repo's
ANNOUNCE.md: no hype, lead with the concrete thing, every number verifiable by running `scan.py`
on the public repo it names. The finding is about **visibility, not blame** — keep that framing or
don't post it.

## Title options

- `We scanned the most popular Claude Code agent collection: 182 of 192 agents run with unrestricted tools`
- `Show HN: candor-agents – your agent fleet is an effect graph (and it's probably unconfined)`
- `What can your subagents actually reach? We made the question answerable`

## One-liner

> We pointed an effect checker at a 36k-star Claude Code agent collection: 182 of 192 agents
> declare no `tools:` restriction — every one of them holds the full toolset (shell, filesystem,
> network) whether its job needs it or not. The tooling to *see* this didn't exist, so we built it:
> https://github.com/tombaldwin/candor-agents

## Body

**The question:** an agent fleet is a delegation graph — orchestrators spawn subagents, subagents
hold tools, tools reach the world. *What can each agent transitively reach?* Nobody's config answers
that today; you'd have to trace it by hand, the exact problem effect checkers solve for code.

**The method:** [candor](https://candor.poly.io) models code as units + call edges + classified
effect leaves, with an `Unknown` for anything unresolvable and a policy gate over the result. A
fleet maps onto that kernel directly: agents are units, delegation is edges (an unnamed `Agent`
grant is `dyn` dispatch — resolved by enumerating every agent, exactly like CHA), tool grants are
the classified leaves (`Bash`→`Exec`, `WebFetch`→`Net`, an MCP server nobody classified →
`Unknown`). The same query and policy tools that gate our Rust/JVM code run **unmodified** over the
fleet report.

**The finding, on the most popular public collection (192 agents):**

- **182 of 192 declare no `tools:` line** — Claude Code's documented default then grants
  *everything*: shell, filesystem write, network, scheduling. A code-review agent that only needs
  `Read`/`Grep` holds `Bash`. This isn't a vulnerability and it isn't sloppiness by any one author —
  it's the ecosystem default, and until now there was no tool that made the aggregate visible.
- **Exactly one agent in the collection can delegate** (an explicit `Agent` grant, unnamed → it can
  spawn anything, so it transitively holds the union of the whole marketplace).
- **Every uncurated MCP server reads `Unknown`** — nothing in the MCP ecosystem declares what a
  server's tools reach. The report says so instead of guessing; the fix we're proposing is a
  one-line declaration ([DECLARING.md](DECLARING.md)).

**Why it matters:** confinement is blast radius. An agent that *can't* reach the network can't
exfiltrate what it reads, no matter what its prompt is talked into. Today the only way to know your
fleet's reach is to read every frontmatter file and every MCP config and hold the delegation graph
in your head — which is precisely the kind of transitive bookkeeping nobody does reliably. One scan
makes it a report; one policy line (`deny Net reviewer`) makes it a gate.

**Honest limits, before you ask:** grants are *may-use* upper bounds — the report says "can reach",
never "does". Named-delegation narrowing is prose-matching (a heuristic). `Bash` is a capability
cliff (a shell reaches everything; we classify it `Exec` and say so). And candor is not a security
boundary — a gate is necessary, never sufficient (our own evals show a determined agent can game
an effect gate by breaking the feature; that's why `rewire` exists on the code side).

Repo: https://github.com/tombaldwin/candor-agents — scanner, tests, a soundness fuzzer with
verified teeth, and the fixture demo. The whole thing needs git + stable cargo + python; `run.sh`
is the five-minute tour. The numbers above reproduce with `scan.py` against the collection.

## Crib answers

- **"Is this a security advisory?"** No. Nothing here is a vulnerability; it's a visibility gap.
  The collection we scanned is good work by many authors — the *default* is what's unconfined, and
  defaults are an ecosystem question, not an author one.
- **"Isn't ambient authority fine if I trust my agents?"** The agent isn't the threat model — its
  *inputs* are. Prompt-injected agents do what the injection says, with whatever tools they hold.
  Minimum tools = minimum blast radius. (We still say "can reach", not "will be exploited".)
- **"Why should the fleet tool share machinery with a code linter?"** Because the math is the same
  (reachability over a typed graph with an honesty marker), and sharing it is the proof: the
  unmodified candor-query binary and the unmodified policy grammar both work on fleet reports. One
  mental model, one gate, code and fleet — including linked: a `whatif` on a *code* function can
  return a *fleet-level* policy verdict.
- **"What should MCP do about `Unknown`?"** Declare capabilities — ideally server-published at
  `initialize`, today as a one-line project-side annotation (DECLARING.md). Typo'd declarations
  void loudly rather than silently narrowing the surface.
