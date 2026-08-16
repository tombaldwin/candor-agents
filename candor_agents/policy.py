#!/usr/bin/env python3
"""The §6.2 policy grammar + standing gate, for the engine's `--policy` / `CANDOR_POLICY` surface.

candor-agents emits a candor-spec §2 report, so the canonical gate is the UNMODIFIED candor-query
binary (that "no candor tool changed" property is the whole point — DESIGN.md). But candor-query has
no single whole-report `--policy` command (its gate lives in the Rust `candor-scan` binary), so the
spec §3.3 standing gate is run IN-PROCESS here, a faithful port of the shared parser/matcher set
(candor-ts policy.mjs / candor_classify::policy) so this gate can never disagree with `whatif`.
One parser, one matcher set, same rule order: AS-EFF-006 deny/pure over transitive `inferred`,
AS-EFF-008 allowlists over the literal surfaces, AS-EFF-009 forbid by callgraph reachability.

A fleet report has no `tables` surface (no Db literal is observable from a tool grant), so a
`deny`/`forbid` over Db still gates on `inferred`, and an `allow Db …` over a unit that reaches Db
reads as uncertifiable (no visible literal) — under-report, never a fabricated clean pass.
"""
import re
import sys

# SPEC §1's effect table, minus the `Unknown` visibility marker — "every effect in the table above,
# excluding `Unknown`" ⟨0.24⟩, which is the phrasing that replaced the stale "the ten" (it went stale
# the moment `Llm` was added at ⟨0.13⟩). `Llm` was MISSING here until ⟨0.24⟩, and the consequence was
# a FAIL-OPEN gate, not a cosmetic gap: `deny Llm` named no known effect, so the whole rule was
# dropped with a warning and the run exited 0 — a green verdict for a policy the user wrote and the
# engine declined to enforce. Counting this list is how the family drifts; read §1 instead.
EFFECTS = ["Net", "Fs", "Db", "Exec", "Env", "Clock", "Ipc", "Log", "Rand", "Clipboard", "Llm"]
ALLOW_EFFECTS = {"Net", "Exec", "Fs", "Db"}  # the four literal surfaces

# §6.2 token separator: ASCII whitespace ONLY (the cross-engine rule — a non-ASCII space stays part
# of its token so the rule reads malformed and is dropped, matching the Rust/Java/TS parsers).
_ASCII_WS = re.compile(r"[ \t\n\v\f\r]+")
_VOCAB = set(EFFECTS)

# ── §6.2 reason classes — the closed, normative projection of §4's `unknownWhy` reasons ───────────
# THIS IS THE ONLY PLACE THIS ENGINE HOLDS A KIND VOCABULARY. §4 ⟨0.24⟩ records that every surveyed
# implementation held one TWICE — a string classifier feeding this table and a typed enum feeding the
# emitter — and that the halves drift silently because the string half stays right. candor-agents has
# no typed half: it GENERATES its reason strings at the source (scan.py/observe.py) and never
# re-parses them, so the divergence is unreachable. A future typed representation must land in the
# same commit as its control (`an off-vocabulary reason kind round-trips and classifies through the
# conservative catch-all`, test.py).
REASON_CLASSES = ("reflect", "dispatch", "indirect", "native", "unresolved", "setup")
# `dynamic` = every GENUINE class (excludes `setup`, which is "the analysis is not wired up"), and it
# INCLUDES `unresolved` so the recommended strict gate never under-gates.
DYNAMIC_CLASSES = frozenset(REASON_CLASSES) - {"setup"}
_UNKNOWN_SCOPED = re.compile(r"Unknown\[([^\]]*)\]")
_EFFECT_SCOPED = re.compile(r"([A-Za-z]+)\[([^\]]*)\]")


