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
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

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
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "fz"],
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


# ── observe/transcript fuzz lane ──────────────────────────────────────────────────────────────────
# The transcript reader parses Claude Code's INTERNAL JSONL — an undocumented, evolving format. A
# malformed line (a bare scalar, a list, a `message` that is a string not an object, plain garbage)
# must be COUNTED and skipped, NEVER crash: a crash aborts observe(), no report is written, and the
# OBSERVED gate silently does not run (the cardinal sin this lane exists to catch). This generates
# transcripts that are mostly malformed and asserts (1) observe exits clean, (2) a report is written,
# (3) the reader's disclosed bad-line count rises with the malformed lines it was fed.
# Lines that MUST raise the disclosed bad-line counter: unparseable text, AND a bare non-dict JSON
# value (`5`/`"x"`/`[…]`) — the exact shapes that crashed `.get` before the isinstance guard.
_BAD_COUNTED = ["5", '"x"', "true", "null", "[1, 2, 3]", "not json at all {", "{unterminated"]
# Parseable dicts of the WRONG shape: legitimately skipped WITHOUT counting (they parsed, they're
# just not tool_use lines). Mixed in to prove the reader doesn't crash on them OR over-count them.
_BAD_UNCOUNTED = ['{"message": "a string, not an object"}', '{"message": 5}',
                  '{"message": {"content": "not a list"}}',
                  '{"message": {"content": [42, "x", {"type": "text"}]}}', '{"other": 1}']
_VALID_USE = '{"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", ' \
             '"input": {"command": "curl https://x"}}]}}'


def run_observe_seed(seed):
    rng = random.Random(10_000 + seed)
    d = tempfile.mkdtemp()
    n_bad = rng.randint(1, 12)
    lines = [rng.choice(_BAD_COUNTED) for _ in range(n_bad)]
    # interleave wrong-shape-but-parseable lines + a few valid tool_use lines: the reader must skip the
    # former WITHOUT counting them, observe the latter, and survive both past the counted-bad lines.
    for _ in range(rng.randint(0, 4)):
        lines.insert(rng.randint(0, len(lines)), rng.choice(_BAD_UNCOUNTED))
    for _ in range(rng.randint(0, 3)):
        lines.insert(rng.randint(0, len(lines)), _VALID_USE)
    rng.shuffle(lines)
    open(os.path.join(d, "session.jsonl"), "w").write("\n".join(lines) + "\n")
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.observe", "--transcripts", d,
                        "--out", out, "--fleet", "fz", d], capture_output=True, text=True)
    bad = []
    if r.returncode != 0:
        bad.append(f"oseed {seed}: observe CRASHED on a malformed transcript: {r.stderr.strip()[:200]}")
        return bad  # nothing else to check if it didn't run
    if not os.path.exists(f"{out}.fz.Observed.json"):
        bad.append(f"oseed {seed}: no report written (the OBSERVED gate would silently not run)")
    # The receipt MUST disclose the unparseable lines — absence of disclosure is the silent under-report.
    m = re.search(r"skipped (\d+) unparseable line", r.stderr)
    skipped = int(m.group(1)) if m else 0
    # EXACT: the wrong-shape-but-parseable lines must NOT inflate the count (they parsed fine) and the
    # counted-bad lines must ALL be disclosed (no silent drop) — both directions are honesty failures.
    if skipped != n_bad:
        bad.append(f"oseed {seed}: reader disclosed {skipped} bad line(s) but {n_bad} were counted-bad "
                   f"(the unparseable-line count must be exact — no silent drop, no over-count)")
    return bad


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    fails = []
    for seed in range(1, n + 1):
        fails.extend(run_seed(seed))
        fails.extend(run_observe_seed(seed))
    for b in fails:
        print(f"  {b}")
    print(f"fuzz: {2 * n - len(set(f.split(':')[0] for f in fails))} seeds passed, "
          f"{len(set(f.split(':')[0] for f in fails))} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
