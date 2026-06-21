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

from candor_agents.scan import SPEC, TOOL_EFFECTS, FS_KIND, PURE_TOOLS, MCP_TABLE, VERSION, bash_cmds, propagate  # one source


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


def classify_tool(name):
    """Tool name -> (effects, fs_kinds, why). The same posture as the static scan: unknown -> Unknown."""
    if name in PURE_TOOLS:
        return set(), set(), None
    if name in TOOL_EFFECTS:
        return set(TOOL_EFFECTS[name]), ({FS_KIND[name]} if name in FS_KIND else set()), None
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else "?"
        if server in MCP_TABLE:
            return set(MCP_TABLE[server]), set(), None
        return {"Unknown"}, set(), f"mcp-uncurated:{server}"
    return {"Unknown"}, set(), f"tool-unknown:{name}"


def surfaces_from(name, inp):
    """Literal surfaces from observed tool inputs — the decidable subset, like every engine."""
    hosts, cmds, paths = set(), set(), set()
    if not isinstance(inp, dict):
        return hosts, cmds, paths
    if name == "Bash" and isinstance(inp.get("command"), str):
        cmds |= bash_cmds(inp["command"])
    if name in ("WebFetch",) and isinstance(inp.get("url"), str):
        h = inp["url"]
        for scheme in ("https://", "http://"):
            if h.startswith(scheme):
                h = h[len(scheme):]
        hosts.add(h.split("/", 1)[0])
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
            msg = e.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    yield b.get("id"), b.get("name") or "?", b.get("input")


def observe(tdir, out_prefix, fleet):
    sessions = sorted(f for f in os.listdir(tdir) if f.endswith(".jsonl"))
    bad = {"lines": 0, "files": 0}

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
            effs, kinds, w = classify_tool(name)
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
            "unitKind": "session" if n == "session" else "agent",  # spec ⟨0.5⟩, informative
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
            entry["paths"] = sorted(p for p in paths_t[n])[:200]
        if n == "session":
            entry["entryPoint"] = True
        functions.append(entry)

    report = {"candor": {"version": VERSION, "toolchain": "claude-code-transcripts", "spec": SPEC},
              "package": fleet, "mode": "observed", "functions": functions}
    rp = f"{out_prefix}.{fleet}.Observed.json"
    cp = f"{out_prefix}.{fleet}.Observed.callgraph.json"
    os.makedirs(os.path.dirname(os.path.abspath(rp)), exist_ok=True)
    json.dump(report, open(rp, "w"), indent=1)
    json.dump({n: sorted(edges.get(n) or []) for n in sorted(set(unit_of_file.values()) | {"session"})},
              open(cp, "w"), indent=1)
    sys.stderr.write(f"candor-agents: observed {len(functions)} effectful unit(s) over "
                     f"{len(sessions)} session(s) + {len(unit_of_file) - len(sessions)} subagent transcript(s) → {rp}\n")
    if bad["lines"] or bad["files"]:
        sys.stderr.write(f"candor-agents: transcript reader skipped {bad['lines']} unparseable line(s) "
                         f"and {bad['files']} unreadable file(s) — the format is Claude Code's internal "
                         f"JSONL; treat the report as best-effort observed coverage\n")
    return rp


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out = ".candor/report"
    target = "."
    tdir_override = None
    fleet_override = None
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
                             f"(usage: observe <dir> [--out <prefix>] [--transcripts <dir>] [--fleet <name>])\n")
            return 2
        else:
            target = args[i]
            i += 1
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
    observe(tdir, out, base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