def classify_reason(why):
    """SPEC §6.2 — map ONE raw `unknownWhy` reason to its normative class. Prefix-based over the
    lowercased token, conservative catch-all: an unrecognized reason is `unresolved`, NEVER dropped.

    A DOMAIN engine (§4 ⟨0.7⟩) emits none of the five code kinds, so every reason candor-agents
    itself writes (`mcp-uncurated:`, `mcp-decl-invalid:`, `tool-unknown:`, `ambient:`, `agent-spawn:`,
    `hooks-unreadable:`, `hooks-malformed:`, `hook-type:`) reaches `unresolved` through that
    catch-all — which is the class §6.2 prescribes for exactly this case. The code prefixes are
    ported verbatim from the reference (candor_classify::policy::ReasonClass::classify) and reach the
    gate over ONE path: `scan --link`, where a chained CODE report's reasons are resolved in that
    report and seeded onto the pseudo-node (scan.link_code_report). So a reason arriving from a
    chained report classifies the same way here as in the engine that wrote it, and `ambiguous:` — a
    §4 kind only since ⟨0.24⟩, but in this table all along — lands on `dispatch` here too.

    That sentence used to be a claim about a table nothing production fed: the link path kept only
    each entry's `inferred`, so the reasons stopped at the boundary and every linked `Unknown`
    reached the gate classless. Any assertion here that only exercises DOMAIN reasons cannot tell the
    two states apart — they all classify `unresolved` either way (§4 ⟨0.24⟩: a control exercised only
    by inputs the implementation already handles is not a control). The control that can is a linked
    `dispatch:` reason, end to end (test.py §10)."""
    w = str(why).strip().lower()
    if w.startswith("reflect") or w == "dynamicmemberlookup":
        return "reflect"
    if w.startswith("native"):
        return "native"
    if w.startswith("callback") or w.startswith("closure") or w.startswith("task-handoff"):
        return "indirect"
    if w.startswith("dispatch") or w.startswith("indy") or w.startswith("ambiguous"):
        return "dispatch"
    if w.startswith("missing-config") or w.startswith("no-tsconfig") or w.startswith("no-node_modules"):
        return "setup"
    return "unresolved"


def reason_class_matches(classes, want):
    """Does a function's TRANSITIVE reason-class set intersect the filter `want`? Empty `want` = the
    bare `Unknown` / `Unknown[*]` form = all classes.

    THE EMPTY-`classes` ARM IS THE FAIL-CLOSED NET (§6.2 ⟨0.24⟩): a function whose `Unknown` carries
    no recorded reason CONTRIBUTES `unresolved`, so a narrowed filter never SILENTLY tolerates a hole
    the engine failed to classify. Read the other way round — `not intersecting ⇒ exclude` over an
    empty set — an unclassifiable hole would be dropped by EVERY filter including one naming its own
    class, which is a silent under-report wearing a filter."""
    if not want:
        return True
    if not classes:
        return "unresolved" in want
    return bool(set(classes) & set(want))


