# candor-agents — effect analysis for agent fleets

<p align="center"><img src="https://raw.githubusercontent.com/tombaldwin/candor/main/assets/beaky.svg" alt="Beaky, the candor canary" width="180"></p>

**Your agent fleet is an effect graph.** Agents are units, delegation is edges, tool grants are the
classified leaves (`Bash`→`Exec`, `WebFetch`→`Net`, `Edit`→`Fs(write)`, an uncurated MCP server →
`Unknown` — never silence). candor-agents ([candor.poly.io](https://candor.poly.io)) emits [candor-spec](https://github.com/tombaldwin/candor-spec)
0.8 reports over a Claude Code project, in two modes that answer two different questions:

```sh
pipx install git+https://github.com/tombaldwin/candor-agents   # recommended — isolated venv
# (plain `pip install …` works too — the code ships as the `candor_agents` package, so nothing
#  top-level can clash; pipx is still recommended for a CLI tool's own isolated environment)

candor-agents scan    <project>    # DECLARED: what the fleet MAY do (agents, .mcp.json, settings,
                                   #           permissions, slash-commands, skills, cron)
candor-agents observe <project>    # OBSERVED: what it actually DID (the session transcripts)
candor-agents drift   <project>    # the gap: least-privilege advice + anomalies
candor-agents guard   <policy>     # ENFORCED: compile a deny-policy -> settings.json permissions.deny
candor-agents stats   <project>    # MEASURED: edit-time gate activity, from the stop-hook log
candor-agents digest  <project>    # OWNER report: a plain-language CANDOR-REPORT.md over that log
candor-agents log-gate <gate.json> [<report.json>]  # feed the digest from a jar --gate-json CI run (path-free)
```

- **scan** is the capability surface — the static fleet declarations as a report.
- **observe** reads the project's session transcripts (`~/.claude/projects/<slug>/`): per agent
  TYPE, the effects actually exercised, the delegation edges actually taken, and the literal
  surfaces from real tool inputs (`Bash` commands → `cmds`, file paths → `paths`, fetched URLs →
  `hosts`).
- **drift** joins them: an effect **granted but never observed** is a least-privilege trim
  candidate (the AS-EFF-002 analog, per agent); an agent **never spawned** is a bigger one; an
  **uncurated MCP tool observed running** is named (`mcp-uncurated:<server>`) so you curate it or
  read the transcript.

Because both modes emit conformant envelopes, the **unmodified `candor-query` binary** from
candor-rust answers `show` / `where` / `callers` / `map` / `whatif` over either report, and the
**unmodified §6.2 policy grammar** gates the fleet — `deny Exec orchestrator` means exactly what
`deny Net api` means in code, enforced by the same machinery:

```sh
bash run.sh        # the declared-mode demo — STANDALONE: needs only git + stable cargo
python3 test.py    # 276 behavioral checks, incl. the unmodified-candor-query integration lane
python3 fuzz.py    # soundness fuzzer (default 40 seeds = 80 lanes), teeth verified per mechanism
```

**The CI surface (spec 0.8):** `scan` and `observe` take `--policy <file>` (honouring
`$CANDOR_POLICY`; exit 1 on a violation, 2 when the policy is unreadable — never a silent
gate-pass) and `--gate-json <file>` — the structured verdict
`{spec, ok, violations:[{rule, fn, effects, detail}]}` (spec §3.3 ⟨0.8⟩), emitted from the same
check that sets the exit code; it is what the PR-native `candor-sarif` GitHub Action consumes.
Both also read a checked-in **`.candor/config`** (spec §3.4): discovered by walking UP from the
scan *target* (never the CWD; `$CANDOR_CONFIG` overrides discovery), fail-closed when configured
but unusable (exit 2 — a silently-dropped config is a silently-dropped gate), precedence flag →
env → config, relative values resolved against the config's own directory. A family key
candor-agents doesn't implement warns that its gate is **not** active here. Every scan receipt
also prints the spec §7 item-14 **`κ doesn't know`** coverage ledger: the uncurated MCP servers,
unknown tools and unlisted command heads the verdict could not see through, plus the
reviewed-pure grants it relies on — per-scan evidence, not a doc footnote.

**Honest bounds:** `observe` reads Claude Code's *internal* transcript JSONL — the reader is
best-effort, discloses what it could not parse in the receipt, and may break when the format moves.
Observation covers the sessions you have, not the sessions you'll run: granted-but-unused is
evidence, not proof, and the advice sharpens with more transcripts. The declared scan remains the
sound capability ceiling. **The curated MCP table is a name-trust bound**: it classifies by the
*conventional server name* only, so a server *named* `time` reads `{Clock}` whatever its entry
actually runs — `.mcp.json` is project-controlled, which makes a curated row a claim about an
honestly-named server (the same declared-not-verified tier as `candorEffects`), not an audit of the
binary behind it. If you don't control the project's `.mcp.json`, verify the entries' commands/URLs
yourself before trusting the curated classifications.

What the fixture demo shows (all real mechanics, deliberately seeded):

- **CHA for unnamed delegation**: the `coder` holds the `Agent` tool without naming its subagents →
  it edges to *every* agent and inherits the `mailer`'s `Net`/`Ipc` and the legacy `triage` agent's
  everything — so `deny Net coder` fails **today**, and the fix is exactly devirtualization:
  name the delegation.
- **The non-local consequence**: `whatif mailer Exec` → the **orchestrator**, three hops up,
  violates *its* `deny Exec` rule. candor's whole thesis, on a fleet.
- **The honesty marker**: the un-curated `internal-billing` MCP server marks every agent that can
  reach it `unresolved` with `unknownWhy: ["mcp-uncurated:internal-billing"]` — nothing in the MCP
  ecosystem declares tool capabilities today, and the report says so instead of guessing.
  **The fix is [DECLARING.md](DECLARING.md)**: a `candorEffects` array on the server's `.mcp.json`
  entry classifies it (declared-not-verified, `[]` = pure, a typo'd effect name voids the
  declaration loudly) — project-side today, server-published as the ecosystem proposal.
- **Ambient authority is visible**: the legacy `triage` agent (no `tools:` line = inherits
  everything) reads `{Clock Exec Fs Ipc Net Unknown}` — the report makes the un-confined agent the
  obvious outlier.
- **Confinement is read, not just grants**: `permissions.deny` in settings.json *subtracts* a
  hard-denied tool or MCP server (`"WebFetch"`, `"mcp__github"`) from every unit's surface — sound,
  because the harness enforces it — while a scoped deny (`Bash(curl:*)`) is disclosed but not
  subtracted (the tool stays usable). Slash commands (`.claude/commands`) and skills
  (`.claude/skills`) are scanned as their own `command:`/`skill:` units, effects from their
  `allowed-tools`. A command's shell heads (a `Bash(curl:*)` specifier or a `!`-line) **refine the
  Exec cliff** (spec §4 ⟨0.5⟩): a known head adds its effect (`curl`→`Net`, `candor*`→`Fs`/`Env` by
  the analyzer self-boundary), `Exec` stays, an unknown head keeps the bare cliff — so a command
  that only runs candor *over* the code reads `Fs`, it doesn't inherit the code's `Net`/`Db`.

**Combined mode** (validated): write the fleet report and a code engine's report under ONE
prefix and candor-query treats them as one world — `where Fs` lists agents beside Rust functions.
`--link <code-report-prefix>` goes further: Bash-holding agents edge to the code's entry points
and inherit their *measured* effects, so `callers <code-fn>` climbs into the fleet and a
`whatif` on a code function returns fleet-level policy verdicts (exit 1 across the boundary).

See [DESIGN.md](DESIGN.md) for the full mapping, the tool table, and the honest non-transfers
(the Bash capability cliff; grants are may-use upper bounds; dynamic tool surfacing → `Unknown`;
the function/unit distinction, carried by the spec's `unitKind` field as of ⟨0.5⟩).

## What this is not (yet)

Not a product: no runtime validation, and the MCP capability table is small. The next real steps,
if pursued: an MCP capability-declaration convention (the `Unknown` killer); scanning more real
fleets in the wild.
