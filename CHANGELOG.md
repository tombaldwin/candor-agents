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