def parse_policy(text):
    """Parse a §6.2 policy into {'deny':[…], 'allow':[…], 'forbid':[…]}. Malformed rules are dropped
    with a stderr note (the shared parser's behaviour); `pure <scope>` is a `deny` with no effects.

    ⟨0.24⟩ FATAL vs REPORTED — a distinction this engine did not make, and the gap was a fail-open.
    `deny Frobnicate` was dropped with a note and the run exited 0 with `policy ✓`: a policy silently
    REWRITTEN into a different (weaker) policy, which is the same shape as the AS-EFF-005 dropped-rule
    hazard SPEC §6.2 already refuses at exit 2 in the four code engines. The fatal set here is theirs,
    token for token (candor-classify `policy.rs` `not_honoured!(true, …)`): a `deny` whose effect list
    ends up EMPTY, an `allow` naming an effect outside the four literal surfaces, and an unrecognised
    reason-class/alias inside `Unknown[…]`. Everything else stays REPORTED-and-survivable, because the
    rest of the policy still means exactly what it says. Errors land in `LAST_POLICY_ERRORS`; `run_gate`
    refuses on the fatal ones before any of them is used as a verdict."""
    import sys
    global LAST_POLICY_ERRORS
    LAST_POLICY_ERRORS = []
    errors = LAST_POLICY_ERRORS
    deny, allow, forbid = [], [], []
    # Normalise CRLF/CR to LF first: a bare-\r (classic-Mac) file would otherwise collapse to one line
    # (\r is also an in-line separator), gluing every later rule into the first and dropping it.
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.split("#", 1)[0].strip(" \t\n\v\f\r")
        if not line:
            continue
        t = _ASCII_WS.split(line)

        def warn(why, fatal=False, _line=None):
            _line = line if _line is None else _line
            sys.stderr.write(f"candor-agents: {'policy error' if fatal else 'ignoring policy rule'} "
                             f"({why}): {_line}\n")
            errors.append({"raw": _line, "why": why, "fatal": fatal})

        if t[0] == "deny":
            effects, scope = [], ""
            classes, star = set(), False  # the `Unknown[…]` reason-class filter; empty ⇒ all classes
            for tok in t[1:]:
                m = _UNKNOWN_SCOPED.fullmatch(tok)
                if m:
                    # `Unknown[dispatch,reflect]` / `Unknown[*]` / `Unknown[dynamic]` — §6.2 ⟨0.19⟩,
                    # ported token-for-token from candor_classify::policy.
                    effects.append("Unknown")
                    for cn in m.group(1).split(","):
                        cn = cn.strip()
                        if not cn:
                            continue
                        if cn == "*":
                            star = True
                        elif cn == "dynamic":
                            classes |= DYNAMIC_CLASSES
                        elif cn in REASON_CLASSES:
                            classes.add(cn)
                        else:
                            # ⟨0.24⟩ FATAL, not a note. This used to drop the class and keep the rule,
                            # on the reasoning that the surviving filter is still enforced — but the
                            # rule that RAN is then not the rule that was WRITTEN, and the direction
                            # that matters NARROWS it: `deny Unknown[dispatch,nativ]` ran as
                            # `Unknown[dispatch]` and let every `native` hole through, green. The four
                            # code engines refuse this (candor-classify KIND_REASON_CLASS, fatal), and
                            # a silently narrowed gate is exactly the fail-open §6.2 exists to close.
                            warn(f"unrecognised reason-class/alias `{cn}` — accepted: "
                                 f"{', '.join(REASON_CLASSES)}, plus the aliases `dynamic` and `*`",
                                 fatal=True)
                    continue
                me = _EFFECT_SCOPED.fullmatch(tok)
                if me and me.group(1) in _VOCAB:
                    # A bracketed filter on a CONCRETE effect — `Net[unknown-host]`, the ⟨0.20⟩
                    # destination-class form. This engine emits no `netClass`, so HONOURING the filter
                    # would fail OPEN (it would match against an absent field and pass). Dropping the
                    # whole rule fails open too: `deny Net[unknown-host]` named no known effect, so it
                    # was discarded and the run exited 0 on a Net-reaching fleet. Take the third road,
                    # which is the family's policy-side rule (§3.1: "on the policy side a dropped token
                    # leaves a WIDER rule standing"): keep the EFFECT, drop the filter, and say so.
                    # Widening is safe under monotone denial; narrowing is the silent relaxation.
                    effects.append(me.group(1))
                    sys.stderr.write(
                        f"candor-agents: policy rule scopes `{me.group(1)}[…]` by destination class, "
                        f"which this engine does not emit — the filter is DROPPED and the rule is "
                        f"enforced UNSCOPED (wider, never narrower; a honoured-looking narrow filter "
                        f"would silently pass): {line}\n")
                    continue
                if tok in _VOCAB or tok == "Unknown":
                    effects.append(tok)
                    if tok == "Unknown":
                        star = True  # bare `Unknown` ⇒ all classes (a pre-0.19 policy is unchanged)
                else:
                    scope = tok  # first non-effect token is the scope and ENDS the rule
                    break
            if not effects:
                warn("deny names no known effect", fatal=True); continue
            if star:
                classes = set()  # `*` / bare `Unknown` ⇒ empty filter ⇒ matches any Unknown
            elif classes and "unresolved" not in classes:
                # Advisory under-gating lint (§6.2): `unresolved` is the catch-all for holes the engine
                # could not classify, so a narrowed filter omitting it may tolerate exactly those. Every
                # reason this engine writes ITSELF projects to `unresolved`, so on an unlinked fleet
                # report such a rule gates nothing at all. It is NOT vacuous under `--link`: a chained
                # CODE report's reasons keep their own class across the boundary, so `Unknown[dispatch]`
                # there names a real, reachable set — hence "the fleet's own" rather than "every".
                sys.stderr.write(
                    f"candor-agents: policy rule narrows `Unknown[…]` but omits `unresolved` — it may "
                    f"UNDER-gate on holes the engine couldn't classify, and every reason the fleet "
                    f"scan writes itself classifies `unresolved`, so this rule gates no Unknown at all "
                    f"unless it is aimed at a `--link`ed code report's reasons; add `unresolved` (or "
                    f"use `dynamic`): {line}\n")
            deny.append({"effects": sorted(set(effects)), "scope": scope,
                         "unknownClasses": sorted(classes), "raw": line})
        elif t[0] == "pure":
            deny.append({"effects": [], "scope": t[1] if len(t) > 1 else "",
                         "unknownClasses": [], "raw": line})
        elif t[0] == "allow":
            if len(t) < 3:
                warn("allow names no values"); continue
            if t[1] not in ALLOW_EFFECTS:
                warn("allow supports only Net hosts / Exec commands / Fs paths / Db tables", fatal=True); continue
            scope, vi = "", 2
            if t[2] == "in":
                scope = t[3] if len(t) > 3 else ""; vi = 4
            values = t[vi:]
            if not values:
                warn("allow names no values"); continue
            allow.append({"effect": t[1], "scope": scope, "values": sorted(set(values)), "raw": line})
        elif t[0] == "forbid":
            a = t[1] if len(t) > 1 else ""
            arrow = t[2] if len(t) > 2 else ""
            b = t[3] if len(t) > 3 else ""
            if not a or arrow != "->" or not b:
                warn("malformed forbid (want `forbid <scope> -> <scope>`)"); continue
            forbid.append({"from": a, "to": b, "raw": line})
        elif t[0] == "only":
            # ⟨0.29⟩ THE PERMISSION FORM — SPEC §6.2. This engine analyses an AGENT FLEET (declared vs
            # observed tool reach), not a call graph, so it has no `from`-reaches-`to` relation to evaluate
            # `only` against and does not implement it.
            #
            # IT REFUSES RATHER THAN DROPPING, and the distinction is the whole point. Dropped, it fell to
            # the `unknown rule kind` arm below with `fatal=False` — a warning on stderr and a GREEN
            # verdict over a policy whose permission rule was never enforced. `only` exists precisely
            # because `forbid` fails open; silently ignoring it is that failure in its purest form, in the
            # fifth §6.2 implementation nobody counted when this rung was called "four-way". A rule this
            # engine cannot answer is exactly what §3.1's answerability MUST covers.
            warn("`only` is a §6.2 permission rule this engine cannot evaluate — it analyses an agent "
                 "fleet, not a call graph, so it has no dependency relation to check the permission "
                 "against. Refusing rather than ignoring: a permission rule that is silently dropped "
                 "leaves a gate green over an unenforced boundary. Gate `only` with a code engine",
                 fatal=True)
        else:
            warn("unknown rule kind")
    return {"deny": deny, "allow": allow, "forbid": forbid}


