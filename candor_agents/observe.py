#!/usr/bin/env python3
"""candor-agents observe — the OBSERVED fleet: what the agents actually did.

scan.py reads the fleet's *declarations* (what agents MAY do — the capability surface). This reads
the session transcripts (what they DID): units are agent TYPES (all instances of `general-purpose`
aggregate, matching the static scan's units) plus the `session` root; edges are observed delegation
(a subagent's meta `toolUseId` resolved to the transcript that issued it); effects are the §1
vocabulary over observed tool_use events, classified by the same table as the static scan — an
unknown tool or uncurated MCP server reads `Unknown`, never silence. Literal surfaces come from
observed tool INPUTS (`Bash` commands -> cmds, file paths -> paths, fetched URLs -> hosts).

Honesty notes: the transcript format is Claude Code's internal JSONL — this reader is best-effort
and DISCLOSES what it could not parse (count in the receipt); a `drift` between declarations and
observations is computed by cli.py's drift command (granted-but-never-used = least-privilege
advice, the AS-EFF-002 analog).
"""
import json
import os
import re
import sys
import urllib.parse

from candor_agents.scan import (SPEC, VERSION, bash_cmds, classify, config_policy, propagate,
                                read_mcp)  # one source

# The emit bound on a unit's observed `paths` literal surface. A unit past it is TRUNCATED — which
# must be (a) disclosed on the receipt and (b) fail-closed under an `allow Fs` gate: the dropped
# remainder could hold the disallowed path, so certifying the visible prefix would let it pass clean
# (the AS-EFF-008 masking evasion). Sorted-lexicographic truncation is a bound, never a sample.
PATHS_CAP = 200


def transcript_dir_for(path):
    """A project dir maps to ~/.claude/projects/<slug>; a dir already holding *.jsonl is used as-is."""
    if any(f.endswith(".jsonl") for f in os.listdir(path)) if os.path.isdir(path) else False:
        return path
    # Claude Code's project slug flattens EVERY non-alphanumeric to `-` (not only `/` and `.`), so a
    # path with `_`, `@`, a space etc. (e.g. `~/git/my_app`, the public-sweep `Updog_restore`) mapped
    # to a slug that never exists — observe/drift silently dead for those projects. Match its rule.
    slug = re.sub(r"[^a-zA-Z0-9]", "-", os.path.abspath(path))
    cand = os.path.expanduser(os.path.join("~/.claude/projects", slug))
    return cand if os.path.isdir(cand) else None


def classify_tool(name, declared_mcp=None, declared_bad=None):
    """One observed tool name -> (effects, fs_kinds, why). THE CLASSIFIER IS `scan.classify` — this is
    an adapter onto its one-tool case, not a second table walk.

    The module docstring has always claimed observed effects are "classified by the same table as the
    static scan". That was true of the TABLES and false of the LADDER: this function knew only
    `MCP_TABLE`, so the `.mcp.json` `candorEffects` tier `scan.read_mcp`/`scan.classify` own — the
    documented DECLARING.md convention, the project's own claim about its server — did not exist here.
    Measured on a fleet whose `.mcp.json` declares `acme: ["Net"]` and whose transcript shows one
    `mcp__acme__query` use:

      scan     --policy 'deny Net'   →  AS-EFF-006 on the unit, exit 1
      observe  --policy 'deny Net'   →  `policy ✓`, exit 0

    The OBSERVED gate — the half that answers "what did the fleet actually DO" — passed clean over the
    exact use the DECLARED gate fails on, because the server read `Unknown` instead of `Net`. That is
    a silent under-report of a gate surface, and it came with a FALSE disclosure beside it
    (`mcp-uncurated:acme` on a server the project HAD curated) plus a false `drift --strict` anomaly:
    every project that followed DECLARING.md got an OBSERVED-OUTSIDE-DECLARATION for doing so.

    Delegating also inherits the two tiers this copy could never have grown on its own: the ⟨0.24⟩
    `mcp-decl-invalid:<server>:<effect>` voiding of an out-of-vocabulary declaration, and §6.1's
    `refine_llm` (a declared `Llm` co-emits `Net`), which `read_mcp` applies at the source."""
    effs, fs, why = classify([name], None, declared_mcp, declared_bad)
    return effs, fs, (sorted(why)[0] if why else None)


