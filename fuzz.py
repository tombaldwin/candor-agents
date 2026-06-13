#!/usr/bin/env python3
"""Soundness fuzzer for the fleet scanner — the candor-rust harness's analog (Bet 1, ported).

Generates random fleets that thread a KNOWN effect from a sink agent up through a chain of
delegation forms (the forms that could hide an edge: named delegation, unnamed/CHA, an ambient
agent mid-chain, a curated-MCP sink, an uncurated-MCP sink). Every agent on the chain can
transitively reach the effect, so each must be reported with the effect OR Unknown — a chain agent
reported pure (or omitted) is a SILENT UNDER-REPORT, the bug class this exists to catch.

Also asserts the PRECISION twin on fully-named chains: a pure distractor agent (delegates to
nobody, mentioned by nobody) must stay OUT of the report — catching a CHA leak that would smear
the effect across the fleet.

Run: python3 fuzz.py [N]   (default 40 seeds)
"""
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "scan.py")

SINKS = {
    "net":  ("WebFetch", "Net"),
    "exec": ("Bash", "Exec"),
    "fs":   ("Edit", "Fs"),
    "ipc":  ("SendUserFile", "Ipc"),
    "mcp_curated":   ("mcp__gmail__send", "Net"),
    "mcp_uncurated": ("mcp__shadow__op", "Unknown"),
}
FORMS = ["named", "cha", "ambient"]


def agent_md(name, tools, body, desc="fuzz agent"):
    t = f"tools: {tools}\n" if tools is not None else ""
    return f"---\nname: {name}\ndescription: {desc}\n{t}---\n{body}\n"


def run_seed(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 7)  # chain length (excluding sink)
    names = [f"ag{i:02d}" for i in range(n)]
    sink_kind = rng.choice(list(SINKS))
    sink_tool, sink_eff = SINKS[sink_kind]
    forms = {}

    files = {"sink.md": agent_md("sink", sink_tool, "Performs the work.")}
    all_named = True
    for i, name in enumerate(names):
        callee = names[i + 1] if i + 1 < len(names) else "sink"
        form = rng.choice(FORMS)
        forms[name] = form
        if form == "named":
            files[f"{name}.md"] = agent_md(name, "Agent, Read", f"Delegate to `{callee}` only.")
        elif form == "cha":
            files[f"{name}.md"] = agent_md(name, "Agent", "Delegate as needed.")
            all_named = False
        else:  # ambient: no tools line at all (inherits everything, incl. Agent)
            files[f"{name}.md"] = agent_md(name, None, "Do whatever it takes.")
            all_named = False
    # The precision distractor: pure, delegates to nobody, named by nobody.
    files["zz_bystander.md"] = agent_md("zz_bystander", "TodoWrite", "Watch quietly.")

    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    for f, c in files.items():
        open(os.path.join(adir, f), "w").write(c)
    # SUBTRACTION soundness: sometimes deny a tool OFF the chain (never a SINKS tool, never the
    # Agent/Task edge-makers) — permissions.deny must remove only what it names, so the threaded
    # effect MUST survive. Catches over-subtraction (a deny filter dropping the wrong tool). The
    # pool is disjoint from every SINKS tool, so denying it cannot legitimately cut the chain.
    if rng.random() < 0.4:
        deny_tool = rng.choice(["WebSearch", "Glob", "NotebookEdit", "PushNotification"])
        json.dump({"permissions": {"deny": [deny_tool]}}, open(os.path.join(d, ".claude", "settings.json"), "w"))
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, SCAN, d, "--out", out, "--fleet", "fz"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return [f"seed {seed}: scan failed: {r.stderr.strip()}"]
    rep = json.load(open(f"{out}.fz.Fleet.json"))
    by = {f["fn"]: f for f in rep["functions"]}

    bad = []
    # SOUNDNESS: every chain agent (and main) reaches the sink → effect-or-Unknown, never pure/omitted.
    for name in names + ["sink", "session"]:
        e = by.get(name)
        if e is None:
            bad.append(f"seed {seed}: {name} OMITTED (silent pure; sink={sink_kind}, form={forms.get(name)})")
        elif sink_eff not in e["inferred"] and "Unknown" not in e["inferred"]:
            bad.append(f"seed {seed}: {name} lacks {sink_eff}/Unknown: {e['inferred']} (form={forms.get(name)})")
    # PRECISION (fully-named chains only — CHA/ambient legitimately smear): the bystander stays pure,
    # and chain agents don't pick up effects the chain doesn't have.
    if all_named:
        if "zz_bystander" in by:
            bad.append(f"seed {seed}: bystander leaked into the report: {by['zz_bystander']['inferred']}")
        head = by.get(names[0])
        if head and sink_kind == "net" and "Exec" in head["inferred"]:
            bad.append(f"seed {seed}: named chain head gained Exec from nowhere: {head['inferred']}")
    return bad


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    fails = []
    for seed in range(1, n + 1):
        fails.extend(run_seed(seed))
    for b in fails:
        print(f"  {b}")
    print(f"fuzz: {n - len(set(f.split(':')[0] for f in fails))} seeds passed, "
          f"{len(set(f.split(':')[0] for f in fails))} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