_SEG = re.compile(r"[.:]+")


def scope_matches(name, scope):
    """§6.2 scope match: by NAME SEGMENT, last segment a prefix. Segments split on `.` AND `::` so a
    shared policy matches across engines (Rust/Java qualify with `::`, the fleet units use `#`/`:`)."""
    segs = [s for s in _SEG.split(name) if s]
    parts = [s for s in _SEG.split(scope) if s]
    if not parts or len(parts) > len(segs):
        return False
    last, init = parts[-1], parts[:-1]
    for i in range(len(segs) - len(parts) + 1):
        if all(segs[i + k] == init[k] for k in range(len(init))) and segs[i + len(parts) - 1].startswith(last):
            return True
    return False


# ── effect-specific literal matchers (§6.2), mirroring the Rust/JVM/TS semantics ──────────────────
def _host_part(h):
    if h.startswith("["):
        return h[1:].split("]")[0]          # [ipv6][:port]
    if h.count(":") > 1:
        return h                            # bare ipv6 — no port to strip
    return h.split(":")[0]


def _cmd_base(c):
    first = c.strip().split()[0] if c.strip() else ""
    return re.split(r"[/\\]", first)[-1]


def _path_covered(a, r):
    def norm(s):
        return [c for c in re.split(r"[/\\]", s) if c and c != "."]
    if ".." in norm(r):
        return False
    abs_ = lambda s: s.startswith("/") or s.startswith("\\")
    if abs_(a) != abs_(r):
        return False
    ac, rc = norm(a), norm(r)
    return len(ac) <= len(rc) and all(x == rc[i] for i, x in enumerate(ac))


def _table_covered(a, r):
    a, r = a.lower(), r.lower()
    if a.endswith(".*"):
        return r.startswith(a[:-1])         # "schema." prefix
    return a == r


def _literal_allowed(effect, reached, values):
    if effect == "Net":
        return any(_host_part(a) == _host_part(reached) for a in values)
    if effect == "Exec":
        return any(_cmd_base(a) == _cmd_base(reached) for a in values)
    if effect == "Fs":
        return any(_path_covered(a, reached) for a in values)
    if effect == "Db":
        return any(_table_covered(a, reached) for a in values)
    # Unreachable via parse_policy (allow is parser-restricted to the four literal surfaces above),
    # but KEPT deliberately: the Rust (`_ => allow.contains(reached)`) and TS (`default: values.
    # includes(reached)`) matchers carry the identical arm, and this file's contract is a faithful
    # line-for-line port of that shared set; evaluate_policy is also a documented embedder surface
    # (hand-built policy dicts), where an unknown-effect allow must exact-match, never crash.
    return reached in values


