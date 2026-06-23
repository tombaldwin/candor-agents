#!/usr/bin/env python3
"""candor-agents stats — measured edit-time gate activity from the candor activity log.

  candor-agents stats [<project-dir>] [--log <path>] [--session <id>] [--since <iso>] [--json]

Reads `.candor/activity.jsonl` (one record per agent turn, written by the Claude Code stop hook —
see integrations/claude-code/) and reports what candor actually DID: edits checked, blocks, policy
violations caught, effects introduced, files touched. Every field is directly COUNTED from the log
— nothing modelled or estimated.

A token/time "savings" estimate is deliberately NOT here. That is a separate, clearly-labelled
model, and it needs the agent's candor-query usage (what it asked candor instead of re-deriving),
not this edit-time gate log — see FEEDBACK-SPEC.md §C / P4. Keeping the two apart is the point:
measured stays measured.
"""
import json
import os
import sys
from collections import Counter


def _load(path, session, since):
    """Return the records (filtered), or None if the log doesn't exist. Corrupt lines are skipped,
    never fatal — a stats reader must not fall over on a half-written tail line."""
    try:
        f = open(path)
    except FileNotFoundError:
        return None
    recs = []
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if session is not None and r.get("sessionId") != session:
                continue
            if since is not None and (r.get("ts") or "") < since:
                continue
            recs.append(r)
    return recs


def _summary(recs):
    verdict = Counter(r.get("verdict") for r in recs)
    viol = Counter()
    effects, files, sessions = set(), set(), set()
    max_blast = unknowns_max = candor_ms = 0
    introduced_turns = 0
    ts = sorted(r["ts"] for r in recs if r.get("ts"))
    for r in recs:
        for v in r.get("violations") or []:
            viol[v] += 1
        effects.update(r.get("gained") or [])
        files.update(r.get("edited") or [])
        if r.get("sessionId"):
            sessions.add(r["sessionId"])
        b = r.get("blastRadius") or 0
        if isinstance(b, int):
            max_blast = max(max_blast, b)
        if (r.get("gained") or []) or (isinstance(b, int) and b > 0):
            introduced_turns += 1
        u = r.get("unknowns")
        if isinstance(u, int):
            unknowns_max = max(unknowns_max, u)
        m = r.get("reviewMs")
        if isinstance(m, int):
            candor_ms += m
    return {
        "unknownsMax": unknowns_max,
        "candorMs": candor_ms,
        "turns": len(recs),
        "clean": verdict.get("clean", 0),
        "blocked": verdict.get("blocked", 0),
        "setup": verdict.get("setup", 0),
        "violations": dict(viol),
        "effectsIntroduced": sorted(effects),
        "turnsIntroducingEffects": introduced_turns,
        "largestBlastRadius": max_blast,
        "filesTouched": len(files),
        "sessions": len(sessions),
        "span": [ts[0], ts[-1]] if ts else None,
    }


def _print_human(s, path):
    print(f"candor — gate activity ({path})")
    span = s["span"]
    sess = f" · {s['sessions']} session(s)" if s["sessions"] else ""
    print(f"  span: {span[0]} → {span[1]}{sess}" if span else f"  span: —{sess}")
    print(f"  {s['turns']} edits checked — {s['clean']} clean, {s['blocked']} blocked, {s['setup']} setup")
    if s["violations"]:
        vs = ", ".join(f"{k} ×{n}" for k, n in sorted(s["violations"].items()))
        print(f"  blocked by policy: {vs}")
    if s["effectsIntroduced"]:
        print(f"  effects introduced this period: {', '.join(s['effectsIntroduced'])}")
    if s["largestBlastRadius"]:
        print(f"  largest blast radius seen: {s['largestBlastRadius']} function(s)")
    if s.get("unknownsMax"):
        print(f"  Unknowns disclosed (max in a turn): {s['unknownsMax']}")
    if s["filesTouched"]:
        print(f"  files touched: {s['filesTouched']}")
    if s.get("candorMs"):
        print(f"  candor's own time: {s['candorMs'] / 1000:.1f}s across {s['turns']} checks")


def main(argv):
    path = target = session = since = None
    target = "."
    as_json = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            as_json = True
            i += 1
        elif a in ("--log", "--session", "--since"):
            if i + 1 >= len(argv):
                print(f"candor-agents: {a} requires a value", file=sys.stderr)
                return 2
            val = argv[i + 1]
            if a == "--log":
                path = val
            elif a == "--session":
                session = val
            else:
                since = val
            i += 2
        elif a.startswith("--"):
            print(f"candor-agents: unknown flag {a} (usage: stats [<dir>] [--log <path>] "
                  f"[--session <id>] [--since <iso>] [--json])", file=sys.stderr)
            return 2
        else:
            target = a
            i += 1
    if path is None:
        path = os.path.join(target, ".candor", "activity.jsonl")
    recs = _load(path, session, since)
    if recs is None:
        if as_json:
            print(json.dumps({"turns": 0, "log": path, "exists": False}))
        else:
            print(f"candor — no activity log at {path}.")
            print("  (the Claude Code stop hook writes one per turn; see integrations/claude-code/.)")
        return 0
    s = _summary(recs)
    if as_json:
        out = dict(s)
        out["log"] = path
        print(json.dumps(out, sort_keys=True))
    elif s["turns"] == 0:
        print(f"candor — no matching gate activity in {path}.")
    else:
        _print_human(s, path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
