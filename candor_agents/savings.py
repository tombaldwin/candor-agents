#!/usr/bin/env python3
"""candor-agents savings — a LABELLED estimate of what candor-query saved the agent.

  candor-agents savings [<project-dir>] [--transcript <dir>] [--json]

Counts the agent's candor-query calls in the session transcript(s) — questions it asked candor
instead of re-deriving — and prints a comparison against our published benchmark.

This is a MODEL, not a measurement: we cannot see what the agent would have spent re-deriving the
answer by hand. So the *count* is measured and the *saving* is clearly labelled as an estimate; the
two are never blended, and there is no fake-precise total (everything is "on the order of"). The
benchmark was measured on the BLAST-RADIUS task specifically, so the estimate applies only to the
blast-radius (callers/where) queries — other candor-query calls are counted but not multiplied at
that rate. A tool that sells disclosure-not-fabrication must hold its own ROI number to that standard.

Benchmark (candor.poly.io/agents): one blast-radius query ≈ 24k tokens / 1 tool call / ~8 s; an
unaided agent re-deriving the same answer averages ~17× tokens, ~50× tool calls, ~38× wall-clock.
"""
import json
import os
import re
import sys

from candor_agents.observe import transcript_dir_for, tool_uses

TOK, CALLS = 17, 50          # benchmark multiples
QUERY_TOKENS = 24_000        # one candor query, per the benchmark

# candor-query INVOKED at a command position (start of the command or after a shell separator) — not
# merely mentioned inside an echo/grep/comment/path. Covers `candor-query`, `candor-ts-query`, and
# `npx [-y] candor-ts-query`.
_QUERY = re.compile(r"(?:^|[;&|]\s*)(?:npx\s+(?:-y\s+)?)?candor(?:-ts)?-query\b")
_BLAST = re.compile(r"candor(?:-ts)?-query\s+(?:callers|where)\b")


def _is_query(name, inp):
    if name == "Bash" and isinstance(inp, dict) and isinstance(inp.get("command"), str):
        return bool(_QUERY.search(inp["command"]))
    if isinstance(name, str) and re.search(r"candor.*quer", name, re.I):
        return True  # an MCP candor-query tool, if wired that way
    return False


def _is_blast(name, inp):
    # callers = reverse reachability (the blast radius); where = "what reaches this effect".
    return (name == "Bash" and isinstance(inp, dict) and isinstance(inp.get("command"), str)
            and bool(_QUERY.search(inp["command"])) and bool(_BLAST.search(inp["command"])))


def _count(tdir):
    total = blast = 0
    bad = {"files": 0, "lines": 0}   # tool_uses MUTATES this as a dict — a list would TypeError on a bad line
    for root, _dirs, files in os.walk(tdir):   # walk subagents/ subdirs too, as observe.py does
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            for _id, name, inp in tool_uses(os.path.join(root, f), bad):
                if _is_query(name, inp):
                    total += 1
                    if _is_blast(name, inp):
                        blast += 1
    return total, blast


def _h(n):
    if n >= 1_000_000:
        return f"~{round(n / 1_000_000, 1):g}M"
    if n >= 1_000:
        return f"~{int(round(n / 1_000))}K"
    return f"~{int(n)}"


def main(argv):
    target, tdir, as_json = ".", None, False
    target_set = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            as_json = True
            i += 1
        elif a == "--transcript":
            if i + 1 >= len(argv):
                print("candor-agents: --transcript requires a value", file=sys.stderr)
                return 2
            tdir = argv[i + 1]
            i += 2
        elif a.startswith("--"):
            print(f"candor-agents: unknown flag {a} "
                  f"(usage: savings [<dir>] [--transcript <dir>] [--json])", file=sys.stderr)
            return 2
        elif target_set:
            # a second positional FAILS like scan/drift — it silently replaced the target before
            print(f"candor-agents: unexpected extra argument {a}", file=sys.stderr)
            return 2
        else:
            target = a
            target_set = True
            i += 1
    if tdir is None:
        tdir = transcript_dir_for(target)
    if not tdir or not os.path.isdir(tdir):
        note = f"no transcripts found (looked in {tdir or target})"
        if as_json:
            print(json.dumps({"queries": 0, "note": note}))
        else:
            print(f"candor — {note}.")
            print("  point --transcript at a Claude Code session-transcript directory.")
        return 0
    total, blast = _count(tdir)
    # The benchmark was measured on the blast-radius task, so the estimate is based on the blast-radius
    # queries ONLY; other candor-query calls (show/blindspots/…) are counted but not multiplied at that rate.
    saved_tokens = blast * QUERY_TOKENS * (TOK - 1)
    saved_calls = blast * (CALLS - 1)
    if as_json:
        print(json.dumps({
            "measured": {"queries": total, "blastRadiusQueries": blast},
            "modelled": True,
            "estimate": {"basis": "blastRadiusQueries",
                         "tokensSaved": _h(saved_tokens), "toolCallsSaved": _h(saved_calls)},
            "benchmark": {"tokens": TOK, "toolCalls": CALLS, "queryTokens": QUERY_TOKENS,
                          "source": "candor.poly.io/agents"},
            "note": "model, not a measurement — applies only to blast-radius (callers/where) queries",
        }, sort_keys=True))
        return 0
    if total == 0:
        print(f"candor — no candor-query calls in {tdir} (nothing to estimate).")
        return 0
    print(f"candor-query usage (measured): {total} call(s), {blast} blast-radius (callers/where).")
    print()
    if blast == 0:
        print("No blast-radius (callers/where) queries this session — the benchmark only covers those,")
        print("so there's nothing to model. (The other queries are counted above.)")
        return 0
    print("Estimated saving — model, not measured (we can't see what you didn't spend):")
    print(f"  on our benchmark an unaided agent re-derives each blast-radius answer at ~{TOK}× the")
    print(f"  tokens and ~{CALLS}× the tool calls of the one candor query — so {blast} such call(s) is")
    print(f"  on the order of {_h(saved_tokens)} tokens and {_h(saved_calls)} tool calls of hand-tracing avoided.")
    print("  basis: candor.poly.io/agents (one query ≈ 24k tokens / 1 tool call / ~8 s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