def transitive_reason_classes(functions, callgraph, seed=None):
    """fn -> its TRANSITIVE §6.2 reason-class set. `unknownWhy` is direct-only by design (§4: a reason
    names a site in the unit's OWN body), so a unit whose `Unknown` is purely inherited carries none —
    matching a filter against the direct field answers a different question. The gate resolves the
    class over the same reach the `Unknown` effect itself propagates on.

    THE CONTRIBUTION IS AT THE SOURCE, NOT THE JOIN (§6.2 ⟨0.24⟩): a unit with a DIRECT `Unknown` it
    did not name contributes `unresolved` into the DIRECT map here, BEFORE propagation — so a caller
    of both a reasonless unit and a reasoned one accumulates both classes, and the class set only ever
    GROWS as evidence arrives (the monotone-denial property `Reject` is upward-closed in). Gating this
    on absence of the whole reason set instead would be the mirror fabrication the clause names: it
    would charge `unresolved` to a unit whose `Unknown` is correctly classified at its callee.

    `seed`: fn -> class set for nodes that are NOT in `functions` — the `--link` pseudo-nodes, whose
    reasons live in the chained CODE report (scan.link_code_report resolves them there and hands the
    result over). Reach without resolution is BOTH failure modes at once: the linking unit inherits
    the pseudo-node's `Unknown` over the preserved edge, so if the class does not travel with it the
    unit lands on the empty-`classes` arm below — excluded by a filter naming its own class (the
    under-report §6.2 requirement 2 forbids) and charged `unresolved` by one that doesn't (the mirror
    fabrication requirement 3 forbids). Seeds join the DIRECT map, before propagation, like any other
    source-side contribution."""
    direct = {n: set(cs) for n, cs in (seed or {}).items() if cs}
    for f in functions:
        cs = {classify_reason(w) for w in f.get("unknownWhy", [])}
        if "Unknown" in f.get("direct", []) and not f.get("unknownWhy"):
            cs.add("unresolved")
        if cs:
            direct.setdefault(f["fn"], set()).update(cs)
    acc = {n: set(direct.get(n, ())) for n in set(callgraph) | set(direct)}
    changed = True
    while changed:  # least fixpoint of the componentwise join (§4.0), same shape as propagate()
        changed = False
        for n, callees in callgraph.items():
            for c in callees:
                add = acc.get(c)
                if add and not add <= acc[n]:
                    acc[n] |= add
                    changed = True
    return {n: cs for n, cs in acc.items() if cs}


