# Changelog

All notable changes to candor-agents are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and the family convention (candor-rust's CHANGELOG):
candor is pre-1.0, so minor versions may include behavioural changes — always in the
soundness-increasing direction (the §4 trust contract) — and a **⚠** marks an entry that affects
report bytes, drift results, or gate verdicts (regenerate baselines / expect verdict changes across it).

candor-agents is the family's **domain engine** (SPEC §4): its units are *agents*, not functions, and it
rides the spec ladder on its own schedule (it never holds the four code engines' floor back). Its
major.minor tracks the spec it declares — `0.15.x` declares spec `0.15`.

## Unreleased

- ⚠ **The two `.mcp.json` trust tiers were RANKED, not joined, so a project's own declaration was
  silently DISCARDED whenever candor happened to curate a server of that name.** DECLARING.md said
  candor's curated table "outranks a declaration when both exist"; read as a total order that is
  under-protective in the widening direction. Measured over two `.mcp.json` files differing ONLY in
  the server's NAME, both declaring `["Fs","Net"]`, one agent granted that server's tool, one
  `deny Net worker`:

      acme        (uncurated)       ->  AS-EFF-006, exit 1
      filesystem  (curated as {Fs}) ->  `policy ✓`, exit 0, disclosed NOWHERE

  So naming your server after a curated one silenced your own declaration and turned a red gate
  green. `guard` inherited it the moment it started delegating to `scan` (the previous entry's fix):
  same policy, same `.mcp.json`, one commit apart, `deny = [WebFetch, WebSearch, mcp__filesystem]`
  became `deny = [WebFetch, WebSearch]` with no warning about the dropped declaration. That commit's
  test covered only the NARROWING direction, so it could not see it.

  **This is not a spec violation and it was still wrong**, which is why the fix is an argument and not
  an edit. DECLARING.md asserts two paragraphs above the precedence sentence that "a typo must never
  silently narrow the reported surface"; a *valid* declaration dropped in silence is that same
  forbidden direction with a better-formed input. Both tiers assert a LOWER BOUND on a server's
  surface — candor's claim about a conventionally-named server, the project's claim about the server
  it actually runs — and the sound combination of two lower bounds is their UNION, which is what
  `scan.mcp_server_effects` now computes, in one place, for the three call sites that each walked
  their own copy of the ladder (`classify`, `classify_units.mcp_effects`, `guard.server_effects`).
  The union keeps every case the precedence rule was written for and changes only the one it was never
  argued for: a narrowing declaration still cannot narrow (`github` declaring `["Fs"]` is still denied
  by `deny Net`), `"candorEffects": []` on a curated server is still inert, and an UNDECLARED curated
  server still classifies as exactly its curated entry. A VOIDED declaration on a curated server now
  also reaches the report as `Unknown` + `mcp-decl-invalid` instead of vanishing behind the curated
  row — the operator's claim was discarded either way, and discarding it silently is the same defect
  one branch over. DECLARING.md and AGENTS.md carry the corrected rule.

- **The test harness could DIE MID-RUN and report a smaller green number instead of an error.**
  `test.py` is one module-level script, so any exception raised while BUILDING a `check()` argument
  aborts the file. Measured by deleting `policy.scope_matches`' empty-scope guard: the IndexError it
  prevents killed the run at check 490 of 553 — no FAIL row, no summary, 63 checks that never
  executed, and nothing in the output distinguishing that from a shorter suite. The guard's own
  comment claimed it was pinned so a regression "fails a test, not a crash"; it crashed. Two fixes:
  `called()` evaluates such a call and returns the EXCEPTION, so a crash-guard's removal fails its own
  row (all 553 now execute, one goes red); and the harness now DECLARES its denominator and reconciles
  it at exit — the module must reach its end, and `PASS + FAIL + SKIPPED` must equal `DECLARED`, or
  the run prints `test: ABORTED` / `RECONCILIATION FAILED` and exits 3. Proven by injecting an
  exception mid-run (`ABORTED — died after 201 of 565 declared checks; 364 never ran`, exit 3) and by
  silencing one conditional block (`551 passed, 0 failed` followed by `RECONCILIATION FAILED`, exit
  3). Environment-dependent blocks now call `skip(why, n)` so their absence is accounted, not
  subtracted. The reconciliation also names the DISK when free space is low: this machine's disk hit
  zero during the work, and a suite that cannot write a fixture fails in exactly the same shape as one
  that found a bug.

- **`_linked_gate` and the `--gate-json` rows read their verdict with a bare `json.load(open(...))`,
  so an engine that never wrote one killed the harness** instead of failing the rows — including in
  the DETAIL argument of a check, one argument over from the condition it explains. `_rcls`'s own
  docstring already claimed this property for that helper ("so a mutant that empties the verdict FAILS
  these checks instead of crashing the run out from under the rest") and the line above it defeated
  it. With `verdict()` in place, deleting `scan._same_artifact`'s empty-path guard now runs all 565
  checks and fails 24; before, it died at check 71.

- **A bare `\r` policy compiled to a fully permissive `permissions.deny`, and the test that claimed to
  pin the guard could not see it.** `"deny Net\r\ndeny Db\r\n"` parses correctly with the WHOLE CRLF
  normalisation deleted, because the per-line `.strip(" \t\n\v\f\r")` handles a trailing `\r` — so
  the CRLF row measured nothing. The case the guard's own comment names, a classic-Mac BARE `\r`, had
  zero coverage: without the normalisation `split("\n")` sees ONE line, `deny Net\rdeny Db` tokenises
  to `[deny, Net, deny, Db]`, and the file compiles to a single `deny Net` SCOPED TO A PHANTOM AGENT
  named `deny` — zero errors, zero warnings, and `compile_guard` emits `deny: []` for a policy that
  denies Net and Exec. **Boundary, stated: this is the LIBRARY surface only.** Every `open()` in the
  package is default text mode (grepped: no `newline=`, no `"rb"`, no `.decode()`), so universal
  newlines has already normalised any policy FILE — measured end to end, a bare-CR policy file still
  gates at exit 1 with the normalisation deleted. The live hole is `parse_policy(text)` /
  `compile_guard(text, dir)` called with a string, which is how `test.py`, any embedder, and anything
  reading a policy off a socket or a JSON field reaches it. Rows added for the bare-`\r` parse and its
  compiled fragment, plus the over-charge control (normalising `\r` must not MINT a rule); the CRLF
  row is kept and relabelled as the control it actually is.

- **`_same_artifact`'s empty-path row was VACUOUS, and one vacuous row implied a class.** Its two
  cases were `("", <a real file>)` and `(<a real file>, "")`, and `realpath("")` is the CWD, which is
  not that file — so both stayed `False` with the guard deleted. The values that discriminate are the
  ones where the degenerate side resolves ONTO the other: two empty paths, and an empty path against
  the cwd, both answer True and produce a FALSE `SAME FILE` refusal at exit 2 on a run with no
  collision. And `None`, not `""`, is the PRODUCTION shape — `_refuse_sink_over_input` is called with
  `os.environ.get("CANDOR_POLICY")`, unset on almost every real run, and `realpath(None)` raises a
  TypeError that `resolve`'s `except OSError` does not catch, so deleting the guard crashes
  `scan --gate-json` outright.

  **The sweep that vacuous row implied** — boundary drawn round the MECHANISM, not round
  `_same_artifact`: every guard in every module whose job is to catch an absent/empty/None/wrong-type
  value, 124 of them, each disabled in turn against the full suite. 87 red, 16 aborted, **21 GREEN —
  no row could tell the guard from its absence.** Seven of the 21 are one pattern: a guard COVERED on
  the `scan` route and UNCOVERED on the identical `observe` route. Closed here:
  - **a BLANK LINE in `.candor/config` was an IndexError that killed the scan before the gate ran** —
    `parts[0]` after splitting an empty line, in BOTH parsers behind that one `open()`;
  - observe's five missing-flag-value guards (a `--policy` swallowed as None is a gate that silently
    never ran), its duplicate-`--gate-json` refusal reaching the exit code, and its `--gate-json -`
    stream refusal for a run that exits before the gate — all five/three pinned on `scan` and none on
    `observe`;
  - a non-dict tool `input` in a transcript (an `AttributeError` in the lane whose unfuzzed crash
    shipped a bug once);
  - `policy.reason_class_matches`' empty-`classes` arm, whose own docstring calls it "THE FAIL-CLOSED
    NET" and describes the silent under-report it prevents — unreachable through the only tested route
    (`--link` substitutes `{"unresolved"}` upstream), so the comment asserting the property is exactly
    what stopped it being measured.

  Judged dead, not findings: `observe.py`'s non-list `content` guard (iterating a string yields
  characters and the next line's `isinstance(b, dict)` rejects every one) and `guard.py`'s `.mcp.json`
  existence fast-path (`read_mcp` re-checks). Reported, not fixed — cosmetic or fail-closed: digest's
  empty span, guard's residual-advice branch, savings' `.jsonl` filter and default transcript dir,
  observe's output basename, policy's empty reason-class token.

  Two of the new rows were themselves VACUOUS on their first draft and only falsification found it:
  five shared a fixture dir whose `.candor/config` pinned a bogus engine, so every arm exited 2 on the
  pin mismatch; and asserting the exit code plus the diagnostic still could not see the
  `--transcripts` guard, because `value()` writes its message BEFORE returning None and the run then
  reaches `no transcripts found` and exits 2 anyway. Both now assert that the run went NO FURTHER.

- ⚠ **`observe` classified MCP servers from a table that knew nothing about `.mcp.json` declarations,
  so the OBSERVED gate passed clean over the exact tool use the DECLARED gate fails on.** The module
  docstring has always said observed effects are "classified by the same table as the static scan";
  that was true of the TABLES and false of the LADDER. `observe.classify_tool` was a second table walk
  that knew only `MCP_TABLE`, so the `candorEffects` tier `scan.read_mcp`/`scan.classify` own — the
  documented DECLARING.md convention, the project's own claim about its own server — did not exist on
  the observed side. Measured on a fleet whose `.mcp.json` declares `acme: ["Net"]` and whose
  transcript shows one `mcp__acme__query` use, same policy, same fleet, one command apart:
  `scan --policy 'deny Net'` → AS-EFF-006, exit 1; `observe --policy 'deny Net'` → `policy ✓`, exit 0.
  A silent under-report of a first-class gate surface. It came with two more defects in the same
  gap: a FALSE disclosure (`mcp-uncurated:acme` on a server the project HAD curated — the same class
  as a config key reported ignored while being honoured), and a false `drift --strict` anomaly, so
  **following DECLARING.md was by itself enough to fail the drift gate**. `classify_tool` is now an
  adapter onto `scan.classify`'s one-tool case, which also brings the two tiers a copy could never
  have grown on its own: ⟨0.24⟩ `mcp-decl-invalid:<server>:<effect>` voiding, and §6.1 `refine_llm`
  (a declared `Llm` co-emits `Net`).

- ⚠ **`guard`'s deny-parser was a SECOND §6.2 parser, and it disagreed with the gate's on five shapes
  — one of them silently emitting an empty, fully permissive fragment.** It was a faithful positional
  mirror on the token partition, which is the part its comments defended, and nothing else was checked.
  Run both over the same string:
  - **`deny\tNet` (TAB-separated) compiled to an EMPTY `permissions.deny` with no warning** — the gate
    enforces the rule (`policy.parse_policy` splits on ASCII whitespace), and guard's own
    `line.lower().startswith("deny ")` wanted a literal SPACE. This is the ⟨0.34⟩ BOM defect one
    whitespace character over, in the same under-protective direction.
  - **`Deny Net` / `DENY Net` compiled a confident fleet-wide Net deny** and printed "the harness then
    enforces this deny boundary", for a rule the gate drops as an unknown rule kind and exits 0 on.
  - **A non-ASCII space inside a rule** (`deny Net<NBSP>agent`) read as a scoped deny; §6.2 splits on
    ASCII whitespace only (deliberately, the cross-engine rule) so the gate refuses it at exit 2.
  - **`str.splitlines()` splits on `\v`/`\f`/`\x85`/` `** and the gate's line split does not.
  - **A policy the gate REFUSES was compiled anyway.** `only …`, `deny Frobnicate`,
    `deny Unknown[nativ]` are fatal §6.2 errors: `run_gate` has exited 2 on them since ⟨0.24⟩ rather
    than enforce a rewritten remainder. `guard` compiled that remainder and exited 0 — the silently
    rewritten policy, arriving in the enforcement layer instead of the gate. `guard` now refuses at
    exit 2 with the same wording and emits nothing.
  `parse_denies` now delegates to `policy.parse_policy` and reads its case-fold lint off what THAT
  parser recorded (the scope token it chose; the `Unknown[…]`/`E[…]` tokens it dropped) rather than
  re-tokenising. `policy.parse_policy` grew the two structural fields that makes possible — a `widened`
  key on a deny rule whose destination-class filter was dropped, omitted when empty so every other rule
  dict stays byte-identical, and the scope token on a `deny names no known effect` error. A new
  **parity row runs guard's compiled rules against the parser's output over 21 policy texts**, so a
  future divergence of any shape fails, not just the five measured here.

- ⚠ **`guard` also hand-rolled the `.mcp.json` reader `scan.read_mcp` owns, and disagreed three ways,
  every one under-protective** — a compiled `permissions.deny` silently missing a server the scan gate
  fires AS-EFF-006 on:
  - **PRECEDENCE:** scan reads the curated `MCP_TABLE` FIRST and a `candorEffects` declaration only as
    a fallback (`.mcp.json` is project-controlled, so a project must not be able to narrow candor's own
    claim about a conventionally-named server). guard read the declaration first, so a server named
    `github` declaring `["Fs"]` was left out of a compiled `deny Net`.
  - **`Llm` ⇒ `Net`:** `read_mcp` applies §6.1 ⟨0.24⟩ `refine_llm` at the source. guard took
    `set(decl)` raw, so a server declaring `["Llm"]` — the one thing on the fleet reaching the network
    — was invisible to `deny Net`.
  - **SHAPE:** a wrong-TYPE `candorEffects` (`"Net"`, a string) fell through `else:` and left the
    server silently un-denied with no warning, while scan voids it loudly as `mcp-decl-invalid`. The
    list-valued typo was already handled here; the wrong-type branch was the one nobody wrote.

- **`.candor/config` is TWO line parsers behind ONE `open()`, and a leading UTF-8 BOM defeated both —
  the ⟨0.34⟩ BOM sweep's own boundary, drawn around "hand-rolled line parsers" and missing the file
  read by two of them.** Same artifact, one file over, and it silently disables gates:
  - **`policy fleet.policy` read the key as `'﻿policy'`**, fell to `ignoring unknown config key`, and
    the gate was NEVER CONFIGURED — the identical file exits 1 on a violating fleet without the BOM
    and 0 with it. The disclosure that did print is itself false: the key is not unknown, it is
    `policy` carrying an invisible byte.
  - **`engine v0.33.1` was skipped by `engine_pin_for`'s `parts[0].lower() != "engine"`** with NO
    message at all, so a mismatched §3.4 pin that exits 2 exited 0.
  Stripped once at the read, so both parsers are covered by construction and a third over this file
  cannot reintroduce it.

- **`savings` anchored "is this a candor-query invocation?" on its own regex instead of
  `scan.bash_cmds`, and under-counted the MEASURED half of a labelled estimate on four real
  invocation forms**: a newline separator (`^` with no `re.M`), a `sudo` wrapper, a shell keyword
  (`for …; do candor-query …`), and a command substitution (`X=$(candor-query …)`). All four are
  already handled by the module that owns "what does this shell string run". `bash_cmds` is also the
  stricter half where it matters — `echo "candor-query …"`, a `#` comment, a path argument and a
  heredoc body still do not count — so the anti-fabrication property the anchor was reaching for is
  kept rather than traded away. The `npx [-y] candor-ts-query` form keeps a literal matcher: its
  command head is `npx`, the one invocation `bash_cmds` alone cannot see.

- **`scan`: four sites open-coded `base_tool`'s split** (`live()`, both `read_commands_skills`
  branches, `_heads`). Behaviourally identical today — no test can go red on this and none was added;
  it is de-duplication so the next change to base-name handling cannot land in three of four places.

  *17 new regression checks for the findings above plus 3 for the config BOM, each falsified against
  the pre-fix tree in a throwaway worktree (18 of 20 RED; the 2 that stayed green are the two
  over-charge controls, which is what a control is for). 553/553 checks green, `fuzz.py` (80 seeds)
  clean, `run.sh` clean, and the suite re-run under `python:3.12-slim` on Linux (545/553 — the 8
  skipped are the `candor-query` parity block, the `et_EE.UTF-8` locale control, and two chmod-000
  fixtures that cannot construct their condition as root).*

- **A leading UTF-8 BOM (a real, still-common Windows-editor/export artifact) silently blinded three
  independent hand-rolled parsers, in three different failure shapes, because `read_md`'s and the
  policy readers' plain `utf-8` codec does not strip one.** Found by attacking `scan.py`'s frontmatter
  matcher directly (`\A---\n…` never matches `﻿---\n…`) and then asking where else the same
  encoding artifact reaches a line-oriented parser (§ AGENT-CORPUS-BRIEF section G: two modules
  computing a related fact independently, sharing an unhandled input shape).
  - **`scan`: a BOM'd command or skill file's `allowed-tools` silently vanished, reading as a fully
    PURE unit.** `parse_frontmatter` returning `{}` on a match failure is indistinguishable from "no
    frontmatter at all" — for an AGENT that's the safer over-approximating direction (absent `tools:`
    reads ambient, and `read_agents` already has its own disclosed skip for this shape), but for a
    COMMAND or SKILL an absent `allowed-tools` reads PURE (the opposite convention). A real
    `allowed-tools: Bash(psql:*)` command was OMITTED from the report entirely — no unit, no Exec, no
    disclosure. Fixed by stripping a leading BOM in `parse_frontmatter` itself, before the `---` match.
  - **`guard`: a BOM'd single-rule policy compiled to an EMPTY (fully permissive)
    `permissions.deny` fragment, with no warning at all.** `parse_denies`'s own
    `line.lower().startswith("deny ")` check failed on `﻿deny Net`, and unlike every other
    unenforceable shape this parser recognises, an unrecognised line is skipped in total silence.
  - **`policy` (the in-process §6.2 gate `scan`/`observe` share): a BOM'd policy's FIRST rule was
    dropped as a non-fatal "unknown rule kind", so a single-line `deny Net` policy gated nothing and
    the run exited 0 `policy ✓` over a fleet that should have failed.** This is the more severe of the
    three: it defeats the enforced gate itself, not just a disclosure surface.
  Fixed at the same site as the CRLF normalisation each parser already carried (`parse_policy`) or by
  adding the missing strip where none existed (`parse_frontmatter`, `guard.parse_denies` — guard reads
  its own file and does not call `policy.parse_policy`, so it needed its own fix). Six new regression
  checks in `test.py`, each falsified against the pre-fix parsers first. `policy.py`'s pre-existing
  CRLF normalisation had the identical gap — present and correct, but with zero coverage until now
  (found by the same guard-deletion sweep, not a new bug).

- **Three guard-deletion findings from the sweep this brief specifically named, all previously
  correct-but-untested code, not new bugs:**
  - **`_same_artifact` (SPEC §3.3.1 ⟨0.27⟩) — the entire "two path spellings, one file" collision
    guard had ZERO test coverage.** Gutting it to an unconditional `return False` left all 519
    pre-existing checks green: nothing exercised `--policy /w/P --gate-json ./P`-shaped collisions
    (identical file, different spelling) at all, at either the unit level or through the CLI. Seven
    new checks cover identity, a `.`-segment respelling resolving via `realpath` (the case a naive
    string compare misses — the exact scenario the docstring names), two genuinely different files
    correctly NOT colliding, the `"-"` stream-sink exemption, and two end-to-end CLI reproductions
    (via `--policy` and via `$CANDOR_POLICY`) proving the file is never overwritten by the refusal.
  - **`_bash_spec_head`'s basename-stripping of a path-qualified `Bash(...)` specifier
    (`Bash(/usr/bin/curl:*)` → `curl`) was untested** — every existing head-refinement fixture used a
    bare command word, so the `.rsplit("/", 1)[-1]` step could be deleted with the full suite green.
    One new check.

- **`guard`: `deny Unknown` (bare or `Unknown[<class>]`) is no longer silently dropped.** Adversarial
  review of the enforcement surface: `guard`'s own hand-rolled positional deny-parser only recognised
  the 11 named EFFECTS, so a `deny Unknown` policy line's first token failed that check, was read as a
  SCOPE, collected zero effects, and vanished — no deny compiled, no warning, no note, and the CLI
  printed the same "no fleet-wide deny rule to enforce" line it prints for an empty policy. A compound
  `deny Net Unknown` was worse: `Unknown` was misread as a fictitious agent scope and the real `Net`
  denial was dropped along with it. `Unknown` is a legal §6.2 deny token (`policy.parse_policy` already
  accepts it) — `guard` now recognises it as its own token class, still can't bind it to a
  `permissions.deny` entry (there's no tool that produces "an unresolved capability"), and says so
  explicitly instead of going quiet. `deny Net Unknown` now enforces `Net` fleet-wide as it should.
  Three new regression checks in `test.py`, each falsified against the pre-fix parser first.

- **`guard`: that same fix had the identical hole one keystroke over — `deny Net UNKNOWN` (or
  `unknown`/`UnKnown`) silently dropped the fleet-wide `Net` denial too, with no signal that a typo had
  occurred.** `Unknown`-token matching stays exact-literal (case-sensitive, matching `policy.py`'s own
  `tok == "Unknown"` — a miscased effect reads as a SCOPE there too, never case-folded, so `guard` must
  not case-fold it either or its runtime enforcement would answer a different question than the gate
  does for the same policy text). The gap was disclosure: `guard` already has a case-fold *warning* for
  exactly this confusion on the 11 named effects (`VOCAB_LOWER`/`suspects`, catching `deny net`) and it
  simply wasn't extended to `Unknown`. It now is — `VOCAB_LOWER["unknown"] = "Unknown"` shares the one
  map rather than adding a second mechanism — so `deny Net UNKNOWN` now warns ("effects are
  case-sensitive... use `deny Unknown`") instead of reading identically to a genuine `deny Net
  researcher`. No enforcement semantics changed; a real agent scoped literally `unknown` is unaffected.
  A sweep of every other token `guard`'s positional parser reads found one more instance of the same
  root cause, unrelated to casing: the §6.2 ⟨0.20⟩ destination-class filter on a concrete effect
  (`deny Net[unknown-host]`) matched neither `VOCAB` (exact, brackets included) nor the `Unknown` token,
  so a bare `deny Net[unknown-host]` silently collected zero effects and vanished — the same
  whole-rule-drop shape, on a legal policy line rather than a typo. Fixed the way `policy.py` handles
  it: keep the effect, drop the filter (widen, never narrow), and disclose it. Six new regression checks
  in `test.py`.

- **`scan`: a declared `Agent(x, y)` spawn-allowlist member that names no known fleet agent is now
  disclosed as `Unknown`, not silently read as a fully-pure "no residual" edge.** The resolution
  ladder's rung 1 treats an allowlist as harness-enforced and sound, so it narrowed to exactly the
  members that matched a declared `.claude/agents/*.md` name and stopped there — but the allowlist is
  a list of NAMES, and a member can legitimately name a harness BUILTIN subagent
  (`Agent(general-purpose)`, `Agent(Explore)`, `Agent(Plan)` — a common real pattern, since builtins
  have no `.md` file to declare in this fleet) or simply be a typo. Either way the member is real to
  the runtime and unanalyzable by this scan, so treating it as "no residual" vanished the whole
  delegation surface behind it: `boss.md` with `tools: Agent(general-purpose)` reported ZERO edges,
  ZERO effects, and no disclosure at all — a `deny Exec boss` gate would pass clean over an agent that
  can spawn an unrestricted subagent. Now: edges are kept for whatever DOES resolve to a declared
  agent (unchanged, still sound), and any allowlist member that doesn't resolve adds `Unknown` with a
  named `agent-spawn:` reason. Two new regression checks in `test.py`, each falsified against the
  pre-fix ladder.

- **`scan`: `.claude/scheduled_tasks.json` shaped as an object with MORE THAN ONE list-valued key
  silently dropped every task under any key but the first.** `read_crons` tolerates either a bare
  top-level list or an object wrapping the list under a conventional key, and resolved the latter with
  `next(v for v in sdata.values() if isinstance(v, list))` — an allowlist of "the first list this
  reader happens to notice". A second (or third) list-valued key's scheduled tasks never became
  `cron:` units: no error, no disclosure, just fewer autonomous entry points in the report than the
  file actually declares. Changed to union every list-valued key found (a denylist posture — over-read
  a shape we didn't anticipate rather than silently narrow to one). One new regression check.

- **`scan`: three correct-but-untested guards found by the guard-deletion attack (delete the guard,
  confirm the suite goes red — none did) now have regression coverage**, so a future refactor that
  breaks any of them will be caught instead of shipping silently:
  - the duplicate-cron-id disambiguation in `read_crons` (two scheduled tasks sharing an explicit
    `id`/`name` are kept as distinct `cron:` units, the same silent-clobber class the duplicate-agent-name
    guard already covers) — deleting it dropped the second task's report row entirely, 478/478 still green;
  - a command or skill (not just an agent) whose tools match a `PostToolUse`/`PreToolUse` hook matcher
    edging to the `hooks` unit and inheriting its `Exec` — only the agent case had a test before this;
  - the `--link` Exec-boundary exemption for an AMBIENT agent whose `Bash` is removed by
    `permissions.deny` — it must not inherit the linked code report's effects (it can no longer invoke
    the code), and nothing exercised that combination before.

- **`stats`: `"maxHops": true` in an activity-log record was counted as a 1-hop deepest-propagation
  reading.** `bool` subclasses `int` in Python, and every other numeric field here (`blastRadius`,
  `unknowns`, `reviewMs`) already guards against it via `_is_int()` — `maxHops` used a bare
  `isinstance(h, int)` and was the one field the earlier bool-as-int hardening missed. Fixed to use the
  same `_is_int()` guard as its siblings. One new regression check, falsified against the pre-fix guard.

- **`observe`: `--json` beside `--gate-json -` printed the FULL report envelope to stdout, THEN
  discovered the sink conflict and exited 2 — a refusal code with a complete, successfully-parsed
  report already sitting on the stream.** `scan.py` decides this combination BEFORE producing any
  output; `observe.py` had no equivalent pre-check and relied solely on `write_gate_json`'s internal
  guard, which fires too late for observe's code shape (`observe()` prints the `--json` envelope
  unconditionally, before `run_gate` ever sees the sink). The §3.1 posture that a refusal document has
  no exempt cause and no exempt sink extends to the report stream itself, not only the verdict sink.
  Fixed by deciding the conflict in `observe.main()` before target/transcript resolution — nothing is
  written to stdout on this path now, matching `scan.py`. Found attacking the modules a prior round
  called "heavily hardened but never actually attacked by deletion" (`bin/AGENT-CORPUS-BRIEF.md`
  §C, the guard-deletion test). One new regression check, falsified against the pre-fix code.

- **A guard-deletion sweep (delete each guard, confirm the suite goes red — measure, don't reason) of
  `cli.py`, `observe.py`, `policy.py`, and `scan.py`'s classification/policy core found ten
  correct-but-UNTESTED guards** — none of them wrong, all of them one refactor away from silently
  regressing — and gave each regression coverage:
  - `policy.py`'s `only` permission rule fell through to the ordinary "unknown rule kind" warning
    (non-fatal) in every existing test — none exercised it at all, so the documented FATAL refusal
    (§6.2 ⟨0.29⟩: this engine has no from-reaches-to relation to check `only` against) had zero
    coverage; a dropped `only` would leave the rest of a policy enforced and the run exit 0/1 with the
    permission clause never checked;
  - the §4 ⟨0.27⟩ `zeroMatch` disclosure (a typo'd policy SCOPE binds no unit and reads as satisfied)
    had zero coverage, on both the direct `evaluate_policy` return and the `--gate-json` verdict key;
  - the ENTIRE `.candor/config` `engine` pin feature (§3.4 ⟨0.27⟩) — `engine_pin_for`,
    `normalize_pin_version`, `enforce_engine_pin` — had zero coverage despite three separately-commented
    historical bugs living in it (qualifier-before-arity ordering, the at-most-one leading `v`,
    ASCII-vs-Unicode digit checking); each of the three, and the match/mismatch comparison itself,
    could be reverted with the full suite green;
  - `_path_covered`'s `..`-climb-out refusal had a test, but its fixture never reached the guarded
    branch (a near-miss poison: the chosen paths already differed before the `..` segment, so removing
    the real guard changed nothing) — an allowlisted-prefix ESCAPE case (`/etc/allowed` vs
    `/etc/allowed/../../etc/passwd`) now actually discriminates it;
  - `scope_matches`'s empty-scope guard (unreachable from either production caller, both of which check
    scope truthiness first, but a direct-call crash otherwise — pinned for embedders);
  - `reason_class_matches`'s fail-closed empty-`classes` net (unreachable through this engine's own
    report generation, since every producer of `Unknown` already seeds a class — but a real gap on the
    documented `evaluate_policy` embedder surface, where a hand-built function can disagree with that
    invariant);
  - a SCOPED `mcp__server__tool` deny was indistinguishable, by any test, from a whole-server
    `mcp__server` deny — the dangerous direction: conflating them would read a still-reachable server
    as fully denied, a silent under-report of a live capability;
  - `live()`'s base-tool-stripped denial check (a whole `Bash` deny must strip a scoped `Bash(git:*)`
    grant too; reverting the check to an exact-string match, which a specifier can never satisfy, left
    the scoped grant reading as live);
  - `refuse_duplicate_gate_sinks`'s two-distinct-sinks refusal (⟨0.28⟩, extensively commented against a
    real four-engine bug, zero coverage in this engine);
  - `drift`'s `undeclared_unknown` anomaly, the docstring's own "most security-relevant drift": an
    agent observed reaching `Unknown` via a tool it never declared, where that call is its ENTIRE
    observed surface — `extra` alone can't see this, because it strips `Unknown` from both sides before
    comparing (`obs - dec - {"Unknown"}` is `{}` either way);
  - a subagent meta sidecar that EXISTS but fails to parse being counted+disclosed as an unreadable
    file, distinct from the normal (undisclosed) missing-sidecar case observe.py's own comment
    describes.

  33 new regression checks in `test.py` (486 → 519), every one falsified against a hand-reverted guard
  first, never reasoned about. `test.py` (519/519), `run.sh`, and `fuzz.py` (80/80 seeds) all stay
  green. Not attacked this round (bounded scope; left for later): `guard.py` (hardened this release
  already), `digest.py`/`savings.py`/`log_gate.py`/`stats.py`/`agentsmd.py` (the previous round's
  scope), and `scan.py`'s YAML-frontmatter parsing and command-head literal extraction.

- **Declare spec `0.34`.** `candor_agents/__init__.py`, `candor_agents/scan.py` and `pyproject.toml`
  move with the family floor. ⟨0.34⟩ adds nothing this engine emits or consumes — its three parts are
  the cross-policy refusal's cause-naming remedy, the `zeroMatch` §3.1 carve-out, and the `--policy`
  usage-error rule, none of which candor-agents' declared-vs-observed drift surface touches. The
  declaration moves because the floor is conformance-pinned and every engine declares the same rung.

## [0.33.1] — 2026-08-27

- **`ci.yml` gains `workflow_dispatch`.** Audit of the family's recovery gap after the 0.33.0 cut's
  Actions stall (three tag-triggered runs across three repos created, never expanded into jobs, and
  left neither cancellable — 409 "has not been queued yet" — nor rerunnable). This repo has no
  tag-triggered workflow to fix in the same way, but its only workflow had no `workflow_dispatch`
  either, so the same stall on a push/PR run here would have forced an empty commit to re-trigger it.

## [0.33.0] — 2026-08-26

- **MIGRATION — ⟨0.33⟩ IS NOT ADDITIVE, and the cost is measured, not estimated.** If you gate a
  **STORED** report that a pre-0.33 engine produced — committed to a repo, cached between CI jobs, or
  published by a dependency and gated downstream — expect exit 2. Measured over **32 real third-party
  projects, 67 reports, 402 report×policy pairs, all four engines**, published **0.32.1** binaries as
  the producer against **0.33** HEAD as the consumer: **202 of the 265 pairs that pass today — 76.2% —
  flip to exit 2** with the policy unchanged. It is deterministic rather than statistical: a report
  carrying any `peeked: true` class refuses **202 of 202**, a report carrying none passes **63 of 63**,
  and **26 of the 32 projects** have at least one.

  **THE REMEDY: re-scan with a 0.33 engine under the SAME policy the gate applies** — not merely *a*
  policy, which is the loose reading this rung exists to close. It discharges the cost in full:
  **265 of 265** pairs green again, no residual tax and nothing to suppress. A pipeline that scans and
  gates in ONE run under ONE policy is **unaffected** — producer and consumer are the same run, so
  `P ⊆ P` holds by construction. Nor is legitimate narrowing over-charged: **62 pairs** whose
  producer's deny set genuinely covers the gate's took **0 refusals**, and over the full cross-policy
  sweep of **918 gates**, **529 refuse correctly and none fails open**.

  **The operators this hits are the ones who followed ⟨0.32⟩'s own remedy** — *scan with the policy* —
  because that is exactly what puts a `peeked: true` class into a report. They migrated one rung ago
  and are being asked to migrate again, for a hole that remedy did not close. The wording was the
  defect and the wording is the fix. It fails **CLOSED**.

- **`candor_agents/__init__.py` joins the spec-claim sweep, and the sweep reads two more spellings.** The
  module docstring carries the contract claim — it is what `help()` and every doc renderer show — and it
  was the one literal in this repo no gate read, beside three that were already covered. The claim
  grammar also widened from `spec` + one to FOUR of `[-: "]` to one to EIGHT of `[-: "*)\]]`, so an
  ALIGNED envelope column and a markdown-linked version are visible; both were live in shipped documents
  in this family. Still derived from `scan.SPEC`, with the control extended to discriminate the wide
  grammar from the narrow one.

## [0.32.1] — 2026-08-25

- Build version → 0.32.1; the `SPEC` declaration does not move.

- **Family build bump — the domain engine is unchanged, and the spec it declares does not move.** `SPEC`
  in `candor_agents/scan.py` stays `0.32`, `pyproject.toml`'s description with it; only the build id
  moves. Nothing about the declared-versus-observed drift analysis changed, and no drift result differs.
  This is a CODE-engine patch: candor-java's v0.32.0 native binaries were withheld by its parity gate
  after the image reported `0 functions` over a tree the jar found 210 in, and the front door reaches the
  rebuilt ones only when `ENGINE_PIN` moves for the family at once. `adopt/candor-digest.yml`'s
  `candor-agents@v` pin travels with it, so this package is republished at 0.32.1 to keep
  `pipx install "git+https://github.com/tombaldwin/candor-agents@v0.32.1"` resolvable beside it.

## [0.32.0] — 2026-08-25

- **⟨0.32⟩ — this engine is unchanged; only the declared spec moves.** The rung is about the CODE engines'
  file set: a class the scan did not read makes the verdict INCOMPLETE, the carve-out keys on the policy in
  force rather than on the producing scan's history, a multi-report verdict joins by `hash` instead of bare
  `fn`, and `Exec` reaches the subprocess capability rather than only the launch. None of those has a
  surface in a domain engine whose units are agents: candor-agents reads a declared manifest and the
  observed tool calls beside it, so it has no excluded-file census, no peek, and no cross-report join. The
  `SPEC` declaration in `candor_agents/scan.py` moves to `0.32` so the family agrees on one floor; nothing
  about what this engine analyses or reports changed.

- **`pyproject.toml`'s description had been left behind by two floor bumps running, and nothing read it.**
  It is a CONTRACT CLAIM — what PyPI and `pipx` show — and it said `candor-spec 0.31` while
  `candor_agents/scan.py` said `SPEC = "0.32"`. 124012a caught the same string one bump earlier, by hand,
  from the umbrella's preflight rather than from this suite. Every spec assertion in `test.py` reads the
  report ENVELOPE, and the envelope was correct each time, so the packaging text drifted unwatched.

  Now swept, and UNIVERSALLY rather than by grepping the current value: a positive `contains "spec 0.32"`
  is satisfied by one correct mention and stays blind to a second, stale one in the same file — which is
  exactly how candor-rust's and candor-java's READMEs kept a `"spec": "0.31"` gate-output example through
  this same bump. Every `spec <X.Y>` in README.md, AGENTS.md and pyproject.toml — prose, hyphenated, or
  `"spec": "X.Y"` — must equal `scan.SPEC`, DERIVED, never a literal. A CONTROL runs first, because a
  pattern that stopped matching finds nothing and so does a clean sweep. README's `(spec 0.8)` note on the
  CI surface is a historical marker and takes the family's `(spec 0.8, informative)` form, which is what
  the exemption keys on — not a list of tolerated old versions.

- Written by hand rather than left empty ON PURPOSE, and the hazard is armed rather than theoretical:
  `_stage_changelogs.py` SKIPS a changelog whose `## Unreleased` is empty ("nothing would ship
  unlabelled"), so no `## [0.32.0]` heading is created — and `release.sh` then falls through to the newest
  NON-EMPTY section, which is 0.31.0's. An empty section does not publish nothing here; it publishes the
  PREVIOUS version's notes under this version's tag.

## [0.31.0] — 2026-08-20

- `pyproject.toml`'s description said `candor-spec 0.30` — a contract claim, and the one that reaches
  PyPI. Moved with the floor.

- **⟨0.31⟩ — this engine is unchanged.** The rung's two halves are the scan route's unevaluable-target
  refusal and the `netPartners` disclosure, and neither touches a domain engine whose units are agents
  rather than functions. The declared spec moves to `0.31` so the family agrees on one floor; nothing
  about what this engine analyses or reports changed.

  Written by hand rather than left empty on purpose: `release-stage.sh` skips a changelog whose
  `## Unreleased` is empty ("nothing would ship unlabelled"), and `release.sh` then refuses to publish a
  release with no notes. An empty section is not a quiet no-op here — it stops the cut.

- **CI: every workflow declares `timeout-minutes`.** Gated by `release-preflight [7b]`. Without it
  GitHub's six-hour default applies, so a stuck runner blocks a release while looking exactly like a slow
  job — measured twice at 3h45m and 54m against runtimes of minutes.

## [0.30.0] — 2026-08-19

- **Spec floor 0.30.** The declaration this build emits as `candor.spec` moves with the family; see
  candor-spec's changelog for the rung.

- **Spec floor moved to 0.30.** No behaviour change in this engine: ⟨0.30⟩ governs the code engines'
  gate verdict over out-of-scope findings, a surface candor-agents does not emit. The declared `spec`
  moves with the family so a consumer reading it against a 0.30 report is not told the producer is older
  than it is.

## [0.29.1] — 2026-08-18

- **Family build bump only — no engine changes in this repo.** 0.29.1 is a WITHIN-SPEC patch cut across
  the family; the floor is unchanged at 0.29 and this engine's behaviour is identical to 0.29.0. The
  patch carries fixes in candor-ts, candor-java and candor-rust (see their changelogs) plus the Claude
  Code stop-hook work in the umbrella. Written explicitly because an EMPTY `## Unreleased` is left
  alone by the stager, and `release.sh` then falls through to "the newest non-empty section" — which
  would have published a v0.29.1 release carrying 0.29.0's notes verbatim.

## [0.29.0] — 2026-08-17

- **The packaging description names the current contract (candor-spec 0.29).** It is what PyPI and
  `pipx` show, so a stale floor there is the version claim a user reads first.

- **⟨0.29⟩ the precondition under which `scan`'s gate may omit `incomplete` is now ASSERTED, not assumed.**
  `observe.py` passes `incomplete=` to the shared `run_gate`; `scan.py` does not, and today that is
  correct rather than an oversight — `propagate`'s contract says the scan route carries effects and fs
  kinds only, while observe also carries hosts/cmds/paths, so a scan-route report has no literal surface
  and every `allow` rule already fails closed with *"no visible literal"*. Measured, including across
  `--link` against a code report whose entry declares `incomplete: ["Fs"]`.

  That makes it a LATENT hole: the day this route grows a literal surface (the declared half of the drift
  comparison is the obvious candidate), a linked report's `incomplete` stops crossing and a benign
  declared literal certifies a masked locator — the ⟨0.29⟩ dep-join defect one join over. A test now
  fails if any scan-route unit carries `hosts`/`cmds`/`paths`/`tables`, and names the call site that must
  change first. A comment alone rots; this rung already shipped one that contradicted its code for weeks.

- **⟨0.29⟩ `only` is REFUSED, not dropped.** SPEC §6.2's permission form landed in the four code engines;
  this is the FIFTH §6.2 implementation, and it fell to the `unknown rule kind` arm — a warning on stderr
  and a GREEN verdict over a policy whose permission rule was never enforced. `only` exists precisely
  because `forbid` fails open, so silently ignoring it is that failure in its purest form. This engine
  analyses an agent fleet, not a call graph, so it has no dependency relation to evaluate the rule against
  and refuses under §3.1's answerability rule rather than pretending. Found by review, which counted the
  engines and got five where the rung had said four.
## [0.28.2] — 2026-08-15

_A cardinal-sin fix. 0.28.1's body-less-declaration pass reopened, in two shapes, the hole it was
written to close — both found by a max-effort review of that patch, both live on npm and crates.io
until this release. The spec floor is unchanged at 0.28._

- **Version-aligned only, no functional change.** The cardinal-sin fix this release carries is in
  candor-ts; `release-preflight` [4] requires every engine's build version to agree, so this arm
  moves with the family. The spec floor is unchanged at 0.28.

## [0.28.1] — 2026-08-15

- **Version-aligned only, no functional change.** `release-preflight` [4] requires every engine's
  build version to agree, so this arm moves with the family's post-release review patch even
  though none of those fixes were here. The spec floor is unchanged at 0.28.

## [0.28.0] — 2026-08-14

- **Two committed scan OUTPUTS removed** (`report.agc.Fleet.json`, `report.fixture.Fleet.json`). Nothing
  referenced them; the `.gitignore` meant to exclude them named a single package (`report.fleet.Fleet.*`)
  and missed these two, so they were committed by accident and went stale at the next floor bump, where
  release-preflight flagged them as artifacts declaring the previous contract. Pattern widened to
  `report.*.Fleet.*`.
- **The pyproject description** named the previous floor.

- **AGENTS.md points at the umbrella** — this engine answers what a fleet MAY do, and says nothing about
  what the code its agents touch actually reaches; the umbrella is where the other four engines are.

- **⚠ `observe` had none of the §3.3.1 sink layer.** It is a first-class gate surface — `--policy` and
  `--gate-json` are both in its CLI — with no arming, no input guard, no ⟨0.28⟩ duplicate rule and no
  stream guarantee. Measured: a FIRING gate wrote red to the last sink and left the first holding a
  pre-seeded `{"ok": true}`; `--gate-json <the policy>` overwrote the policy and exited 0; every exit-2
  cause with `--gate-json -` gave zero bytes. All of it had been fixed in `scan.py` and nowhere else.
- **⚠ Two regressions from the same day, both found by a release panel.** The ⟨0.28⟩ per-sink loop copied
  four of the five input checks and omitted the `.candor/config` SHAPE test, so a duplicate sink naming
  the config DESTROYED it — through the refusal added to protect operators' files. And `--json
  --gate-json -` emitted TWO documents (parseable before, `Extra data` after) because the conflict return
  never marked the stream written, so the entry wrapper appended a refusal after the report envelope.
- The duplicate rule now lives in ONE function both routes call. It was briefly two — an inline copy plus
  the extracted helper — and a conformance falsifiability check caught it in minutes: disabling the helper
  changed nothing on the scan route, so the row pinning it was vacuous.
- Conformance PART 36 now runs (b18)/(b20)/(b22) and an observe row against this engine, so the rung is
  pinned five-way rather than claimed five-way.

- **⚠ Three exit-2 causes left `--gate-json -` EMPTY**: a nonexistent fleet path, an unreadable
  `.candor/config`, and an unsatisfied engine pin — while the other four engines wrote a refusal on the
  same inputs. A file sink was already covered (arming leaves a placeholder every later exit inherits); a
  stream has no placeholder, and its refusal was written at exactly ONE site, the `parse_args` usage
  error. Everything met afterwards left through a bare `sys.exit(2)`.

  Fixed with ONE wrapper at the module entry rather than three patched sites, because the fourth cause
  nobody enumerated is the one that matters — measured the same day, when a generated argv sweep found an
  exit-2 cause absent from a hand-written list of twelve. The narrower copy inside `main` is removed
  rather than kept beside it. Conformance PART 36 rows (b4)/(b5)/(b6) pin the three, and were proven to
  fail against the pre-fix entry before being believed.
- **⟨0.28⟩ a repeated `--gate-json` is refused, and every path named gets the refusal** (SPEC §3.3.1). Two
  spellings of one path stay ONE sink; a sink that is an input is refused having written nothing.
## [0.27.0] — 2026-08-07



- **A config-driven exit no longer leaves a stale GREEN verdict at the file sink.** The collision
  pre-pass loads `.candor/config` to learn what the sink must not overwrite, and re-raised `SystemExit`
  — so an unreadable config exited THERE, before `arm_gate_json`, leaving a previous run's `ok: true`
  intact on disk. SPEC §3.3 names that outcome: "a refusal that writes nothing leaves the previous run's
  green document on disk." The pre-pass now loads leniently and the arming happens; the real load
  refuses a moment later with the sink armed. Nothing is lost, because a config nobody can read declares
  no inputs anyone can name. Same defect, same fix, as candor-ts.
- **⟨0.27⟩ The stream sink and `zeroMatch` (SPEC §3.1/§4, conformance PART 36).** (1) `--gate-json -`
  now carries the fail-closed refusal document on the exit-2 causes that fired before the gate tail —
  a usage error (unknown flag, valueless flag, missing target) and an unreadable policy — instead of
  leaving stdout empty; a file sink gets the specific reason in place of the armed placeholder on the
  unreadable-policy cause. (2) `zeroMatch`: the §4 zero-match list now rides the verdict document
  (code-point sorted, deduplicated, omitted when empty); it was stderr-only in all five engines.

- **⚠ ⟨0.24⟩ A POLICY THIS ENGINE COULD NOT HONOUR WAS SILENTLY REWRITTEN INTO A WEAKER ONE.**
  `deny Frobnicate` was dropped with a stderr note and the run exited **0 printing `policy ✓`** — the
  operator reads an armed gate that does not exist, which is the fail-open SPEC §6.2 ⟨0.24⟩ closed in
  the four code engines by refusing (exit 2, the unreadable-policy posture). Found while measuring the
  PART 36 cells. The FATAL set now matches theirs on every token it shares, which is not the same as "token for
  token" — an earlier draft of this sentence said that and it was false in two places, both deliberate
  and both worth stating rather than quietly matching:

  - `deny Net[<class>]` is HONOURED (scoped, exit 0) by candor-classify for a class it recognises and
    FATAL only for one it does not; here EVERY bracketed form is WIDENED — the filter dropped, the rule
    enforced unscoped — with a stderr line saying so. So the divergence is wider than a fatal-set
    mismatch: a filter candor-classify would apply, this engine discards.

    (This sentence has now been wrong twice. It first claimed parity "token for token"; the correction
    then said candor-classify treats the whole bracketed form as fatal, which is true only for an
    unrecognised class — measured: `deny Net[known-telemetry]` and `deny Net[*]` exit 0 there,
    `deny Net[telemetry]` exits 2. Writing a comparison to another engine without running that engine
    is what produced both.) This engine emits no `netClass`, so honouring the
    filter would match an absent field and PASS — fail-open — and dropping the whole rule fails open
    too. §3.1's policy-side rule is that a dropped token leaves a WIDER rule standing, so widening is
    the third road and the only closed one. A bracketed **allow** is refused outright rather than
    widened, because widening an allow is the direction that hides a violation.
  - `allow Llm` is honoured by candor-classify and refused here: `ALLOW_EFFECTS` is the four literal
    surfaces (`Net`, `Exec`, `Fs`, `Db`) and Llm is not among them.

  Otherwise the set is theirs (candor-classify `not_honoured!(true, …)`):
  a `deny` whose effect list ends up EMPTY, an `allow` naming an effect outside the four literal
  surfaces, and an unrecognised reason-class/alias inside `Unknown[…]` — that last one is the dangerous
  direction, since `deny Unknown[dispatch,nativ]` ran as `Unknown[dispatch]` and let every `native` hole
  through green. Everything else stays reported-and-survivable (`allow` with no values, a malformed
  `forbid`, an unknown rule kind), because the rest of the policy still means what it says. The refusal
  reaches BOTH sinks: the file placeholder is replaced with the specific reason, and `--gate-json -`
  carries the refusal document as stdout's only content. There is no composed case to weigh — this
  engine has no AS-EFF-005 baseline producer, so every violation it can establish comes from the policy
  being refused, and a bad token establishes nothing from the policy itself (§3.1); the site that would
  have to consult one is marked.

- **⚠ This engine declared `spec 0.27` and implemented none of the gate-sink layer that release adds.**
  Measured: `--policy P --gate-json P` DESTROYED `P`, so the very next run of the same command exited 0
  on a fleet that violates — the gate silently gone, which is the machine-readable false all-clear the
  ⟨0.27⟩ rung exists to close. And an unknown flag beside `--gate-json G` exited 2 leaving the PREVIOUS
  run's green at `G`. Four engines gained the guard in this release; this one declared the same contract
  without it, and conformance PARTs 32/34 ran four engines, so nothing caught it.
  Now: the sink is armed fail-closed at the instant it is known — before `parse_args`, whose own exits
  would otherwise leave a stale green — and a sink naming an INPUT is refused with nothing written. Every
  channel: `--policy`, `CANDOR_POLICY`, `CANDOR_CONFIG`, the config's own `policy` key (the checked-in
  form, i.e. the one CI has), and any `.candor/config` by shape. Sameness resolves artifacts, not strings.
- **⚠ §4 zero-match: a rule whose scope bound no unit was scored as SATISFIED.** `deny Exec orchestratr`
  — one character — turned a failing gate green, silently. Now disclosed on stderr with the verdict and
  exit code untouched, because a zero-match rule is legitimate when one policy is shared across fleets.


- **A version is ASCII digits, and `str.isdigit()` is not.** `engine ٣.٣` (Arabic-Indic) and `engine ².0`
  NORMALISED as versions, so they read as a MISMATCH rather than MALFORMED — and that difference decides
  whether the "an unreadable unqualified line is not hidden by a qualified pin" rule fires. Beside a good
  qualified pin the junk line was handed over silently and the run passed at **exit 0** while three
  engines exited 2. Alone, every engine already refused; only the paired shape shows it. candor-swift had
  the same defect via `Character.isNumber`; both fixed, five-way now, pinned by conformance PART 33.


- **An unreadable unqualified pin was hidden behind a qualified one.** `engine garbage` beside a good
  qualified line passed silently here while candor-java exited 2 — unreadability is a property of the
  line; precedence only decides which version applies.

- **A bare `engine <impl>` still split the family five ways.** `engine swift` — an operator forgetting
  the version on a qualified line — was skipped by candor-java and treated by the other four as a
  WILDCARD pin whose version is the literal `swift`, so it exited 2 in every engine that is *not* swift:
  one typo, a family-wide outage, on the exact property PART 33 exists to pin. The cause was arm ORDER —
  arity was tested before ownership, so the one-token case was claimed by the wildcard arm before anyone
  asked whose line it was. **A known qualifier now decides ownership first**, per §3.4's "whatever
  follows it" — and nothing following it is a case of that too.

- **Panel review: the pin grammar disagreed across engines on a shared config.** Three confirmed
  divergences, each a case conformance PART 33 had not thought of, all now fixed and pinned there:
  a junked line qualified for ANOTHER implementation (`engine swift 0.99.0 junk`) killed this engine's
  own run — SPEC §3.4 now rules the skip WHOLE-LINE, because a malformed line naming another engine is
  that engine's problem and it refuses on it, while refusing everywhere turns one typo into a
  family-wide outage; `vv0.27.0` was accepted as a version by engines that stripped every leading `v`;
  and a CRLF config broke a MATCHING pin where `\r` was not treated as whitespace.


- **⟨0.27⟩ SPEC §3.4 `engine` — the engine↔baseline coupling, enforced here too.** A build that is not
  the pinned one FAILS with exit 2 (UNEVALUABLE, never 1 — a machine consumer must not read "I could not
  trust this result" as "your code broke a rule"). Two of the five verdicts deliberately do NOT change
  the exit code: an absent pin (the key is opt-in by construction) and one this build cannot check,
  which is §3.1's unanswerable-condition rule — disclosed, never scored, *including* as satisfied. An
  unreadable pin (`engine latest`) exits 2 rather than being skipped: this is the one place §6.2's
  warn-and-skip inverts, because skipping a PIN hands the operator a guard they believe is on. A pin
  qualified for another implementation is ignored — one config serves the family, which versions as a
  ladder. Pinned four-way by conformance **PART 33**.


### ⟨0.27⟩ `resolves` — declare which optional refinement surfaces this producer computes

The envelope now carries `resolves`, a top-level array naming the optional SPEC §2 refinement surfaces
this producer actually computes. Absence of an optional field otherwise means two different things a
consumer cannot separate — *"undetermined"* versus *"not computed here"* — and only the first licenses
reading the absence as an answer. candor-agents declares the surfaces it resolves and no others; a
producer MUST NOT list one it does not compute.

### the spec declaration moved to 0.27, and the assertions that broke are a CLASS

Bumping the declared spec turned this repo's own suite red on assertions that hardcoded the version
string. That is not a one-off: the same shape broke six repos on the same bump. The assertions here now
DERIVE the expected value from the engine's own constant, so the next rung moves one declaration rather
than one edit per repo.


## [0.26.0] — 2026-08-04 ⟨spec 0.26⟩

### ⟨0.26⟩ floor bump to spec 0.26

No engine work: this engine emits no §2.2 hierarchy sidecar and consumes none, so the rung's
producer and consumer rules are both vacuous here. The declared `spec` moves to `0.26` so the family's
floor stays uniform. See candor-spec/CHANGELOG.md for the measurement.

## [0.25.0] — 2026-08-02

⟨spec 0.25⟩ **Floor bump only — no behaviour change in this engine.** SPEC §2 chaining rule 1 now states
that an ambiguous join key is UNIONED rather than dropped; this engine already implemented the union
(conformance PARTs 25/26 pin it four-way), so 0.25 records the contract catching up with the code. See
candor-spec/CHANGELOG.md for the measurement and the reversal note.

## [0.24.0] — 2026-07-28

Spec floor → **0.24**. Not a version-only bump: four ⟨0.24⟩ clauses genuinely bind this engine, and
three of them were live defects. The `--class` query flags, the `callers --include-unknown` frontier,
the §2.2 hierarchy sidecar and `gate --report` are N/A — candor-agents ships no §3.1 query verb by
design (the unmodified `candor-query` answers over its report). §4's five-kind code vocabulary is N/A
too: §4 ⟨0.7⟩ exempts the agent-fleet engine **by name** and requires its own documented origin
vocabulary instead, which it already had.

- ⚠ **§4.0 / conformance PART 16 — `pure <scope>` no longer fires on `Unknown`.** It fires iff a
  DETERMINED effect is present (`S ≠ ∅`); `D ≠ ∅` alone is AS-EFF-003 *disclosure*, not an AS-EFF-006
  violation. A fleet whose agents were determined-pure behind one uncurated MCP server used to fail
  `pure` and now passes. **Gate verdicts change.**
- ⚠ **§6.2 — `deny E Unknown[<class>]` is implemented; it was a FAIL-OPEN.** The bracketed form parsed
  as the rule's *scope* token, so `deny Unknown[*]` named no effect, was dropped with a warning and
  exited **0** on a report where the bare `deny Unknown` exited 1 — the spec says those two forms are
  byte-identical. The scan's own reasons all project to `unresolved` via §6.2's conservative
  catch-all, so on an unlinked fleet `[*]`, `[unresolved]` and `[dynamic]` match the bare form, while
  a code-only filter (`[dispatch]`) matches nothing and emits the advisory under-gating lint. An
  all-unrecognized bracket falls back to ALL classes — fail-closed, never to a filter that matches
  nothing. AS-EFF-006 verdicts carrying `Unknown` now include the ⟨0.19⟩ **`reasonClass`** array; the
  class resolves TRANSITIVELY, and a direct `Unknown` a unit did not name CONTRIBUTES `unresolved` at
  the source (⟨0.24⟩), so adding a reasoned callee can never turn a red verdict green.
- ⚠ **§6.2 — the reason class now crosses the `--link` boundary with the reach it scopes.** The reach
  crossed and the class did not: `link_code_report` kept only each entry's `inferred`, so a fleet unit
  inheriting a linked `Unknown` reached the gate with an EMPTY class set. Both of §6.2's named failure
  modes were live at once — `deny Unknown[dispatch]` exited **0** on a reach that is exactly
  dispatch-classified in the code report (req 2: excluded by every filter, *including one naming its
  own class*), while `deny Unknown[unresolved]` exited **1** on that same reach (req 3's mirror
  fabrication, charged by the join's absence-keyed arm), and the verdict omitted `reasonClass`
  entirely. Fixed at the source: the linked report's own TRANSITIVE resolution (over its
  `.callgraph.json` sidecar *and* its rows' `calls`) is seeded onto the pseudo-node — the entry's own
  `unknownWhy` is direct-only by §4 design and is not sufficient. A linked `Unknown` with no resolvable
  reason contributes `unresolved` at that pseudo-node, so a classed sibling on the same unit cannot
  mask it. **Gate verdicts change under `--link`, in both directions.** The pre-existing `--link`
  consumer control could not see any of this: its reason (`banana:whatever`) classifies `unresolved`
  anyway, so it asserted the same outcome whether linked reasons were consumed or dropped entirely —
  §4 ⟨0.24⟩'s "a control only exercised by inputs the implementation already handles is not a control",
  demonstrated on itself. The new controls use `dispatch:`/`reflect:`, classes the fleet scan can never
  produce on its own, and pin BOTH directions plus the bare/`[*]`/`[dynamic]` forms as unchanged. The
  ported code-prefix table in `classify_reason` was dead on every production path until this; its
  docstring's claim that chained-code reasons "classify the same way in this engine as in the one that
  wrote it" is now true rather than aspirational.
- ⚠ **§1/§5.1/§6.1 — `Llm` was missing from all THREE of this engine's copies of §1's effect table.**
  Consequences, both real: `deny Llm` named no known effect, so the rule was dropped and the gate
  exited 0 (fail-open); and `"candorEffects": ["Llm"]` was voided as out-of-vocabulary, so a server
  declaring the effect it actually has read `Unknown` — a *false* disclosure, reporting a legitimate
  declaration as a typo. One vocabulary now, sourced once. Per §6.1 ⟨0.24⟩ `Llm` **co-emits `Net`**
  (a model-provider call is an outbound request in every instance); `Db` still does not.
- ⚠ **`deny <E>[<class>]` on a CONCRETE effect no longer drops the rule.** Found by the same sweep:
  `deny Net[unknown-host]` named no known effect, so the whole rule was discarded and the run exited
  0 on a Net-reaching fleet. This engine emits no ⟨0.20⟩ `netClass`, so *honouring* the filter would
  fail open too (matching an absent field passes). It now keeps the EFFECT, drops the filter, and
  says so on stderr — the family's policy-side rule (a dropped token leaves a *wider* rule standing),
  which is safe under monotone denial. Implementing `netClass` properly is still open.
- **§2.2 — the `--link` report locator excludes all SEVEN reserved trailing segments at the glob.**
  It carved out three (`callgraph`, `calibrated`, `encountered-*`) and would claim another engine's
  `hierarchy` / `layerreach` / `locs` / `gate` sidecar as a report. Still a denylist, deliberately: an
  allowlist of known `<type>` values makes any report whose type segment we failed to anticipate
  silently invisible. A crate legitimately *named* `hierarchy` still resolves.
- **§2 — every ordering is locale-INDEPENDENT, now pinned.** Python's `sorted()` on `str` is
  code-point order, so this was already satisfied at all 72 sort sites (no `locale.strxfrm`, no
  locale-aware comparator, no environment-derived sort key). "Free today" is not "pinned": a control
  asserts the report, the callgraph sidecar and the stderr receipt are byte-identical under `LC_ALL=C`
  and `LC_ALL=et_EE.UTF-8`. Estonian, not Turkish — locale collation reorders pure ASCII, and a
  Turkish control returns "no difference" on ASCII and licenses a false all-clear.
- **Controls.** Every new test was verified to FAIL against a mutation of the fix it pins, including
  the §4 ⟨0.24⟩ MUST: a fabricated off-vocabulary kind (`banana:whatever`) round-trips verbatim and
  classifies through the conservative catch-all — and still ARMS a `Unknown[unresolved]` gate, so an
  unrecognized reason can never become a silent hole under a narrowed filter.

## [0.23.1] — 2026-07-20

Version-aligned with the family's 0.23.1 patch (engine performance + classifier-soundness fixes in the
code engines; spec unchanged at **0.23**). No functional change to candor-agents itself — it reads the same
0.23 report schema and drift semantics.

## [0.23.0] — 2026-07-20

Spec floor → **0.23** (lockstep with the family). Reads the 0.23 report schema (interfaceUnion field
tolerated; the declared-vs-observed drift analysis is unchanged). No agent-facing behaviour change.

## [0.22.0] — 2026-07-18

Spec floor → **0.22** (the `verify` oracle rung, shipped on the java/ts arms). candor-agents declares `0.22`; the
report and verdict schema are unchanged from 0.21, so this engine's output is byte-identical across the bump. No
functional change to the agents engine.

## [0.19.0] — 2026-07-17

Floor ratchet to spec **0.19**. The 0.19 rung is **reason-scoped `Unknown` policies** (SPEC §6.2) — a
§6.2 policy-gate tool-surface feature for the effect engines (rust/java/ts/swift); it adds no report-schema
or drift-surface change, so candor-agents' declared-vs-observed report is byte-identical under 0.19 (the
declared `spec` string bumps to keep the family floor uniform).

## [0.18.0] — 2026-07-16

### spec 0.18 — the trust-trio

candor-agents now declares **spec `0.18`** (`SPEC`; version `agents-0.18.0`). Tracks the family floor bump —
the pinned-tool-surface rung (`--strict` advisory-verb CI gate; the surface/`tour` mostly-Unknown
disclosure). No report-schema or verdict change; the drift-gate and `--gate-json` verdict are unchanged.

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