def surfaces_from(name, inp):
    """Literal surfaces from observed tool inputs — the decidable subset, like every engine."""
    hosts, cmds, paths = set(), set(), set()
    if not isinstance(inp, dict):
        return hosts, cmds, paths
    if name == "Bash" and isinstance(inp.get("command"), str):
        cmds |= bash_cmds(inp["command"])
    if name in ("WebFetch",) and isinstance(inp.get("url"), str):
        # urlsplit handles every URL form (`user:pass@host`, `host:port`, scheme-relative `//host/…`)
        # — a naive strip-scheme-then-split misparsed all three, manufacturing a bogus host. `.hostname`
        # is the registered name only (no userinfo/port). None (a non-URL, a relative path) is SKIPPED:
        # under-report, never fabricate a host from a string that has none.
        host = urllib.parse.urlsplit(inp["url"]).hostname
        if host:
            hosts.add(host)
    for key in ("file_path", "path", "notebook_path"):
        if isinstance(inp.get(key), str) and inp[key].startswith("/"):
            paths.add(inp[key])
    return hosts, cmds, paths


def tool_uses(jsonl_path, bad):
    """Yield (tool_use_id, name, input) from one transcript; count unparseable lines."""
    try:
        fh = open(jsonl_path, encoding="utf-8")
    except OSError:
        bad["files"] += 1
        return
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except Exception:
                bad["lines"] += 1
                continue
            # Valid JSON of the WRONG shape (a bare scalar `5`/`"x"`, a list, or a `message` that is a
            # string not an object) must be COUNTED and skipped, never crash on `.get` — one malformed
            # line otherwise aborted the whole observe(), so no report was written and the OBSERVED gate
            # silently did not run (the cardinal sin). Mirrors the stats.py reader's isinstance guard.
            if not isinstance(e, dict):
                bad["lines"] += 1
                continue
            msg = e.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    yield b.get("id"), b.get("name") or "?", b.get("input")


def print_version():
    """`--version`/`-V` (spec §3.3): the build + the candor-spec version it speaks, offline."""
    print(f"candor-agents {VERSION} (candor-spec {SPEC})")
    print("upgrade: pipx upgrade candor-agents  (or: pip install -U candor-agents)")


_HELP = """candor-agents observe — OBSERVED: what the fleet DID, from session transcripts.

Reads the project's Claude Code transcripts (~/.claude/projects/<slug>/) and writes
a report in the same shape scan emits — the two halves drift compares.

USAGE
  candor-agents observe <project-dir> [options]

OPTIONS
  --out <prefix>            write <prefix>.<fleet>.Observed.json + a .callgraph.json sidecar
                            (default: .candor/report)
  --json                    emit the report envelope as JSON to stdout (human/progress
                            goes to stderr)
  --policy <file>           evaluate a policy file: exit 1 on a violation, 2 if unreadable;
                            honours $CANDOR_POLICY, then a discovered .candor/config `policy`
  --gate-json <file>        write the structured gate verdict {spec, ok, violations} as JSON
                            (`-` = stdout); written whenever given, exit code unchanged
  --transcripts <dir>       read transcripts from here instead of the derived ~/.claude/projects slug
  --fleet <name>            name the fleet (default: the project dir's basename)
  -V, --version             print the installed version (offline)
  -h, --help                this help

EXAMPLES
  candor-agents observe .
  candor-agents observe . --transcripts ./sessions --json

Docs: candor.poly.io   ·   Verify an install: candor doctor"""