def evaluate_policy(pol, functions, callgraph, incomplete=None, reason_seed=None):
    """The standing §6.2 gate over a report + callgraph. Returns one STRUCTURED violation record
    {rule, fn, effects, detail} per breach (candor-spec §3.3 ⟨0.8⟩ — the --gate-json shape, shared
    with candor-ts policy.mjs): `effects` is the specific effect set the violation concerns per the
    rule's semantics (the denied intersection for 006, the allow rule's effect for 008, [] for the
    009 layer-flow); `detail` is the message BODY (no `[AS-EFF-00x]` prefix — `rule` carries the
    code; render() rebuilds the console line). Empty list = clean.

    `incomplete` (optional): fn -> set of effects whose literal surface is INCOMPLETE (e.g. an
    observed `paths` list truncated at the emit bound). An allowlist over an incomplete surface is
    uncertifiable even when literals are visible — a benign visible literal must not mask the
    dropped remainder (the AS-EFF-008 fail-closed posture, matching the code engines' internal
    masking-incompleteness map).

    `reason_seed` (optional): fn -> reason-class set for callgraph nodes with no row in `functions` —
    the `--link` pseudo-nodes. See transitive_reason_classes; without it the reach crosses the link
    boundary but the CLASS does not, which is an under-report and a fabrication simultaneously."""
    out = []
    incomplete = incomplete or {}
    surfaces = {"Net": "hosts", "Exec": "cmds", "Fs": "paths", "Db": "tables"}
    classes = transitive_reason_classes(functions, callgraph, seed=reason_seed)

    def push(rule, fn, effects, detail, reason_class=()):
        v = {"rule": rule, "fn": fn, "effects": list(effects), "detail": detail}
        if reason_class:
            v["reasonClass"] = list(reason_class)  # §3.3/§6.2 ⟨0.19⟩, omitted when empty
        out.append(v)

    # ⟨0.24⟩ §4 ZERO-MATCH DISCLOSURE. A rule whose SCOPE binds no unit is scored as satisfied, so a
    # one-character typo in an agent name (`deny Exec orchestratr`) turns a failing gate green — and the
    # asymmetry is the tell: a typo'd EFFECT token exits 2 naming the accepted vocabulary, a typo'd
    # SCOPE binds nothing and passes. The remedy is DISCLOSURE, not refusal: a zero-match rule is
    # legitimate when one policy is shared across fleets and an agent exists in only some of them.
    #
    # Four engines gained this at ⟨0.24⟩ and this one did not, while declaring the same spec —
    # conformance PART 32 runs four engines, so nothing caught it.
    scope_hits = {r["raw"]: 0 for r in pol["deny"] if r.get("scope")}
    for f in functions:
        for r in pol["deny"]:
            if r.get("scope") and scope_matches(f["fn"], r["scope"]):
                scope_hits[r["raw"]] = scope_hits.get(r["raw"], 0) + 1
    # ⟨0.27⟩ code-point sorted + deduplicated (Python str sort IS code-point order; the dict keys are
    # unique by construction) — the SPEC §4 `zeroMatch` collation. Stashed module-wide so `run_gate` can
    # put the SAME list on the verdict document without changing `evaluate_policy`'s return shape, which
    # is a documented embedder surface. Safe for the same reason the CLI is: one gate evaluation per run.
    global LAST_ZERO_MATCH
    LAST_ZERO_MATCH = sorted(raw for raw, n in scope_hits.items() if n == 0)
    for raw in LAST_ZERO_MATCH:
        print(f"candor-agents: policy rule matched NO unit — `{raw}`. It was evaluated and bound "
              f"nothing, so it cannot have caught anything. Legitimate when one policy is shared "
              f"across fleets; a typo'd agent name otherwise.", file=sys.stderr)

    for f in functions:
        inferred = f.get("inferred", [])
        for r in pol["deny"]:
            if r["scope"] and not scope_matches(f["fn"], r["scope"]):
                continue
            if not r["effects"]:
                # `pure <scope>` — SPEC §4.0's verb table: it fires iff `S ≠ ∅`, and `S` is the
                # DETERMINED effects, i.e. `inferred` MINUS the `Unknown` marker. `D ≠ ∅` alone
                # (an undischarged blind spot) is AS-EFF-003 disclosure, never an AS-EFF-006
                # violation — conformance PART 16 pins the same fixture under a bare `pure` as
                # PASS four-way, and `unverified` is the verb that exists to surface exactly this.
                # Until ⟨0.24⟩ this engine counted `Unknown` as an effect and failed a fleet whose
                # units were determined-pure but had an uncurated MCP server behind them.
                hits = [e for e in inferred if e != "Unknown"]
            else:
                want = set(r.get("unknownClasses") or ())
                keep = {e for e in r["effects"] if e != "Unknown"}
                if "Unknown" in r["effects"] and "Unknown" in inferred \
                        and reason_class_matches(classes.get(f["fn"]), want):
                    keep.add("Unknown")
                hits = [e for e in inferred if e in keep]
            if hits:
                push("AS-EFF-006", f["fn"], hits,
                     f"`{f['fn']}` performs {{ {', '.join(hits)} }}, forbidden by policy: `{r['raw']}`",
                     reason_class=sorted(classes.get(f["fn"], ())) if "Unknown" in hits else ())
        for r in pol["allow"]:
            if r["scope"] and not scope_matches(f["fn"], r["scope"]):
                continue
            if r["effect"] not in inferred:
                continue
            reached = f.get(surfaces[r["effect"]], [])
            if not reached:
                # The effect is reached but NO literal is visible (e.g. Db has no surface, or a dynamic
                # host) — the surface cannot be certified, so the allowlist can't clear it. Fail closed:
                # a clean pass here would let an invisible forbidden endpoint hide behind the allowlist.
                push("AS-EFF-008", f["fn"], [r["effect"]],
                     f"`{f['fn']}` performs {r['effect']} with no visible literal — "
                     f"the surface cannot be certified: `{r['raw']}`")
            elif r["effect"] in incomplete.get(f["fn"], ()):
                # Literals ARE visible but the surface is INCOMPLETE (truncated) — the invisible
                # remainder could hold the forbidden value, so a clean pass would be fabricated.
                push("AS-EFF-008", f["fn"], [r["effect"]],
                     f"`{f['fn']}` performs {r['effect']} but its literal surface is INCOMPLETE "
                     f"(truncated) — the surface cannot be certified: `{r['raw']}`")
            else:
                bad = [v for v in reached if not _literal_allowed(r["effect"], v, r["values"])]
                if bad:
                    push("AS-EFF-008", f["fn"], [r["effect"]],
                         f"`{f['fn']}` reaches {{ {', '.join(bad)} }} outside the allowlist: `{r['raw']}`")
    # AS-EFF-009: forbid A -> B by reachability over the callgraph. No single effect → effects: [].
    for r in pol["forbid"]:
        for fn in callgraph:
            if not scope_matches(fn, r["from"]):
                continue
            seen, queue, hit = {fn}, [fn], None
            while queue and not hit:
                for c in callgraph.get(queue.pop(), []):
                    if c in seen:
                        continue
                    seen.add(c)
                    if scope_matches(c, r["to"]):
                        hit = c; break
                    queue.append(c)
            if hit:
                push("AS-EFF-009", fn, [],
                     f"`{fn}` reaches into a forbidden layer (via `{hit}`), violating policy: `{r['raw']}`")
    return out


