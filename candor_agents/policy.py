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

EFFECTS = ["Net", "Fs", "Db", "Exec", "Env", "Clock", "Ipc", "Log", "Rand", "Clipboard"]
ALLOW_EFFECTS = {"Net", "Exec", "Fs", "Db"}  # the four literal surfaces

# §6.2 token separator: ASCII whitespace ONLY (the cross-engine rule — a non-ASCII space stays part
# of its token so the rule reads malformed and is dropped, matching the Rust/Java/TS parsers).
_ASCII_WS = re.compile(r"[ \t\n\v\f\r]+")
_VOCAB = set(EFFECTS)


def parse_policy(text):
    """Parse a §6.2 policy into {'deny':[…], 'allow':[…], 'forbid':[…]}. Malformed rules are dropped
    with a stderr note (the shared parser's behaviour); `pure <scope>` is a `deny` with no effects."""
    import sys
    deny, allow, forbid = [], [], []
    # Normalise CRLF/CR to LF first: a bare-\r (classic-Mac) file would otherwise collapse to one line
    # (\r is also an in-line separator), gluing every later rule into the first and dropping it.
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.split("#", 1)[0].strip(" \t\n\v\f\r")
        if not line:
            continue
        t = _ASCII_WS.split(line)

        def warn(why):
            sys.stderr.write(f"candor-agents: ignoring policy rule ({why}): {line}\n")

        if t[0] == "deny":
            effects, scope = [], ""
            for tok in t[1:]:
                if tok in _VOCAB or tok == "Unknown":
                    effects.append(tok)
                else:
                    scope = tok  # first non-effect token is the scope and ENDS the rule
                    break
            if not effects:
                warn("deny names no known effect"); continue
            deny.append({"effects": sorted(set(effects)), "scope": scope, "raw": line})
        elif t[0] == "pure":
            deny.append({"effects": [], "scope": t[1] if len(t) > 1 else "", "raw": line})
        elif t[0] == "allow":
            if len(t) < 3:
                warn("allow names no values"); continue
            if t[1] not in ALLOW_EFFECTS:
                warn("allow supports only Net hosts / Exec commands / Fs paths / Db tables"); continue
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


def evaluate_policy(pol, functions, callgraph, incomplete=None):
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
    masking-incompleteness map)."""
    out = []
    incomplete = incomplete or {}
    surfaces = {"Net": "hosts", "Exec": "cmds", "Fs": "paths", "Db": "tables"}

    def push(rule, fn, effects, detail):
        out.append({"rule": rule, "fn": fn, "effects": list(effects), "detail": detail})

    for f in functions:
        inferred = f.get("inferred", [])
        for r in pol["deny"]:
            if r["scope"] and not scope_matches(f["fn"], r["scope"]):
                continue
            hits = inferred if not r["effects"] else [e for e in inferred if e in r["effects"]]
            if hits:
                push("AS-EFF-006", f["fn"], hits,
                     f"`{f['fn']}` performs {{ {', '.join(hits)} }}, forbidden by policy: `{r['raw']}`")
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


def write_gate_json(path, violations, spec, stdout_is_json=False):
    """`--gate-json <file>` (spec §3.3 ⟨0.8⟩): write the structured gate verdict
    {spec, ok, violations:[{rule, fn, effects, detail}]} from the SAME violation records that set
    the exit code — a consumer can never see a verdict that disagrees with the gate. Written
    whenever the flag is given (ok:true, [] when no gate is configured). `-` streams the verdict
    to stdout (refused when stdout already carries the §2 report — two JSON documents don't pipe).
    Returns True on success; the CALLER exits 2 on False — an unwritable verdict path must fail
    the run, never silently drop the machine surface a CI consumer is reading."""
    import json
    import sys
    verdict = json.dumps({"spec": spec, "ok": not violations, "violations": violations}, indent=1)
    if path == "-":
        if stdout_is_json:
            sys.stderr.write("candor-agents: --gate-json - conflicts with --json "
                             "(stdout already carries the report envelope)\n")
            return False
        print(verdict)
        return True
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(verdict + "\n")
        return True
    except OSError as e:
        sys.stderr.write(f"candor-agents: could not write --gate-json {path} ({e}) — "
                         f"failing (exit 2), the verdict surface must not vanish silently\n")
        return False


def run_gate(policy_path, gate_json, functions, callgraph, spec, stdout_is_json=False, incomplete=None):
    """The standing §6.2 gate + §3.3 verdict — ONE implementation, shared by scan (declared) and
    observe (observed) so the two gate surfaces can never diverge in wording or exit-code contract:
    a set-but-unreadable policy FAILS the run (exit 2) — never a silent gate-pass (that includes a
    set-but-EMPTY $CANDOR_POLICY / a bare config `policy` line: enabled-with-empty fails loud on the
    open, never a silent skip); a violation exits 1. --gate-json (spec §3.3 ⟨0.8⟩) re-emits the SAME
    violation records as the machine verdict — written whenever the flag is given (ok:true, [] with
    no gate configured), and an unwritable verdict path exits 2, never a silent drop. Violations and
    the receipt go to stderr (stdout may already carry the report envelope in --json mode).
    `incomplete` is observe's truncated-literal-surface map (see evaluate_policy). Returns the
    process exit code: 0 clean (or no gate configured), 1 violation(s), 2 gate-infrastructure failure."""
    import sys
    violations = []
    if policy_path is not None:
        try:
            ptext = open(policy_path, encoding="utf-8").read()
        except OSError as e:
            print(f"candor-agents: policy {policy_path} could not be read ({e}) — gate NOT enforced "
                  f"(exit 2)", file=sys.stderr)
            return 2
        violations = evaluate_policy(parse_policy(ptext), functions, callgraph, incomplete=incomplete)
        for v in violations:
            print(render(v), file=sys.stderr)  # keep stdout pure JSON in --json mode
    if gate_json is not None:
        if not write_gate_json(gate_json, violations, spec, stdout_is_json=stdout_is_json):
            return 2
    if policy_path is not None:
        if violations:
            print(f"candor-agents: {len(violations)} policy violation(s)", file=sys.stderr)
            return 1
        print("candor-agents: policy ✓", file=sys.stderr)
    return 0