def observe(tdir, out_prefix, fleet, as_json=False, project_dir=None):
    sessions = sorted(f for f in os.listdir(tdir) if f.endswith(".jsonl"))
    bad = {"lines": 0, "files": 0}
    # The project's `.mcp.json` declaration tier, read by the ONE reader scan uses (see classify_tool).
    # `project_dir` is the fleet the transcripts belong to; when observe is pointed straight at a
    # transcript directory there is no manifest and read_mcp returns empty — the pre-declaration
    # behaviour, unchanged.
    _srv, declared_mcp, declared_bad = read_mcp(project_dir) if project_dir else ([], {}, {})

    # unit name per transcript file: the main session(s) -> "session"; a subagent -> its agentType
    unit_of_file = {}
    meta_parent_tooluse = {}  # subagent file -> the parent's tool_use id (the delegation edge key)
    for s in sessions:
        unit_of_file[os.path.join(tdir, s)] = "session"
    # Subagent transcripts live in `subagents/` dirs; a subagent that itself spawns one NESTS them
    # (`…/subagents/x/subagents/y.jsonl`). Walk for EVERY `subagents` dir at any depth so a nested
    # subagent's effects are observed, not silently dropped (the cardinal sin) — a one-level glob
    # missed them. os.walk visits each dir once, so no transcript is processed twice.
    subdirs = [dp for dp, _dn, _fn in os.walk(tdir) if os.path.basename(dp) == "subagents"]
    for sd in subdirs:
        for f in sorted(os.listdir(sd)):
            if not f.endswith(".jsonl"):
                continue
            p = os.path.join(sd, f)
            unit = "subagent"
            meta = p[:-6] + ".meta.json"
            # A MISSING sidecar is the normal optional case (the unit falls back to `subagent`, which
            # drift then flags as undeclared) — only a sidecar that EXISTS but won't parse counts as an
            # "unreadable file"; conflating the two over-reported the best-effort-coverage receipt.
            if os.path.exists(meta):
                try:
                    m = json.load(open(meta))
                    unit = m.get("agentType") or "subagent"
                    if m.get("toolUseId"):
                        meta_parent_tooluse[p] = m["toolUseId"]
                except Exception:
                    bad["files"] += 1
            unit_of_file[p] = unit

    direct, fs_kinds, why = {}, {}, {}
    hosts, cmds, paths = {}, {}, {}
    counts = {}
    tooluse_owner = {}  # tool_use id -> unit that issued it (resolves delegation parents)
    for p, unit in unit_of_file.items():
        for tu_id, name, inp in tool_uses(p, bad):
            if tu_id:
                tooluse_owner[tu_id] = unit
            counts[unit] = counts.get(unit, 0) + 1
            effs, kinds, w = classify_tool(name, declared_mcp, declared_bad)
            direct.setdefault(unit, set()).update(effs)
            fs_kinds.setdefault(unit, set()).update(kinds)
            if w:
                why.setdefault(unit, set()).add(w)
            h, c, pa = surfaces_from(name, inp)
            hosts.setdefault(unit, set()).update(h)
            cmds.setdefault(unit, set()).update(c)
            paths.setdefault(unit, set()).update(pa)

    # delegation edges: child unit's spawning tool_use belongs to the parent unit
    edges = {u: set() for u in set(unit_of_file.values()) | {"session"}}
    for p, unit in unit_of_file.items():
        tu = meta_parent_tooluse.get(p)
        if unit == "session":
            continue
        parent = tooluse_owner.get(tu, "session")
        if parent != unit:
            edges.setdefault(parent, set()).add(unit)

    # fixpoint: observed effects + surfaces propagate up the delegation graph (one shared propagate())
    inferred = propagate(direct, edges)
    hosts_t, cmds_t, paths_t = propagate(hosts, edges), propagate(cmds, edges), propagate(paths, edges)
    fs_t = propagate(fs_kinds, edges)

    functions = []
    incomplete = {}   # unit -> effects whose literal surface is INCOMPLETE (internal, like the code engines)
    truncated = []    # (unit, total) for the receipt disclosure
    for n in sorted(set(unit_of_file.values()) | {"session"}):
        inf = inferred.get(n) or set()
        if not inf:
            continue
        entry = {
            "fn": n,
            "loc": f"{tdir}:observed:{counts.get(n, 0)} tool uses",
            "inferred": sorted(inf),
            "direct": sorted(direct.get(n) or []),
            "declared": [], "undeclared": [], "overdeclared": [],
            "unresolved": "Unknown" in inf,
            "unitKind": "session" if n == "session" else "agent",  # spec ⟨0.7⟩, informative
            "hash": f"{fleet}#{n}",
            "calls": sorted(edges.get(n) or []),
        }
        if fs_t.get(n):
            entry["fs"] = sorted(fs_t[n])
        if why.get(n):
            entry["unknownWhy"] = sorted(why[n])
        if hosts_t.get(n):
            entry["hosts"] = sorted(hosts_t[n])
        if cmds_t.get(n):
            entry["cmds"] = sorted(cmds_t[n])
        if paths_t.get(n):
            allp = sorted(paths_t[n])
            entry["paths"] = allp[:PATHS_CAP]
            if len(allp) > PATHS_CAP:
                # Truncation makes the Fs surface INCOMPLETE: disclosed below, and fed to the gate so
                # an `allow Fs` over this unit reads uncertifiable — a disallowed path sorted past the
                # cap must not pass clean behind the visible (allowed) prefix.
                incomplete.setdefault(n, set()).add("Fs")
                truncated.append((n, len(allp)))
        if n == "session":
            entry["entryPoint"] = True
        functions.append(entry)

    report = {"candor": {"version": VERSION, "toolchain": "claude-code-transcripts", "spec": SPEC},
              "package": fleet, "mode": "observed", "functions": functions}
    callgraph = {n: sorted(edges.get(n) or []) for n in sorted(set(unit_of_file.values()) | {"session"})}
    rp = f"{out_prefix}.{fleet}.Observed.json"
    cp = f"{out_prefix}.{fleet}.Observed.callgraph.json"
    if as_json:
        # --json: stdout MUST be pure JSON (the §2 envelope); the receipt/progress stays on stderr (it
        # already does), so a `observe --json | jq` pipe never breaks. No files written.
        print(json.dumps(report, indent=1))
        sys.stderr.write(f"candor-agents: observed {len(functions)} effectful unit(s) over "
                         f"{len(sessions)} session(s) + {len(unit_of_file) - len(sessions)} subagent transcript(s) → stdout (--json)\n")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(rp)), exist_ok=True)
        json.dump(report, open(rp, "w"), indent=1)
        json.dump(callgraph, open(cp, "w"), indent=1)
        sys.stderr.write(f"candor-agents: observed {len(functions)} effectful unit(s) over "
                         f"{len(sessions)} session(s) + {len(unit_of_file) - len(sessions)} subagent transcript(s) → {rp}\n")
    if bad["lines"] or bad["files"]:
        sys.stderr.write(f"candor-agents: transcript reader skipped {bad['lines']} unparseable line(s) "
                         f"and {bad['files']} unreadable file(s) — the format is Claude Code's internal "
                         f"JSONL; treat the report as best-effort observed coverage\n")
    if truncated:
        sys.stderr.write(f"candor-agents: observed `paths` TRUNCATED at {PATHS_CAP} for "
                         f"{', '.join(f'{n} ({total} paths)' for n, total in truncated)} — the Fs "
                         f"literal surface is INCOMPLETE; an `allow Fs` over these units fails closed "
                         f"(uncertifiable)\n")
    return report, callgraph, incomplete


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # `-V`/`--version` and `-h`/`--help` are print-and-exit MODES, routed BEFORE the arg walk so a
    # single-dash `-h`/`-V` is never captured as the (positional) transcript target below.
    if "--version" in args or "-V" in args:
        print_version()
        return 0
    if "--help" in args or "-h" in args:
        sys.stderr.write(_HELP + "\n")
        return 0
    out = ".candor/report"
    target = "."
    target_set = False
    tdir_override = None
    fleet_override = None
    as_json = False
    gate_json = None
    policy_path = os.environ.get("CANDOR_POLICY")  # the flag (below) overrides this; config is the floor
    i = 0
    # A value-taking flag with a missing/flag-shaped value FAILS (never silently consuming the next
    # flag or falling back to a default) — the gateless-ignore class the unknown-flag work forbids.
    def value(flag):
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            sys.stderr.write(f"candor-agents: {flag} requires a value\n")
            return None
        return args[i + 1]
    while i < len(args):
        if args[i] == "--out":
            v = value("--out")
            if v is None:
                return 2
            out = v; i += 2
        elif args[i] == "--json":
            as_json = True; i += 1
        elif args[i] == "--policy":
            v = value("--policy")
            if v is None:
                return 2
            policy_path = v; i += 2
        elif args[i] == "--gate-json":
            v = value("--gate-json")
            if v is None:
                return 2
            gate_json = v; i += 2
        elif args[i] == "--transcripts":
            v = value("--transcripts")
            if v is None:
                return 2
            tdir_override = v; i += 2
        elif args[i] == "--fleet":
            v = value("--fleet")
            if v is None:
                return 2
            fleet_override = v; i += 2
        elif args[i].startswith("--"):
            sys.stderr.write(f"candor-agents: unknown flag {args[i]} "
                             f"(usage: observe <dir> [--out <prefix>] [--json] [--policy <file>] "
                             f"[--gate-json <file>] [--transcripts <dir>] [--fleet <name>])\n")
            return 2
        elif target_set:
            # A second positional FAILS like scan/drift — it silently REPLACED the target before
            # (`observe a b` analyzed b and dropped a with exit 0, the positional-swallow class).
            sys.stderr.write(f"candor-agents: unexpected extra argument {args[i]}\n")
            return 2
        else:
            target = args[i]
            target_set = True
            i += 1
    # ⟨0.28⟩ `--json` BESIDE `--gate-json -`: a report and a verdict cannot share one stream. This was
    # refused ONLY inside write_gate_json — AFTER observe() had already written the full §2 report
    # envelope to stdout (the --json path prints unconditionally, before the gate ever runs), so a
    # consumer saw exit 2 (a REFUSAL code) with a complete, successfully-parsed report already sitting
    # on the stream. scan.py decided this BEFORE producing any output; observe.py did not, so the two
    # routes disagreed about whether a "refused" run may still have printed its envelope (the §3.1
    # posture — a refusal document has no exempt cause and no exempt sink — extends to the report
    # stream itself, not just the verdict sink). Decided here, before target/transcript resolution,
    # so nothing is written to stdout on this path.
    if gate_json == "-" and as_json:
        sys.stderr.write("candor-agents: --json and --gate-json - both name STDOUT — refusing (exit 2). "
                         "`--json` writes the REPORT there and `--gate-json -` the VERDICT, so this "
                         "would put two JSON documents on one stream and a consumer parsing it gets "
                         "neither. Send one to a file, or run observe twice.\n")
        from candor_agents import policy as _policy
        _policy.STREAM_VERDICT_WRITTEN = True
        print(json.dumps({"spec": SPEC, "ok": False, "refused": True,
                          "reason": "--json and --gate-json - both name stdout — a report and a "
                                    "verdict cannot share one stream"}, indent=1))
        return 2
    # `.candor/config` (spec §3.4): anchored to the PROJECT target (the fleet whose sessions these
    # are), loaded before observing so a configured-but-unusable config fails up front and a repo
    # migrating its wiring from $CANDOR_POLICY to the checked-in config keeps its OBSERVED gate too.
    # drift's internal runs are gate-free (see scan.py / cli._run — the comparison must not abort).
    if os.environ.get("_CANDOR_AGENTS_NO_GATE") == "1":
        policy_path = None
    else:
        policy_path = config_policy(policy_path, target)
    tdir = tdir_override or transcript_dir_for(target)
    if not tdir:
        sys.stderr.write(f"candor-agents: no transcripts found for {target} "
                         f"(expected *.jsonl there, or ~/.claude/projects/<slug>/ to exist)\n")
        return 2
    # An explicit --transcripts override bypasses transcript_dir_for's existence check, so a path that
    # doesn't exist or is a file would crash in observe()'s os.listdir. Fail clean instead.
    if not os.path.isdir(tdir):
        sys.stderr.write(f"candor-agents: transcripts path {tdir} is not a directory\n")
        return 2
    # the fleet name: an explicit --fleet (drift passes it so both halves agree), else the project
    # dir when it's real, else the transcript dir's slug — the cross-project sweep produced reports
    # named after /dev/null otherwise.
    if fleet_override:
        base = fleet_override
    else:
        base = os.path.basename(os.path.abspath(target)).lstrip("-")
        if (not os.path.isdir(target)) or base in ("", "null", "dev"):
            base = os.path.basename(os.path.abspath(tdir)).lstrip("-") or "fleet"
    # `target` is the PROJECT (the fleet whose sessions these are) even when --transcripts points the
    # reader elsewhere — that is where the `.mcp.json` declarations live, and it is what drift passes.
    report, callgraph, incomplete = observe(tdir, out, base, as_json=as_json,
                                            project_dir=target if os.path.isdir(target) else None)

    # ── the standing §6.2 gate (--policy / $CANDOR_POLICY / config `policy`) over the OBSERVED ────
    # report, via the ONE shared run_gate() — scan and observe must never diverge in wording or
    # exit-code contract. `incomplete` feeds the truncated-Fs fail-closed posture (see observe()).
    from candor_agents import policy as _policy
    return _policy.run_gate(policy_path, gate_json, report["functions"], callgraph, SPEC,
                            stdout_is_json=as_json, incomplete=incomplete)