def render(v):
    """The console line for one structured violation: `[AS-EFF-00x] <detail>` — the same record
    --gate-json emits verbatim, so the human and machine surfaces can never disagree."""
    return f"[{v['rule']}] {v['detail']}"


# ⟨0.27⟩ SPEC §4 `zeroMatch` — the raw text of every rule whose SCOPE bound no unit, stashed by the one
# `evaluate_policy` call a run performs and read by `run_gate` so the disclosure reaches the verdict
# document. The stderr lines alone left a machine consumer unable to see that a rule bound nothing —
# the typo'd-scope silent green, one channel over. Empty until a gate evaluates, which is also why a
# refusal (written when the gate never evaluated) can never carry it.
LAST_ZERO_MATCH = []

# ⟨0.24⟩ The policy errors of the last `parse_policy` — [{raw, why, fatal}]. A FATAL entry means the
# policy cannot be honoured AS WRITTEN, so every gate route MUST refuse (exit 2, the unreadable-policy
# posture) rather than enforce the rewritten remainder. Kept beside the parser rather than returned,
# so no caller can read the rules without the errors being reachable.
LAST_POLICY_ERRORS = []


def write_gate_json(path, violations, spec, stdout_is_json=False, zero_match=()):
    """`--gate-json <file>` (spec §3.3 ⟨0.8⟩): write the structured gate verdict
    {spec, ok, violations:[{rule, fn, effects, detail}]} from the SAME violation records that set
    the exit code — a consumer can never see a verdict that disagrees with the gate. Written
    whenever the flag is given (ok:true, [] when no gate is configured). `-` streams the verdict
    to stdout (refused when stdout already carries the §2 report — two JSON documents don't pipe).
    Returns True on success; the CALLER exits 2 on False — an unwritable verdict path must fail
    the run, never silently drop the machine surface a CI consumer is reading."""
    import json
    import sys
    global STREAM_VERDICT_WRITTEN
    doc = {"spec": spec, "ok": not violations, "violations": violations}
    # ⟨0.27⟩ SPEC §4 `zeroMatch` — the same list the stderr lines carry, in the machine channel.
    # Omitted when empty (byte-compatible verdict); never consulted for `ok` or the exit code.
    if zero_match:
        doc["zeroMatch"] = list(zero_match)
    verdict = json.dumps(doc, indent=1)
    if path == "-":
        if stdout_is_json:
            sys.stderr.write("candor-agents: --gate-json - conflicts with --json "
                             "(stdout already carries the report envelope)\n")
            # STDOUT IS ALREADY SPOKEN FOR. The report envelope went out before this, so the entry
            # wrapper must not append its refusal: two concatenated JSON documents parse as neither, and
            # the wrapper was added to stop exactly that outcome. Marking the stream written is what tells
            # it so — without this the fix for an EMPTY stream produced an UNPARSEABLE one.
            STREAM_VERDICT_WRITTEN = True
            return False
        print(verdict)
        STREAM_VERDICT_WRITTEN = True
        return True
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(verdict + "\n")
        return True
    except OSError as e:
        sys.stderr.write(f"candor-agents: could not write --gate-json {path} ({e}) — "
                         f"failing (exit 2), the verdict surface must not vanish silently\n")
        return False


# ONE FLAG, SET AT EVERY WRITE TO THE STREAM. `scan.py`'s entry guarantees the stream carries a document
# on EVERY exit-2 path, which means it must be able to tell "nothing was written" from "a refusal was
# already written here". Two concatenated JSON objects are not a document — a consumer parsing one gets
# "extra data" and no verdict at all, which is a worse answer than the zero bytes the guarantee exists to
# fix. Set here rather than at the exits, so a future write site inherits it by construction.
STREAM_VERDICT_WRITTEN = False


def _write_refusal(gate_json, spec, reason):
    """⟨0.27⟩ The fail-closed refusal document, on whichever sink was named (SPEC §3.1 — the refusal
    document has no exempt cause AND no exempt sink). A file sink already holds the armed placeholder,
    so this replaces it with the SPECIFIC reason; `--gate-json -` is not armed, so without this write
    the stream carried NOTHING on this cause and the consumer was thrown back to scraping stderr."""
    import json
    if gate_json is None:
        return
    refusal = json.dumps({"spec": spec, "ok": False, "refused": True, "reason": reason}, indent=1)
    if gate_json == "-":
        global STREAM_VERDICT_WRITTEN
        print(refusal)
        STREAM_VERDICT_WRITTEN = True
        return
    try:
        with open(gate_json, "w", encoding="utf-8") as fh:
            fh.write(refusal + "\n")
    except OSError:
        pass  # the armed placeholder still holds; the caller's exit is already 2


def run_gate(policy_path, gate_json, functions, callgraph, spec, stdout_is_json=False, incomplete=None,
             reason_seed=None):
    """The standing §6.2 gate + §3.3 verdict — ONE implementation, shared by scan (declared) and
    observe (observed) so the two gate surfaces can never diverge in wording or exit-code contract:
    a set-but-unreadable policy FAILS the run (exit 2) — never a silent gate-pass (that includes a
    set-but-EMPTY $CANDOR_POLICY / a bare config `policy` line: enabled-with-empty fails loud on the
    open, never a silent skip); a violation exits 1. --gate-json (spec §3.3 ⟨0.8⟩) re-emits the SAME
    violation records as the machine verdict — written whenever the flag is given (ok:true, [] with
    no gate configured), and an unwritable verdict path exits 2, never a silent drop. Violations and
    the receipt go to stderr (stdout may already carry the report envelope in --json mode).
    `incomplete` is observe's truncated-literal-surface map and `reason_seed` scan's `--link`
    pseudo-node reason classes (both see evaluate_policy). Returns the process exit code: 0 clean
    (or no gate configured), 1 violation(s), 2 gate-infrastructure failure."""
    import json
    import sys
    violations = []
    if policy_path is not None:
        try:
            ptext = open(policy_path, encoding="utf-8").read()
        except OSError as e:
            reason = f"policy {policy_path} could not be read ({e}) — gate NOT enforced (exit 2)"
            print(f"candor-agents: {reason}", file=sys.stderr)
            # ⟨0.27⟩ the refusal document has no exempt cause AND no exempt sink (SPEC §3.1): a file
            # sink already holds the armed placeholder, but `--gate-json -` is not armed, so without
            # this write the stream carried NOTHING on this cause and the consumer was thrown back to
            # scraping stderr. Writing here also replaces the file placeholder with the specific reason.
            _write_refusal(gate_json, spec, reason)
            return 2
        parsed = parse_policy(ptext)
        # ⟨0.24⟩ A POLICY THAT CANNOT BE HONOURED AS WRITTEN IS REFUSED, NOT REWRITTEN (SPEC §6.2).
        # `parsed` holds what the text WOULD mean if the fatal error were tolerated, and tolerating it
        # is the defect: `deny Frobnicate` used to reach here as a deny of nothing and the run printed
        # `policy ✓` at exit 0. Same posture as the unreadable policy above — refuse before any of the
        # remainder is used as a verdict. There is no composed case to weigh here: this engine has no
        # AS-EFF-005 baseline producer, so every violation it can establish comes from the policy that
        # is being refused, and a bad token establishes nothing from the policy itself (§3.1). Should a
        # non-policy violation producer ever land, this is the site that must consult it.
        fatal = [e for e in LAST_POLICY_ERRORS if e["fatal"]]
        if fatal:
            reason = ("refusing to evaluate a policy that cannot be honoured AS WRITTEN (exit 2, gate "
                      "NOT enforced): " + "; ".join(f"{e['why']}: {e['raw']}" for e in fatal))
            print(f"candor-agents: {reason}", file=sys.stderr)
            _write_refusal(gate_json, spec, reason)
            return 2
        violations = evaluate_policy(parsed, functions, callgraph, incomplete=incomplete,
                                     reason_seed=reason_seed)
        for v in violations:
            print(render(v), file=sys.stderr)  # keep stdout pure JSON in --json mode
    if gate_json is not None:
        if not write_gate_json(gate_json, violations, spec, stdout_is_json=stdout_is_json,
                               zero_match=LAST_ZERO_MATCH):
            return 2
    if policy_path is not None:
        if violations:
            print(f"candor-agents: {len(violations)} policy violation(s)", file=sys.stderr)
            return 1
        print("candor-agents: policy ✓", file=sys.stderr)
    return 0