def _main_streaming_verdict():
    """`observe` is a first-class gate surface and had NONE of the §3.3.1 sink layer.

    `--policy` and `--gate-json` are in its CLI, `cli._run` reaches it, and until now it did not arm the
    sink, did not guard a sink that names an input, did not implement the ⟨0.28⟩ duplicate rule, and had
    no stream guarantee. Measured: `observe … --gate-json A --gate-json B` on a FIRING gate exited 1,
    wrote red to B and left A holding a pre-seeded `{"ok": true}`; `--gate-json <policy>` overwrote the
    policy; and any exit-2 cause with `--gate-json -` gave zero bytes.

    Every one of those was fixed in `scan.py` and nowhere else — the sibling route, one file across. The
    shared helpers live in scan.py precisely so this cannot drift again.
    """
    from candor_agents import policy as _policy
    from candor_agents.scan import (arm_gate_json, _prescan_sink_and_inputs, _all_gate_sinks,
                                    _distinct_gate_sinks, refuse_duplicate_gate_sinks, SPEC)
    argv = sys.argv[1:]
    gate, _pol = _prescan_sink_and_inputs(argv)
    named = _distinct_gate_sinks(_all_gate_sinks(argv))
    if gate:
        rc = refuse_duplicate_gate_sinks(named, _pol)
        if rc is not None:
            return rc
        arm_gate_json(gate)
    try:
        code = main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    if code == 2 and gate == "-" and not _policy.STREAM_VERDICT_WRITTEN:
        print(json.dumps({"spec": SPEC, "ok": False, "refused": True,
                          "reason": "the gate did not complete — this run exited before a verdict could "
                                    "be decided; see stderr for the cause"}, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(_main_streaming_verdict())
