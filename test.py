#!/usr/bin/env python3
"""Behavioral tests for the fleet scanner — small synthetic fleets, asserted end to end.

Covers the mechanics DESIGN.md claims: the delegation ladder (named narrowing > CHA), ambient
authority, the curated-MCP vs Unknown split, transitive propagation, pure-unit omission (report)
vs presence (sidecar), the envelope, fs detail, and — when the candor-rust binary is present —
that the UNMODIFIED candor-query answers where/callers/whatif over the emitted report.

Run: python3 test.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

# The SPEC contract version, DERIVED — same rule as `_expect_hdr` below and for the same reason. Four
# assertions hardcoded "0.26" and all four broke on the 0.27 bump, in the file whose own comment says not
# to do this. A version-coupled assertion is not a one-off: it is a class, and the fix is to source the
# value rather than to re-edit the literal every release.
_SPEC = __import__("candor_agents.scan", fromlist=["SPEC"]).SPEC
HERE = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def agent(name, tools, body="", desc="d"):
    t = f"tools: {tools}\n" if tools is not None else ""
    return f"---\nname: {name}\ndescription: {desc}\n{t}---\n{body}\n"


def scan(files, mcp=None):
    """Write a fleet into a tempdir, scan it, return (report_dict, callgraph_dict)."""
    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    for fname, content in files.items():
        open(os.path.join(adir, fname), "w").write(content)
    if mcp is not None:
        json.dump({"mcpServers": {s: {} for s in mcp}}, open(os.path.join(d, ".mcp.json"), "w"))
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "t"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return (json.load(open(f"{out}.t.Fleet.json")), json.load(open(f"{out}.t.Fleet.callgraph.json")))


def entry(rep, fn):
    return next((f for f in rep["functions"] if f["fn"] == fn), None)


# ── 1. named-delegation narrowing (the devirt analog) ────────────────────────────────────────────
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="Spawn the `worker` for everything."),
    "worker.md": agent("worker", "WebFetch"),
    "bystander.md": agent("bystander", "WebSearch"),
})
check("named delegation narrows edges", cg["boss"] == ["worker"], f"got {cg['boss']}")
# Bare `Agent`: the edge to worker is precise (no bystander smear), but the spawn target is unprovable —
# so the reach is worker's Net PLUS a disclosed Unknown residual (can spawn an unmentioned agent). The
# narrowing stays precise (no FABRICATED concrete effect); Unknown just blocks a false certification.
check("narrowed boss inherits worker's Net + a disclosed spawn residual",
      entry(rep, "boss")["inferred"] == ["Net", "Unknown"],
      f"got {entry(rep, 'boss')['inferred']}")

# name at the very START of the body (the regex-anchor case)
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="worker is your only delegate."),
    "worker.md": agent("worker", "WebFetch"),
    "other.md": agent("other", "Bash"),
})
check("start-of-body name still narrows", cg["boss"] == ["worker"], f"got {cg['boss']}")

# PUNCTUATION-adjacent names: a delegate referenced as `name:` or `(name)` must still be narrowed-to
# (the earlier delimiter class missed these). But `.` and `/` are NAME-CONTINUATION chars — a name in a
# PATH/identifier is NOT a mention (else `src/build.rs` fabricates a `build` delegation edge — review find).
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="First security-auditor: audits, then (reviewer) reviews. Use helper here."),
    "security-auditor.md": agent("security-auditor", "WebFetch"),  # Net — followed by ':'
    "reviewer.md": agent("reviewer", "Bash"),                       # Exec — wrapped in '()'
    "helper.md": agent("helper", "Read"),                           # Fs — space-delimited
    "ignored.md": agent("ignored", "WebSearch"),                    # not mentioned → excluded
})
check("punctuation-adjacent names narrow (`name:`, `(name)`, space)",
      cg["boss"] == ["helper", "reviewer", "security-auditor"], f"got {cg['boss']}")
check("boss inherits ALL three punctuation-adjacent delegates' effects (+ bare-Agent spawn residual)",
      entry(rep, "boss")["inferred"] == ["Exec", "Fs", "Net", "Unknown"],
      f"got {entry(rep, 'boss')['inferred']}")

# FABRICATION guard: a common-word agent name appearing in a PATH or identifier in prose is NOT a
# delegation mention (`.`/`/` are name-continuation chars) — else a false edge fabricates inherited effects.
rep, cg = scan({
    "boss.md": agent("boss", "Agent, Read", body="Edit src/build.rs and build.gradle, then read data.json in data/."),
    "build.md": agent("build", "Bash"),       # Exec — appears only as src/build.rs and build.gradle
    "data.md": agent("data", "WebFetch"),     # Net — appears only as data.json and data/
})
check("a name in a path/identifier (src/build.rs, data.json) is NOT a false delegation mention",
      cg["boss"] == ["build", "data"],  # no NAMED mention → falls to CHA (all), not a fabricated narrow
      f"got {cg['boss']}")
# the decisive check: with a REAL named mention of one but only path-fragments of the other, only the
# real one narrows (the path fragment must not fabricate an edge).
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="Always delegate to reviewer for review work; the file lives at src/build.rs there."),
    "reviewer.md": agent("reviewer", "WebFetch"),  # Net — real (space-delimited) mention
    "build.md": agent("build", "Bash"),            # Exec — only as src/build.rs → must NOT be edged
})
check("path-fragment name does not fabricate an edge when another name really narrows",
      cg["boss"] == ["reviewer"] and "Exec" not in entry(rep, "boss")["inferred"], f"got {cg['boss']}")

# THE SPAWN-RESIDUAL FIND (R11 seam battery): a bare `Agent` holder that mentions only a PURE agent can
# still spawn an unmentioned EFFECTFUL one at runtime — a prompt mention is not proof of the spawn set
# (unlike a harness-enforced allowlist). Narrowing to the mention WITHOUT disclosing read the
# orchestrator pure → a silent under-report (`deny Exec orch` falsely green). It must carry Unknown,
# while the precise edge (helper, NOT the smear of deployer) is kept for the map.
rep, cg = scan({
    "orch.md": agent("orch", "Agent, Read", body="For summaries, delegate to the `helper`."),
    "helper.md": agent("helper", "TodoWrite"),       # pure
    "deployer.md": agent("deployer", "Bash"),        # Exec — reachable via the Agent tool, not mentioned
})
check("bare-Agent orchestrator mentioning only a pure agent discloses the spawn residual (not silent-pure)",
      cg["orch"] == ["helper"] and "Unknown" in entry(rep, "orch")["inferred"]
      and entry(rep, "orch")["unresolved"], f"got {entry(rep, 'orch')['inferred']}")

# ALLOWLIST control (the SOUND devirt analog): a declared `Agent(helper)` spawn-allowlist is
# harness-enforced, so narrowing to it is sound — NO Unknown residual even with an effectful deployer
# present, and the ALLOWLIST (not a prompt mention of deployer) decides the edges.
rep, cg = scan({
    "orch.md": agent("orch", "Agent(helper), Read", body="Mentions deployer in prose but cannot spawn it."),
    "helper.md": agent("helper", "TodoWrite"),
    "deployer.md": agent("deployer", "Bash"),
})
check("declared Agent(allowlist) narrows soundly — no spawn residual, allowlist beats the prose mention",
      cg["orch"] == ["helper"] and "Unknown" not in entry(rep, "orch")["inferred"],
      f"got inferred={entry(rep, 'orch')['inferred']} edges={cg['orch']}")

# a name embedded in a LONGER name is NOT a mention (boundary holds the precise direction too)
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="Only ever use code-reviewer here."),
    "code-reviewer.md": agent("code-reviewer", "Bash"),
    "reviewer.md": agent("reviewer", "WebFetch"),  # substring of code-reviewer — must NOT be edged
})
check("a name embedded in a longer agent name is not a false mention",
      cg["boss"] == ["code-reviewer"], f"got {cg['boss']}")

# ── 2. CHA fallback for unnamed delegation ────────────────────────────────────────────────────────
rep, cg = scan({
    "boss.md": agent("boss", "Agent, Read", body="Delegate as needed."),
    "a.md": agent("a", "WebFetch"),
    "b.md": agent("b", "Bash"),
})
check("unnamed Agent tool CHA-edges to all", cg["boss"] == ["a", "b"], f"got {cg['boss']}")
check("CHA boss inherits the union", entry(rep, "boss")["inferred"] == ["Exec", "Fs", "Net"],
      f"got {entry(rep, 'boss')['inferred']}")

# PAREN-AWARE agent tools (documented `Agent(worker)` spawn-allowlist + base-stripping a Bash specifier).
# `Agent(worker)` must still count as the Agent tool (enable delegation) — a literal `"Agent" in tools`
# missed it and disabled delegation (review find); and `Bash(git:*)` base-strips to Exec, not Unknown.
rep, cg = scan({
    "coord.md": agent("coord", "Agent(worker), Read", body="Use the worker subagent to fetch things."),
    "worker.md": agent("worker", "WebFetch"),
    "gitter.md": agent("gitter", "Bash(git:*), Read"),
    "multi.md": agent("multi", "Bash(git:*, npm:*), Read"),  # inner comma must NOT shatter the token
})
check("Agent(worker) still grants the Agent tool → delegation enabled (not silently disabled)",
      cg["coord"] == ["worker"] and "Net" in entry(rep, "coord")["inferred"], f"got {cg['coord']}")
check("a Bash(git:*) specifier base-strips to Exec (not Unknown → not a deny-Exec evasion)",
      entry(rep, "gitter")["inferred"] == ["Exec", "Fs"] and not entry(rep, "gitter")["unresolved"],
      f"got {entry(rep, 'gitter')}")
check("a multi-arg specifier Bash(git:*, npm:*) is paren-aware (inner comma kept) → Exec",
      entry(rep, "multi")["inferred"] == ["Exec", "Fs"], f"got {entry(rep, 'multi')}")

# ── 3. ambient authority (no tools: line) ─────────────────────────────────────────────────────────
rep, cg = scan({"legacy.md": agent("legacy", None)}, mcp=["billing"])
e = entry(rep, "legacy")
check("ambient agent is unresolved", e["unresolved"] is True)
check("ambient agent carries Unknown + the big set",
      {"Exec", "Fs", "Net", "Clock", "Ipc", "Unknown"} <= set(e["inferred"]), f"got {e['inferred']}")
check("ambient why names the cause", "ambient:tools-unrestricted" in e.get("unknownWhy", []))
check("ambient reaches the uncurated MCP server", "mcp-uncurated:billing" in e.get("unknownWhy", []))

# ── 3b. --nested-spawn: ambient agents as delegators (harnesses that allow nested spawning) ──────
# Stock Claude Code subagents cannot nest-spawn, so an AMBIENT agent (no `tools:` line) gets NO
# delegation edges by default — with `Agent` ambient, a real 182-agent public fleet produced a
# ~20k-edge all-reaches-all smear (the wshobson/agents find). --nested-spawn opts back in for
# harnesses that DO allow it. Both behaviors are pinned here (the flag had zero coverage and it
# flips the whole delegation model for ambient agents).
def scan_flags(files, *flags):
    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    for fname, content in files.items():
        open(os.path.join(adir, fname), "w").write(content)
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "t", *flags],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return (json.load(open(f"{out}.t.Fleet.json")), json.load(open(f"{out}.t.Fleet.callgraph.json")))

_nsfleet = {
    "amb.md": agent("amb", None, body="Do whatever it takes."),   # ambient: no tools line
    "worker.md": agent("worker", "WebFetch"),
    "off.md": agent("off", "Bash"),
    "scoped.md": agent("scoped", "Read"),                          # confined: never a delegator
}
rep, cg = scan_flags(_nsfleet)
check("--nested-spawn OFF (default): an ambient agent gets NO delegation edges (stock harness: "
      "subagents cannot nest-spawn — the honest graph, no all-reaches-all smear)",
      cg["amb"] == [], f"got {cg['amb']}")
rep, cg = scan_flags(_nsfleet, "--nested-spawn")
check("--nested-spawn ON: an ambient agent becomes a bare-Agent delegator (CHA over the fleet)",
      cg["amb"] == ["off", "scoped", "worker"], f"got {cg['amb']}")
check("--nested-spawn ON: a CONFINED agent (Read, no Agent grant) still gets no edges",
      cg["scoped"] == [], f"got {cg['scoped']}")
# a prompt mention narrows the nested-spawn ambient delegator like any bare-Agent holder (rung 2)
_nsnamed = dict(_nsfleet, **{"amb.md": agent("amb", None, body="Delegate to `worker` only.")})
rep, cg = scan_flags(_nsnamed, "--nested-spawn")
check("--nested-spawn ON: a prompt mention narrows the ambient delegator's edges (worker only), "
      "with the ambient Unknown residual still disclosed",
      cg["amb"] == ["worker"] and "Unknown" in entry(rep, "amb")["inferred"], f"got {cg['amb']}")


# ── 4. MCP: curated vs Unknown ────────────────────────────────────────────────────────────────────
rep, _ = scan({
    "m.md": agent("m", "mcp__gmail__send, Read"),
    "u.md": agent("u", "mcp__billing__charge"),
})
check("curated MCP classifies (gmail → Net+Ipc), no Unknown",
      entry(rep, "m")["inferred"] == ["Fs", "Ipc", "Net"] and not entry(rep, "m")["unresolved"],
      f"got {entry(rep, 'm')}")
eu = entry(rep, "u")
check("uncurated MCP → Unknown + unknownWhy", eu["unresolved"] and eu.get("unknownWhy") == ["mcp-uncurated:billing"],
      f"got {eu}")

# legacy/alias builtin names declared by real fleets (the public-fleet sweep curation gaps)
rep, cg = scan({
    "old.md": agent("old", "LS, MultiEdit, NotebookRead, TodoRead"),
    "boss.md": agent("boss", "Task, Read", body="Spawn `old` when needed."),
    "amb.md": agent("amb", "All tools"),
})
eo = entry(rep, "old")
check("legacy tools classify (LS/MultiEdit/NotebookRead -> Fs, no Unknown)",
      eo["inferred"] == ["Fs"] and not eo["unresolved"] and set(eo.get("fs", [])) == {"read", "write"},
      f"got {eo}")
check("legacy Task tool counts for delegation (named narrowing)",
      cg["boss"] == ["old"] and "Fs" in entry(rep, "boss")["inferred"], f"got {cg['boss']}")
ea = entry(rep, "amb")
check("'tools: All tools' is ambient authority, not a tool named 'All tools'",
      ea["unresolved"] and "ambient:tools-unrestricted" in ea.get("unknownWhy", [])
      and not any(w == "tool-unknown:All tools" for w in ea.get("unknownWhy", [])), f"got {ea}")

# QUOTED tool tokens must keep their meaning — YAML quoting (to protect a specifier's `()`/`:`/`*`)
# must not turn a definite tool into an `Unknown` that slips an effect-specific deny gate.
from candor_agents.scan import parse_frontmatter, tool_list
def _tl(fm): m, _ = parse_frontmatter(fm); return tool_list(m)
check("tool_list: a quoted bare tool keeps its identity (not Unknown)",
      _tl('---\ntools: "Bash"\n---\nx') == ["Bash"]
      and _tl("---\ntools: [Read, 'Bash']\n---\nx") == ["Read", "Bash"]
      and _tl('---\ntools: "Read, Bash"\n---\nx') == ["Read", "Bash"])
check("tool_list: quoting does not disturb `tools: []` (still explicitly confined, not ambient)",
      _tl("---\ntools: []\n---\nx") == [])
# inline YAML comments are stripped (the real parser Claude uses drops them) — else the last tool
# before the comment becomes `Bash # …` → Unknown, evading a deny gate. Quote-aware; `C#` kept.
check("frontmatter: an inline `# comment` after tools is stripped, not parsed as a tool",
      _tl("---\ntools: Read, Bash # only safe\n---\nx") == ["Read", "Bash"]
      and _tl("---\ntools: Read # c\n---\nx") == ["Read"]
      and _tl("---\ntools:\n  - Read\n  - Bash  # shell\n---\nx") == ["Read", "Bash"])
check("frontmatter: a `#` without leading space (C#) is NOT a comment",
      parse_frontmatter("---\ndescription: Reviews C# code\n---\nx")[0]["description"] == "Reviews C# code")
# agent end-to-end: a quoted Bash still classifies as Exec
req, _ = scan({"q.md": agent("q", '"Bash"')})
check("a quoted `tools: \"Bash\"` agent classifies as Exec (not Unknown)",
      entry(req, "q")["inferred"] == ["Exec"], f"got {entry(req, 'q')}")
# command end-to-end: a quoted Bash(curl:*) specifier keeps Exec+Net and the curl head
with tempfile.TemporaryDirectory() as _qd:
    os.makedirs(os.path.join(_qd, ".claude", "commands"))
    open(os.path.join(_qd, ".claude", "commands", "q.md"), "w").write('---\nallowed-tools: "Bash(curl:*)"\n---\nx\n')
    _qo = os.path.join(_qd, "r")
    subprocess.run([sys.executable, "-m", "candor_agents.scan", _qd, "--out", _qo, "--fleet", "t"], capture_output=True, text=True, check=True)
    _qc = entry(json.load(open(f"{_qo}.t.Fleet.json")), "command:q")
    check("a quoted command `allowed-tools: \"Bash(curl:*)\"` keeps Exec+Net + the curl head",
          _qc and set(_qc["inferred"]) == {"Exec", "Net"} and "curl" in _qc.get("cmds", []), f"got {_qc}")

# an unheard-of builtin tool is Unknown, never silently pure
rep, _ = scan({"x.md": agent("x", "FrobnicateDisk")})
ex = entry(rep, "x")
check("unknown tool name → Unknown (never silent-pure)",
      ex and ex["unresolved"] and ex.get("unknownWhy") == ["tool-unknown:FrobnicateDisk"], f"got {ex}")

# ── 4b. declared MCP capabilities (the Unknown killer) ───────────────────────────────────────────
def scan_decl(files, mcp_entries):
    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    for fname, content in files.items():
        open(os.path.join(adir, fname), "w").write(content)
    json.dump({"mcpServers": mcp_entries}, open(os.path.join(d, ".mcp.json"), "w"))
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "t"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return (json.load(open(f"{out}.t.Fleet.json")), json.load(open(f"{out}.t.Fleet.callgraph.json")))

rep, _ = scan_decl(
    {"biller.md": agent("biller", "mcp__billing__charge, Read"),
     "shadow.md": agent("shadow", "mcp__shadow__op"),
     "typo.md": agent("typo", "mcp__typoed__op")},
    {"billing": {"command": "x", "candorEffects": ["Net", "Db"]},
     "shadow": {"command": "x"},
     "typoed": {"command": "x", "candorEffects": ["net"]}})
eb = entry(rep, "biller")
check("declared MCP classifies (candorEffects kills the Unknown)",
      eb["inferred"] == ["Db", "Fs", "Net"] and not eb["unresolved"], f"got {eb}")
check("undeclared server still Unknown", entry(rep, "shadow")["unresolved"])
et = entry(rep, "typo")
check("invalid effect name in a declaration NEVER silently accepted",
      et["unresolved"] and et.get("unknownWhy") == ["mcp-decl-invalid:typoed:net"], f"got {et}")
rep, _ = scan_decl({"p.md": agent("p", "mcp__quiet__op")},
                   {"quiet": {"command": "x", "candorEffects": []}})
check("candorEffects: [] declares a PURE server (agent omitted from report)",
      entry(rep, "p") is None)

# ── 5. transitive propagation through a named chain ───────────────────────────────────────────────
rep, cg = scan({
    "a.md": agent("a", "Agent", body="Use `b`."),
    "b.md": agent("b", "Agent", body="Use `c`."),
    "c.md": agent("c", "Bash"),
})
# a and b hold bare `Agent` and narrow by mention → each discloses the spawn residual (Unknown) on top
# of the propagated Exec; c performs Exec directly (no `Agent` tool → no residual).
check("effect propagates up a 3-deep named chain (+ bare-Agent residual on the spawners)",
      entry(rep, "a")["inferred"] == ["Exec", "Unknown"]
      and entry(rep, "b")["inferred"] == ["Exec", "Unknown"]
      and entry(rep, "c")["inferred"] == ["Exec"])

# ── 6. pure units: omitted from the report, present in the sidecar ───────────────────────────────
rep, cg = scan({
    "pure.md": agent("pure", "TodoWrite, Skill"),
    "eff.md": agent("eff", "WebFetch"),
})
check("pure agent omitted from the report", entry(rep, "pure") is None)
check("pure agent present in the callgraph sidecar", "pure" in cg)

# ── 7. envelope + main ────────────────────────────────────────────────────────────────────────────
rep, cg = scan({"a.md": agent("a", "Read")})
check("spec envelope (candor.spec = %s)" % _SPEC, rep["candor"]["spec"] == _SPEC)
check("hash join keys emitted (§2 MUST)", all("#" in f.get("hash", "") for f in rep["functions"]))
check("unitKind names every fleet unit (spec ⟨0.5⟩: agent/session/hooks)",
      all(f.get("unitKind") in ("agent", "session", "hooks") for f in rep["functions"])
      and entry(rep, "session")["unitKind"] == "session")
m = entry(rep, "session")
check("the session root is the entry point and edges to every agent",
      m and m.get("entryPoint") is True and cg["session"] == ["a"])

# ══ SPEC §2 ⟨0.24⟩ — EVERY ORDERING, in a report AND in a query output, is locale-INDEPENDENT ══════
# Sort by Unicode code point. The whole document already depends on this without saying so: every
# compatibility argument is phrased as "a default report is BYTE-IDENTICAL", which is not even
# checkable if two runs of one version can disagree. Measured elsewhere in the family: one engine used
# a locale-sensitive comparator at SEVEN sites, one of them ordering the coverage ledger INSIDE the
# emitted report — same build, same tree, different `LC_ALL`, different report bytes.
#
# Python's `sorted()` on `str` is code-point order and locale-independent, so this engine satisfies
# the clause for free. "Free today" is not "pinned", hence this control — it fails the moment anyone
# reaches for `locale.strxfrm`, a locale-aware `cmp_to_key`, or an env-derived sort key.
#
# `et_EE`, NOT `tr_TR`: **locale collation reorders pure ASCII**, and Estonian collates `z` between
# `s` and `t`, so `[tpad, zpad]` under `LC_ALL=C` becomes `[zpad, tpad]` under `et_EE.UTF-8`. Turkish
# does not reorder ASCII at all, so a C-vs-`tr_TR` control returns "no difference" and licenses a
# FALSE all-clear. Verified by injecting `key=locale.strxfrm` at one report-ordering site: et_EE
# caught it — and `da_DK.UTF-8` and `cs_CZ.UTF-8` both reported NO difference on the same input, so
# the locale choice is load-bearing, not decorative.
_ld = tempfile.mkdtemp()
_lad = os.path.join(_ld, ".claude", "agents"); os.makedirs(_lad)
for _n, _t in [("zpad", "WebFetch"), ("tpad", "Read"), ("spad", "Bash"), ("apad", "Write")]:
    open(os.path.join(_lad, _n + ".md"), "w").write(agent(_n, f"{_t}, Agent", body="Use zpad, tpad, spad."))
json.dump({"mcpServers": {"zmystery": {}, "tmystery": {}, "smystery": {}}},
          open(os.path.join(_ld, ".mcp.json"), "w"))
_lbytes = {}
for _loc in ("C", "et_EE.UTF-8"):
    for _f in os.listdir(_ld):  # delete the output before measuring the control
        if _f.endswith(".json") and _f != ".mcp.json":
            os.remove(os.path.join(_ld, _f))
    _env = dict(os.environ); _env["LC_ALL"] = _loc; _env["LANG"] = _loc
    _lo = os.path.join(_ld, "L")
    _lr = subprocess.run([sys.executable, "-m", "candor_agents.scan", _ld, "--out", _lo, "--fleet", "t"],
                         capture_output=True, text=True, env=_env)
    _lbytes[_loc] = (_lr.returncode, open(_lo + ".t.Fleet.json", "rb").read(),
                     open(_lo + ".t.Fleet.callgraph.json", "rb").read(), _lr.stderr)
check("§2 ⟨0.24⟩: the REPORT is byte-identical under `LC_ALL=C` and `LC_ALL=et_EE.UTF-8` — every "
      "ordering is by Unicode code point, never by an ambient locale's collation",
      _lbytes["C"][1] == _lbytes["et_EE.UTF-8"][1] and _lbytes["C"][0] == 0,
      f"C={len(_lbytes['C'][1])}B et_EE={len(_lbytes['et_EE.UTF-8'][1])}B")
check("§2 ⟨0.24⟩: the CALLGRAPH SIDECAR and the stderr RECEIPT are locale-independent too — the "
      "clause covers report bytes and query/receipt output alike",
      _lbytes["C"][2] == _lbytes["et_EE.UTF-8"][2] and _lbytes["C"][3] == _lbytes["et_EE.UTF-8"][3])
check("§2 ⟨0.24⟩ control is NON-VACUOUS in its FIXTURE: it really does carry ASCII names Estonian "
      "collation reorders (`tpad` before `zpad` under C; et_EE puts `z` between `s` and `t`)",
      b"zpad" in _lbytes["C"][1] and b"tpad" in _lbytes["C"][1]
      and _lbytes["C"][1].index(b"tpad") < _lbytes["C"][1].index(b"zpad"))
# …and non-vacuous in its PLATFORM. If the C library has no Estonian locale (common on a minimal CI
# image) then setting `LC_ALL` is inert, the byte-equality above passes without testing anything, and
# a green check would be a claim the run did not earn. Probe whether the locale can actually reorder
# ASCII here; disclose a SKIP rather than bank a vacuous pass.
_lprobe = subprocess.run(
    [sys.executable, "-c", "import locale;locale.setlocale(locale.LC_COLLATE,'et_EE.UTF-8');"
                           "print(sorted(['tpad','zpad'],key=locale.strxfrm))"],
    capture_output=True, text=True)
if _lprobe.returncode == 0 and _lprobe.stdout.strip() == "['zpad', 'tpad']":
    check("§2 ⟨0.24⟩ control is NON-VACUOUS on THIS PLATFORM: `et_EE.UTF-8` is installed and does "
          "reorder pure ASCII, so the byte-equality above was a real measurement", True)
else:
    print("  SKIP §2 locale control is VACUOUS here — `et_EE.UTF-8` is unavailable or does not "
          f"reorder ASCII ({_lprobe.stdout.strip() or _lprobe.stderr.strip().splitlines()[-1:]}); "
          "the byte-equality checks above proved nothing on this machine")

# ══ SPEC §1/§5.1/§6.1 ⟨0.24⟩ — `Llm` is a §1 effect, and it CO-EMITS `Net` ════════════════════════
# §5.1: a `candorEffects` manifest names "effect names from §1", and voiding is reserved for a name
# OUTSIDE §1 (a typo must not silently narrow a surface). `Llm` has been §1 since ⟨0.13⟩ and was
# missing from this engine's vocabulary, so a server declaring the effect it actually has was voided
# as out-of-vocabulary and read Unknown — fail-closed, but a FALSE disclosure: a legitimate
# declaration reported as a typo. §6.1 ⟨0.24⟩: `Llm` REFINES `Net` (a model-provider call is an
# outbound request in EVERY instance, which is the test for refinement) so the engines co-emit both —
# unlike `Db`, which has no egress and is emitted alone.
_md = tempfile.mkdtemp()
_mad = os.path.join(_md, ".claude", "agents"); os.makedirs(_mad)
open(os.path.join(_mad, "l.md"), "w").write(agent("l", "mcp__myllm"))
open(os.path.join(_mad, "e.md"), "w").write(agent("e", "mcp__embedded"))
json.dump({"mcpServers": {"myllm": {"candorEffects": ["Llm"]},
                          "embedded": {"candorEffects": ["Db"]}}},
          open(os.path.join(_md, ".mcp.json"), "w"))
_mo = os.path.join(_md, "m")
subprocess.run([sys.executable, "-m", "candor_agents.scan", _md, "--out", _mo, "--fleet", "t"],
               capture_output=True, text=True)
_mrep = json.load(open(_mo + ".t.Fleet.json"))
check("§5.1 ⟨0.24⟩: a `candorEffects: [\"Llm\"]` declaration is ACCEPTED — `Llm` is a §1 name, so "
      "voiding it as out-of-vocabulary was a false disclosure, not a conservative one",
      "Llm" in entry(_mrep, "l")["direct"] and "Unknown" not in entry(_mrep, "l")["inferred"],
      json.dumps(entry(_mrep, "l")))
check("§6.1 ⟨0.24⟩: `Llm` CO-EMITS `Net` (every model-provider call is an outbound request), so a "
      "`deny Net` gate sees the egress — additive and monotone, never a relaxation",
      "Net" in entry(_mrep, "l")["inferred"])
check("§6.1 ⟨0.24⟩: `Db` does NOT co-emit `Net` — an embedded store has no egress at all, and "
      "widening `deny Net` onto it would be the fabrication mirror",
      "Net" not in entry(_mrep, "e")["inferred"] and "Db" in entry(_mrep, "e")["direct"],
      json.dumps(entry(_mrep, "e")))

# ── 8. fs detail aggregates read/write ────────────────────────────────────────────────────────────
rep, _ = scan({"rw.md": agent("rw", "Read, Write")})
check("fs detail carries read+write", entry(rep, "rw").get("fs") == ["read", "write"])

# ── 9. integration: the UNMODIFIED candor-query over the emitted report ──────────────────────────
_fq = subprocess.run(["bash", os.path.join(HERE, "find-query.sh")], capture_output=True, text=True)
Q = _fq.stdout.strip() if _fq.returncode == 0 else ""
if Q and os.path.exists(Q):
    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    open(os.path.join(adir, "boss.md"), "w").write(agent("boss", "Agent", body="Use `leaf`."))
    open(os.path.join(adir, "leaf.md"), "w").write(agent("leaf", "WebFetch"))
    out = os.path.join(d, "r")
    subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out], capture_output=True)
    pre = out
    w = subprocess.run([Q, "where", pre, "Net", "1"], capture_output=True, text=True)
    j = json.loads(w.stdout)
    check("candor-query where: leaf direct, boss inherits",
          "leaf" in j.get("directly", []) and "boss" in j.get("inherited", []), f"got {w.stdout[:120]}")
    c = subprocess.run([Q, "callers", pre, "leaf", "1"], capture_output=True, text=True)
    j = json.loads(c.stdout)
    trans = j.get("transitive", j)
    check("candor-query callers: boss+session reach leaf",
          "boss" in str(trans) and "session" in str(trans), f"got {c.stdout[:120]}")
    pol = os.path.join(d, "policy")
    open(pol, "w").write("deny Ipc boss\n")
    wi = subprocess.run([Q, "whatif", pre, "leaf", "Ipc", pol], capture_output=True, text=True)
    check("candor-query whatif: gate verdict fires through the fleet graph",
          wi.returncode == 1 and "boss" in wi.stdout, f"rc={wi.returncode} out={wi.stdout[:160]}")
else:
    print("  SKIP candor-query integration (find-query.sh could not locate or build it)")

# ── 10. --link: the Exec-boundary refinement (fleet inherits the linked code report) ─────────────
d = tempfile.mkdtemp()
adir = os.path.join(d, ".claude", "agents")
os.makedirs(adir)
open(os.path.join(adir, "runner.md"), "w").write(agent("runner", "Bash"))
open(os.path.join(adir, "watcher.md"), "w").write(agent("watcher", "WebSearch"))
code = {"candor": {"version": "x", "spec": "0.7"},
        "functions": [{"fn": "main", "loc": "src/main.rs", "inferred": ["Db", "Exec"],
                       "direct": ["Db"], "declared": [], "undeclared": [], "overdeclared": [],
                       "unresolved": False, "calls": [], "entryPoint": True}]}
json.dump(code, open(os.path.join(d, "c.app.scan.json"), "w"))
json.dump({"main": []}, open(os.path.join(d, "c.app.scan.callgraph.json"), "w"))
out = os.path.join(d, "r")
r = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "t",
                    "--link", os.path.join(d, "c")], capture_output=True, text=True)
rep = json.load(open(f"{out}.t.Fleet.json"))
cg = json.load(open(f"{out}.t.Fleet.callgraph.json"))
er = entry(rep, "runner")
check("--link: Bash agent edges to the code entryPoint", "main" in cg["runner"], f"got {cg['runner']}")
check("--link: Bash agent inherits the code's recorded effects", "Db" in er["inferred"], f"got {er['inferred']}")
check("--link: non-Bash agent does NOT inherit", "Db" not in entry(rep, "watcher")["inferred"])
check("--link: pseudo-node not re-emitted as a fleet row", entry(rep, "main") is None)

from candor_agents import scan as _sc

# ══ SPEC §2.2 ⟨0.24⟩ — a report-locator glob MUST exclude the RESERVED trailing segments AT THE GLOB
# `--link <prefix>` is this engine's only report locator, and it carved out 3 of the family's 7
# reserved names. Sidecar names are per-engine, so the exclusion cannot be by segment COUNT (that
# admits a 2-segment sidecar from another producer) and cannot be at the PARSE (measured on the
# reference engine: a `<prefix>.<pkg>.hierarchy.json` claimed as a report reported the engine's own
# mistake as the user's data loss). It MUST be a DENYLIST — an allowlist of known `<type>` values
# would make any report whose type segment we failed to anticipate silently invisible: a false
# all-clear. Incompleteness in a denylist is loud; in an allowlist it is the cardinal sin.
_sd = os.path.join(d, "s")
json.dump(code, open(f"{_sd}.app.scan.json", "w"))          # a real report
for _seg in ("callgraph", "hierarchy", "calibrated", "layerreach", "locs", "gate"):
    # every reserved name, in BOTH shapes the family writes: `<prefix>.<seg>.json` (1 segment) and
    # `<prefix>.<pkg>.<seg>.json` (2) — the shape that landed on the `<crate>.<type>` report form.
    json.dump({"functions": [{"fn": f"SIDECAR::{_seg}", "inferred": ["Net"], "entryPoint": True}]},
              open(f"{_sd}.{_seg}.json", "w"))
    json.dump({"functions": [{"fn": f"SIDECAR2::{_seg}", "inferred": ["Net"], "entryPoint": True}]},
              open(f"{_sd}.app.{_seg}.json", "w"))
json.dump({"functions": [{"fn": "SIDECAR::enc", "inferred": ["Net"], "entryPoint": True}]},
          open(f"{_sd}.encountered-app.json", "w"))
out2 = os.path.join(d, "r2")
subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out2, "--fleet", "t",
                "--link", _sd], capture_output=True, text=True)
_cg2 = json.load(open(f"{out2}.t.Fleet.callgraph.json"))
check("§2.2 ⟨0.24⟩ --link: EVERY reserved trailing segment is excluded at the GLOB (callgraph / "
      "hierarchy / calibrated / layerreach / locs / gate / encountered-*), in both the 1-segment and "
      "the 2-segment shape — this list carried 3 of the 7",
      not any(str(t).startswith("SIDECAR") for t in _cg2["runner"]), _cg2["runner"])
check("§2.2 ⟨0.24⟩ --link: the real report beside those sidecars still resolves — the exclusion must "
      "not cost a report (an empty link would pass the assertion above vacuously)",
      "main" in _cg2["runner"] and "Db" in entry(json.load(open(f"{out2}.t.Fleet.json")),
                                                 "runner")["inferred"], _cg2["runner"])
check("§2.2 ⟨0.24⟩: a crate legitimately NAMED `hierarchy` sits in the `<crate>` position, not the "
      "reserved one, and MUST still resolve — the denylist reads the TRAILING segment only",
      not _sc.is_reserved_sidecar("r.hierarchy.lib.json")
      and not _sc.is_reserved_sidecar("r.gate.scan.json")
      and _sc.is_reserved_sidecar("r.app.hierarchy.json")
      and _sc.is_reserved_sidecar("r.encountered-mycrate.json"))

# §4 ⟨0.24⟩ CONSUMER CONTROL — a chained CODE report may carry any of the five kinds, plus a migration
# or fabricated one. This engine reads `entryPoint`/`inferred` from a linked report and NOTHING else,
# so an off-vocabulary kind cannot break it and cannot be silently rewritten. The inherited `Unknown`
# correctly carries NO direct `unknownWhy` on the fleet unit (§4: a reason names a site in the unit's
# OWN body) — the reason stays at the source, in the code report, reachable over the preserved edge.
_lkd = os.path.join(d, "k")
json.dump({"candor": {"version": "x", "spec": "0.25"},
           "functions": [{"fn": "kmain", "inferred": ["Unknown"], "direct": ["Unknown"],
                          "unknownWhy": ["banana:whatever"], "calls": [], "entryPoint": True}]},
          open(f"{_lkd}.app.scan.json", "w"))
out3 = os.path.join(d, "r3")
_r3 = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out3, "--fleet", "t",
                      "--link", _lkd], capture_output=True, text=True)
_rep3 = json.load(open(f"{out3}.t.Fleet.json"))
check("§4 ⟨0.24⟩ --link: a linked report carrying a FABRICATED off-vocabulary kind is consumed "
      "without error and its `Unknown` propagates to the linking unit",
      _r3.returncode == 0 and "Unknown" in entry(_rep3, "runner")["inferred"], _r3.stderr[-300:])
check("§4 ⟨0.24⟩ --link: the foreign reason is NOT copied onto the inheriting unit — an inherited "
      "`Unknown` carries no reason of its own, and the edge to the source is preserved",
      "banana:whatever" not in entry(_rep3, "runner").get("unknownWhy", [])
      and "kmain" in json.load(open(f"{out3}.t.Fleet.callgraph.json"))["runner"])

# ══ SPEC §6.2 ⟨0.24⟩ — THE REASON CLASS MUST CROSS THE `--link` BOUNDARY WITH THE REACH IT SCOPES ══
# The reach crossed and the CLASS did not: the link path kept only each entry's `inferred`, so a
# fleet unit inheriting a linked `Unknown` reached the gate with an EMPTY class set. One defect, both
# of §6.2's named failure modes live at once — `deny Unknown[dispatch]` exited 0 on a reach that is
# EXACTLY dispatch-classified (req 2: excluded "by every filter, including one naming its own class")
# while `deny Unknown[unresolved]` exited 1 on that same reach (req 3's mirror fabrication, charged
# by the join-side empty-`classes` arm that CONTRIBUTES exists to replace). Hence BOTH directions
# below: a fix that only lights the first is half a fix, and the more dangerous half.
#
# WHY THE CONTROL DIRECTLY ABOVE COULD NOT SEE THIS: `banana:whatever` classifies `unresolved`, and so
# does an unclassed hole — it asserts the same outcome whether linked reasons are consumed or dropped
# entirely. §4 ⟨0.24⟩: a control only exercised by inputs the implementation already handles is not a
# control. The reason below is `dispatch:`, whose class the fleet scan can NEVER produce itself, so
# these assertions can only pass if the class genuinely travelled.
def _linked_gate(tag, code_functions, rule, sidecar=None):
    """Scan the fleet against a linked CODE report under one policy rule → (exit code, verdict)."""
    pre = os.path.join(d, f"lk-{tag}")
    json.dump({"candor": {"version": "x", "spec": "0.25"}, "functions": code_functions},
              open(f"{pre}.app.scan.json", "w"))
    if sidecar is not None:
        json.dump(sidecar, open(f"{pre}.app.scan.callgraph.json", "w"))
    po = os.path.join(d, f"lk-{tag}.policy")
    open(po, "w").write(rule + "\n")
    o, gj = os.path.join(d, f"lo-{tag}"), os.path.join(d, f"lg-{tag}.json")
    for stale in (f"{o}.t.Fleet.json", f"{o}.t.Fleet.callgraph.json", gj):
        if os.path.exists(stale):
            os.remove(stale)                       # delete the output before measuring the control
    rr = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", o, "--fleet", "t",
                         "--link", pre, "--policy", po, "--gate-json", gj],
                        capture_output=True, text=True)
    return rr.returncode, json.load(open(gj))


def _rcls(verdict):
    """The first violation's `reasonClass`, or None when there is no violation — so a mutant that
    empties the verdict FAILS these checks instead of crashing the run out from under the rest."""
    vs = verdict["violations"]
    return vs[0].get("reasonClass") if vs else None


# (1) the entry names its own dispatch reason.
_LD = [{"fn": "kmain", "inferred": ["Unknown"], "direct": ["Unknown"],
        "unknownWhy": ["dispatch:A.b"], "calls": [], "entryPoint": True}]
_rc, _vd = _linked_gate("d1", _LD, "deny Unknown[dispatch] runner", {"kmain": []})
check("§6.2 ⟨0.24⟩ --link UNDER-REPORT: `deny Unknown[dispatch]` FIRES on a fleet unit whose "
      "transitive `Unknown` is exactly dispatch-classified in the linked CODE report — the class "
      "crosses the boundary with the reach it scopes",
      _rc == 1 and [v["fn"] for v in _vd["violations"]] == ["runner"], (_rc, _vd))
check("§6.2/§3.3 --link: the AS-EFF-006 verdict carries `reasonClass` — a MUST once `effects` include "
      "`Unknown`, and it must name the LINKED class, not the absence default",
      _rcls(_vd) == ["dispatch"], _vd)
_rc, _vd = _linked_gate("d2", _LD, "deny Unknown[unresolved] runner", {"kmain": []})
check("§6.2 ⟨0.24⟩ --link MIRROR FABRICATION: `deny Unknown[unresolved]` does NOT fire on that same "
      "unit — charging `unresolved` to an `Unknown` correctly classified `dispatch` at its source is "
      "the fabrication req 3 forbids (it fired through the join's absence-keyed empty-classes arm)",
      _rc == 0 and _vd["ok"] and _vd["violations"] == [], (_rc, _vd))
for _f in ("deny Unknown runner", "deny Unknown[*] runner", "deny Unknown[dynamic] runner"):
    _rc, _vd = _linked_gate("d3", _LD, _f, {"kmain": []})
    check(f"§6.2 --link: `{_f}` is UNCHANGED by the class crossing (bare / `*` / `dynamic` all cover "
          f"dispatch) and now carries the resolved class",
          _rc == 1 and [v["fn"] for v in _vd["violations"]] == ["runner"]
          and _rcls(_vd) == ["dispatch"], (_f, _rc, _vd))

# (2) the entry does NOT name it — `unknownWhy` is direct-only (§4), so the reason sits a frame deeper
# INSIDE the code report. Reading the entry's own field would answer a different question and leave
# this case exactly as broken as before; what has to travel is the code report's own transitive
# resolution, over its `.callgraph.json` sidecar AND its rows' `calls`.
_LI = [{"fn": "kmain", "inferred": ["Unknown"], "direct": [], "calls": ["deep"], "entryPoint": True},
       {"fn": "deep", "inferred": ["Unknown"], "direct": ["Unknown"],
        "unknownWhy": ["reflect:R.m"], "calls": []}]
for _tag, _side in (("i1", {"kmain": ["deep"], "deep": []}), ("i2", None)):
    _how = "with its callgraph sidecar" if _side else "with NO sidecar (the rows' `calls` carry it)"
    _rc, _vd = _linked_gate(_tag, _LI, "deny Unknown[reflect] runner", _side)
    check(f"§6.2 ⟨0.24⟩ --link: the class travels when the linked ENTRY's `Unknown` is itself "
          f"INHERITED inside the code report ({_how}) — the entry's direct `unknownWhy` is empty here, "
          f"so only the code report's own TRANSITIVE resolution can supply `reflect`",
          _rc == 1 and _rcls(_vd) == ["reflect"], (_tag, _rc, _vd))
    _rc2, _vd2 = _linked_gate(_tag, _LI, "deny Unknown[unresolved] runner", _side)
    check(f"§6.2 ⟨0.24⟩ --link: and `unresolved` still does NOT fire on it ({_how}) — the mirror "
          f"direction holds for the inherited case too",
          _rc2 == 0 and _vd2["ok"], (_tag, _rc2, _vd2))

# (3) FAIL-CLOSED at the source, not at the join: a linked report that reaches `Unknown` and records
# no reason we can resolve contributes `unresolved` AT THE PSEUDO-NODE. Leaving it to the join's
# empty-`classes` arm would drop the hole entirely for any unit that ALSO inherits a classed Unknown
# from elsewhere — a silent under-report wearing a filter.
_LN = [{"fn": "kmain", "inferred": ["Unknown"], "direct": [], "calls": [], "entryPoint": True}]
_rc, _vd = _linked_gate("n1", _LN, "deny Unknown[unresolved] runner", {"kmain": []})
check("§6.2 --link FAIL-CLOSED: a linked `Unknown` with no resolvable reason contributes `unresolved` "
      "at the pseudo-node, so a narrowed filter naming it still fires",
      _rc == 1 and _rcls(_vd) == ["unresolved"], (_rc, _vd))
_rc, _vd = _linked_gate("n2", _LN, "deny Unknown[dispatch] runner", {"kmain": []})
check("§6.2 --link: …and that unclassifiable hole is NOT fabricated into some other class — "
      "`deny Unknown[dispatch]` does not fire on it",
      _rc == 0 and _vd["ok"], (_rc, _vd))
# WHY AT THE SOURCE AND NOT AT THE JOIN — the case that tells the two apart: ONE unit reaching BOTH a
# classed linked `Unknown` and a classless one. Its class set is then NON-empty, so the join's
# empty-`classes` net never runs and an `unresolved` filter would drop the hole entirely — the silent
# under-report, restored one layer up. Contributing at the pseudo-node keeps the set only GROWING.
_LB = [{"fn": "kdisp", "inferred": ["Unknown"], "direct": ["Unknown"],
        "unknownWhy": ["dispatch:A.b"], "calls": [], "entryPoint": True},
       {"fn": "khole", "inferred": ["Unknown"], "direct": [], "calls": [], "entryPoint": True}]
_rc, _vd = _linked_gate("b1", _LB, "deny Unknown[unresolved] runner", {"kdisp": [], "khole": []})
check("§6.2 ⟨0.24⟩ --link: a unit reaching BOTH a dispatch-classed linked `Unknown` and an "
      "unclassifiable one is STILL caught by `deny Unknown[unresolved]` — the hole contributes at its "
      "own pseudo-node, so its classed sibling cannot mask it",
      _rc == 1 and _rcls(_vd) == ["dispatch", "unresolved"], (_rc, _vd))

print()


# ── real-fleet probe regressions (the VoltAgent 155-agent scan) ──────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    adir = os.path.join(td, ".claude", "agents")
    os.makedirs(adir)
    open(os.path.join(adir, "README.md"), "w").write("# A catalog readme\nNot an agent; no frontmatter.\n")
    open(os.path.join(adir, "real.md"), "w").write(agent("real", "Read"))
    out = os.path.join(td, "deep", "dir", "r")  # --out into a directory that does not exist yet
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", td, "--out", out, "--fleet", "t"],
                       capture_output=True, text=True)
    check("scan: --out creates missing directories (install-probe find)", r.returncode == 0, r.stderr)
    rep = json.load(open(f"{out}.t.Fleet.json"))
    check("scan: a frontmatter-less .md is skipped with disclosure, never an ambient unit",
          entry(rep, "README") is None and "skipped 1 .md" in r.stderr and "README.md" in r.stderr)
    check("scan: the real agent beside the readme still scans", entry(rep, "real") is not None)

print()


# ── observe + drift (the product surface: declared vs observed) ───────────────────────────────────
import subprocess, tempfile
with tempfile.TemporaryDirectory() as td:
    r = subprocess.run([sys.executable, "-m", "candor_agents.observe", "fixture", "--transcripts", "fixture/transcripts",
                        "--out", os.path.join(td, "o")], capture_output=True, text=True)
    obs = json.load(open(os.path.join(td, "o.fixture.Observed.json")))
    by = {e["fn"]: e for e in obs["functions"]}
    check("observe: session is the entry point", by.get("session", {}).get("entryPoint") is True)
    check("observe: subagent transcripts aggregate by agentType",
          "researcher" in by and "coder" in by)
    check("observe: delegation edges resolve via toolUseId",
          set(by["session"]["calls"]) >= {"researcher", "coder"})
    check("observe: effects classify from observed tool_use (researcher Read/Grep -> Fs, no Net)",
          by["researcher"]["direct"] == ["Fs"])
    check("observe: an uncurated MCP tool reads Unknown with a named origin",
          "Unknown" in by["coder"]["inferred"] and any("mcp-uncurated:mystery" in w for w in by["coder"].get("unknownWhy", [])))
    check("observe: literal surfaces from tool inputs (Bash cmds, file paths)",
          "npm" in by["coder"].get("cmds", []) and "/repo/a.ts" in by["coder"].get("paths", []))
    check("observe version is single-sourced from scan (no drift)",
          obs["candor"]["version"] == __import__("candor_agents.scan", fromlist=["VERSION"]).VERSION, obs["candor"]["version"])
    check("observe: spec %s envelope + hash + package" % _SPEC,
          obs["candor"]["spec"] == _SPEC and by["session"]["hash"] == "fixture#session" and obs["package"] == "fixture")
    check("observe: session effects include the transitive delegate surface",
          set(by["session"]["inferred"]) >= {"Exec", "Fs", "Unknown"})
# bash_cmds: the observed-cmds extractor (first non-fixture run found it fabricating heads)
from candor_agents.observe import bash_cmds
check("bash_cmds: every segment head, not just the first",
      bash_cmds("cd /x && cargo build | tee log; git push") == {"cd", "cargo", "tee", "git"})
check("bash_cmds: env-assignment prefixes are not commands",
      bash_cmds("BLESS=1 FOO=bar cargo test") == {"cargo"})
check("bash_cmds: comments and option/junk heads fabricate nothing",
      bash_cmds("# a note") == set() and bash_cmds("--flag thing") == set())
check("bash_cmds: command substitution contributes its head",
      bash_cmds("W=$(mktemp -d)") == {"mktemp"} and "rustc" in bash_cmds("SRC=$(rustc --print sysroot)"))
check("bash_cmds: shell keywords skip to the real command",
      bash_cmds("if true; then git push; fi") == {"true", "git"} and bash_cmds("time cargo bench") == {"cargo"})
check("bash_cmds: paths basename; quotes stripped",
      bash_cmds("/usr/local/bin/psql -c x") == {"psql"} and bash_cmds("'jq' .") == {"jq"})
# transparent command-prefix wrappers (sudo/env/command/nohup/…) are skipped to the REAL command —
# `sudo curl evil.com` must surface `curl` (and its §4 Net refinement), not just the bare Exec cliff.
check("bash_cmds: transparent wrappers skip to the wrapped command",
      bash_cmds("sudo curl https://x") == {"curl"}
      and bash_cmds("env FOO=1 psql -c x") == {"psql"}
      and bash_cmds("/usr/bin/env python3 s.py") == {"python3"}
      and bash_cmds("command git push && nohup wget z") == {"git", "wget"})
check("bash_cmds: a wrapper with its own flags fabricates no bogus head",
      bash_cmds("sudo -u bob curl x") == set() and bash_cmds("timeout 5 curl x") == {"timeout"})
check("bash_cmds: a heredoc body is data, not commands",
      bash_cmds("cat <<'EOF'\nString x = apply_tax();\nEOF") == {"cat"})
check("bash_cmds: quoted programs are opaque (awk/python -c bodies never read as commands)",
      bash_cmds("awk '{print; exit}' f") == {"awk"}
      and bash_cmds("python3 -c 'import os; os.getcwd()'") == {"python3"})
check("bash_cmds: substitution in double quotes runs; in single quotes it does not",
      bash_cmds('echo "$(date)"') == {"echo", "date"} and bash_cmds("grep -n '$(foo' x") == {"grep"})
check("bash_cmds: a for-loop variable is not a command",
      bash_cmds("for f in a b; do cargo t; done") == {"cargo"})
check("bash_cmds: case arms are patterns, not commands",
      bash_cmds("case $1 in\n  audit) cargo audit;;\n  *) echo no;;\nesac") == {"cargo", "echo"})
check("bash_cmds: escaped quotes inside double quotes do not end the string",
      bash_cmds('git commit -m "say \\"hi\\"; but nicely" && make') == {"git", "make"})
check("bash_cmds: substitution heads are filtered like segment heads",
      bash_cmds("N=$(for i in 1 2; do echo $i; done)") == {"echo"} and bash_cmds("echo '$(...' ") == {"echo"})
check("bash_cmds: 2>&1 / &> are redirects, not separators",
      bash_cmds("cargo build 2>&1 | tail -40") == {"cargo", "tail"}
      and bash_cmds("make &>log && ls") == {"make", "ls"})
check("bash_cmds: comments are prose — their `;`/apostrophes never corrupt the parse",
      bash_cmds("# strip artifacts; keep src\nrm -rf x") == {"rm"}
      and bash_cmds("# don't split here\ngrep 'a|b' f") == {"grep"})
check("bash_cmds: a quoted path with spaces is one (unsplittable) head, not fabricated words",
      bash_cmds('"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless x') == set())
check("bash_cmds: a backtick substitution AND the command after it are both captured",
      bash_cmds("VERSION=`cat VERSION` ./deploy.sh") == {"cat", "deploy.sh"})
check("bash_cmds: a bare backtick substitution contributes its head",
      bash_cmds("echo `git rev-parse HEAD`") == {"echo", "git"})

r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture", "--transcripts", "fixture/transcripts"],
                   capture_output=True, text=True)
check("drift: granted-but-unused named per agent (researcher Net)",
      "researcher: granted-but-unused {Net}" in r.stdout)
check("drift: never-observed agents flagged", "mailer: declared" in r.stdout and "NEVER OBSERVED" in r.stdout)
check("drift: uncurated-MCP observation surfaced", "mcp-uncurated:mystery" in r.stdout)
check("drift: advisory by default (exit 0)", r.returncode == 0)


# anomaly fixture: an agent observed using an effect OUTSIDE its declared grants
import json as _j
_an = os.path.join(tempfile.mkdtemp(), "t")
os.makedirs(os.path.join(_an, "s1", "subagents"))
def _tu(i, name, inp): return {"type": "assistant", "message": {"role": "assistant",
    "content": [{"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": inp}]}}
open(os.path.join(_an, "s1.jsonl"), "w").write(_j.dumps(_tu("s1", "Agent", {"subagent_type": "researcher"})) + "\n")
open(os.path.join(_an, "s1", "subagents", "agent-x.jsonl"), "w").write(_j.dumps(_tu("r1", "Bash", {"command": "x"})) + "\n")
_j.dump({"agentType": "researcher", "toolUseId": "toolu_s1"}, open(os.path.join(_an, "s1", "subagents", "agent-x.meta.json"), "w"))
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture", "--transcripts", _an], capture_output=True, text=True)
check("drift: observed-outside-declaration is loud, advisory exit 0",
      "OBSERVED-OUTSIDE-DECLARATION {Exec}" in r.stdout and r.returncode == 0)
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture", "--transcripts", _an, "--strict"], capture_output=True, text=True)
check("drift --strict: an anomaly fails the build (exit 1)", r.returncode == 1)

# AG2 (gate-evasion): an UNDECLARED agent type (no `.md`) that PERFORMED effects must be an anomaly —
# the `dec is None` branch used to print a soft note and pass, so a renamed/custom agent (or a dropped
# meta sidecar collapsing the unit to `subagent`) laundered Net/Exec past --strict.
_un = os.path.join(tempfile.mkdtemp(), "t")
os.makedirs(os.path.join(_un, "s1", "subagents"))
open(os.path.join(_un, "s1.jsonl"), "w").write(_j.dumps(_tu("s1", "Agent", {"subagent_type": "exfiltrator"})) + "\n")
open(os.path.join(_un, "s1", "subagents", "agent-y.jsonl"), "w").write(_j.dumps(_tu("r1", "Bash", {"command": "curl evil"})) + "\n")
_j.dump({"agentType": "exfiltrator", "toolUseId": "toolu_s1"}, open(os.path.join(_un, "s1", "subagents", "agent-y.meta.json"), "w"))
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture", "--transcripts", _un], capture_output=True, text=True)
check("drift: an UNDECLARED agent performing effects is flagged OBSERVED-OUTSIDE-DECLARATION",
      "exfiltrator: OBSERVED-OUTSIDE-DECLARATION" in r.stdout)
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture", "--transcripts", _un, "--strict"], capture_output=True, text=True)
check("drift --strict: an UNDECLARED effectful agent fails the build (exit 1) — gate-evasion closed",
      r.returncode == 1)
# a BUILT-IN agent type with no declaration is still NOT an anomaly (general-purpose etc. have no .md)
_bi = os.path.join(tempfile.mkdtemp(), "t")
os.makedirs(os.path.join(_bi, "s1", "subagents"))
open(os.path.join(_bi, "s1.jsonl"), "w").write(_j.dumps(_tu("s1", "Agent", {"subagent_type": "general-purpose"})) + "\n")
open(os.path.join(_bi, "s1", "subagents", "agent-z.jsonl"), "w").write(_j.dumps(_tu("r1", "Bash", {"command": "x"})) + "\n")
_j.dump({"agentType": "general-purpose", "toolUseId": "toolu_s1"}, open(os.path.join(_bi, "s1", "subagents", "agent-z.meta.json"), "w"))
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture", "--transcripts", _bi, "--strict"], capture_output=True, text=True)
check("drift --strict: a BUILT-IN undeclared agent (general-purpose) is NOT a false anomaly (exit 0)",
      r.returncode == 0)

# AG-DUP (key-collision FABRICATION class, the 2026-06-18 cross-engine sweep): two agent files with the
# SAME `name:` used to SILENTLY CLOBBER (`agents[name] = …`) — only the survivor's contract reached drift,
# LAUNDERING the dropped agent's stricter contract (a pure `worker` observed running shell read CLEAN, the
# cardinal silent miss). Now BOTH are kept, disambiguated by file (`name#file`), so neither holds the bare
# name and an observed bare `worker` routes to OBSERVED-OUTSIDE-DECLARATION (the safe flag).
_dup = tempfile.mkdtemp()
_da = os.path.join(_dup, ".claude", "agents"); os.makedirs(_da)
open(os.path.join(_da, "a-pure.md"), "w").write(agent("worker", "Read"))   # pure worker — no Exec
open(os.path.join(_da, "z-priv.md"), "w").write(agent("worker", "Bash"))   # privileged worker — Exec
r = subprocess.run([sys.executable, "-m", "candor_agents.scan", _dup, "--out", os.path.join(_dup, "d"), "--fleet", "F"], capture_output=True, text=True)
_units = {e["fn"] for e in _j.load(open(os.path.join(_dup, "d.F.Fleet.json")))["functions"]}
check("scan: duplicate agent names are BOTH kept, disambiguated (no silent clobber)",
      "worker#a-pure.md" in _units and "worker#z-priv.md" in _units and "worker" not in _units, _units)
check("scan: the duplicate-name collision is WARNED on stderr", "duplicate agent name `worker`" in r.stderr)
_dtx = os.path.join(tempfile.mkdtemp(), "t"); os.makedirs(os.path.join(_dtx, "s1", "subagents"))
open(os.path.join(_dtx, "s1.jsonl"), "w").write(_j.dumps(_tu("s1", "Agent", {"subagent_type": "worker"})) + "\n")
open(os.path.join(_dtx, "s1", "subagents", "agent-w.jsonl"), "w").write(_j.dumps(_tu("r1", "Bash", {"command": "rm -rf /x"})) + "\n")
_j.dump({"agentType": "worker", "toolUseId": "toolu_s1"}, open(os.path.join(_dtx, "s1", "subagents", "agent-w.meta.json"), "w"))
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", _dup, "--transcripts", _dtx, "--strict"], capture_output=True, text=True)
check("drift --strict: a same-named-agent collision no longer launders a violation (exit 1, flagged)",
      r.returncode == 1 and "worker: OBSERVED-OUTSIDE-DECLARATION" in r.stdout, r.stdout)

# drift must NOT call a command/cron/session a "trim candidate agent" — they aren't agents and aren't
# recorded as distinct units in transcripts (the bug: every declared unit got the agent-trim advice).
_dd = tempfile.mkdtemp()
os.makedirs(os.path.join(_dd, ".claude", "agents")); os.makedirs(os.path.join(_dd, ".claude", "commands"))
os.makedirs(os.path.join(_dd, ".transcripts"))
open(os.path.join(_dd, ".claude", "agents", "a.md"), "w").write(agent("a", "WebFetch"))
open(os.path.join(_dd, ".claude", "commands", "c.md"), "w").write("---\nallowed-tools: Bash(curl:*)\n---\nx\n")
_j.dump([{"id": "n", "cron": "0 9 * * *", "prompt": "go", "durable": True}], open(os.path.join(_dd, ".claude", "scheduled_tasks.json"), "w"))
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", _dd, "--transcripts", os.path.join(_dd, ".transcripts")], capture_output=True, text=True)
check("drift: command:/cron:/session are 'not a trim candidate', a real agent still is",
      "command:c: declared" in r.stdout and "not a trim candidate" in r.stdout
      and "cron:n" in r.stdout and "a: declared {Net} but NEVER OBSERVED" in r.stdout, r.stdout)

# ── hooks: settings.json commands are fleet capability surface ────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    adir = os.path.join(td, ".claude", "agents")
    os.makedirs(adir)
    open(os.path.join(adir, "reader.md"), "w").write(agent("reader", "Read, Grep"))   # cannot trigger Write|Edit
    open(os.path.join(adir, "writer.md"), "w").write(agent("writer", "Read, Edit"))    # CAN trigger Write|Edit
    json.dump({"hooks": {
        "PostToolUse": [{"matcher": "Write|Edit",
                         "hooks": [{"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/.claude/check.py"}]}],
        "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash hook.sh && git status"},
                                            {"type": "telepathy"}]}],
    }}, open(os.path.join(td, ".claude", "settings.json"), "w"))
    out = os.path.join(td, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", td, "--out", out, "--fleet", "t"], capture_output=True, text=True)
    rep = json.load(open(f"{out}.t.Fleet.json"))
    hk = entry(rep, "hooks")
    check("hooks: a `hooks` unit carries the settings.json commands (Exec + cmds surface)",
          hk is not None and "Exec" in hk["direct"]
          and set(hk.get("cmds", [])) >= {"python3", "bash", "git"}, json.dumps(hk))
    check("hooks: an unknown hook type reads Unknown with a named origin",
          "Unknown" in hk["inferred"] and any(w.startswith("hook-type:") for w in hk.get("unknownWhy", [])))
    check("hooks: the session root edges to hooks and inherits Exec",
          "hooks" in entry(rep, "session")["calls"] and "Exec" in entry(rep, "session")["inferred"])
    check("hooks: the receipt discloses that hooks run automatically",
          "run AUTOMATICALLY" in r.stderr and "Stop(1)" in r.stderr, r.stderr)
    # the matcher-aware per-agent reach: an agent whose tools MATCH the PostToolUse matcher edges to
    # hooks and inherits Exec; one whose tools can't trigger it does NOT.
    wr, rd = entry(rep, "writer"), entry(rep, "reader")
    check("hooks: a matching agent (Edit) edges to hooks and inherits Exec",
          wr is not None and "hooks" in wr["calls"] and "Exec" in wr["inferred"], json.dumps(wr))
    check("hooks: a non-matching agent (Read/Grep) does NOT edge to a Write|Edit hook",
          rd is not None and "hooks" not in rd["calls"] and "Exec" not in rd["inferred"], json.dumps(rd))

# tools_match_matcher mirrors Claude Code's THREE-TIER matcher semantics (hooks reference). The
# tier-2 PARTIAL regex was the bug: force-anchoring `^(?:m)$` made `^Notebook` / `Edit$` UNDER-match,
# silently dropping a hook's Exec reach from an agent the harness really fires the hook on.
from candor_agents.scan import tools_match_matcher as tmm
check("matcher tier-1: exact `|`-list fires only on listed tools",
      tmm(["Edit"], "Edit|Write") and not tmm(["MultiEdit"], "Edit|Write"))
check("matcher tier-1: `Edit` does NOT fire on `MultiEdit` (exact, not substring)",
      tmm(["Edit"], "Edit") and not tmm(["MultiEdit"], "Edit"))
check("matcher tier-2: `^Notebook` (prefix regex, search) DOES fire on NotebookEdit",
      tmm(["NotebookEdit"], "^Notebook") and tmm(["NotebookRead"], "^Notebook"))
check("matcher tier-2: `Edit$` (suffix regex) fires on MultiEdit",
      tmm(["MultiEdit"], "Edit$") and tmm(["NotebookEdit"], "Edit$"))
check("matcher tier-2: `mcp__.*` fires on a namespaced MCP tool",
      tmm(["mcp__github__create_issue"], "mcp__.*"))
check("matcher tier-3: empty and `*` match all; ambient (None tools) matches any matcher",
      tmm(["X"], "") and tmm(["X"], "*") and tmm(None, "Bash"))
check("matcher: an unparseable regex does NOT fabricate an edge (under-report posture)",
      not tmm(["Edit"], "Edit("))

# a project with hooks but NO agents still has a capability surface (the pgman shape)
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, ".claude"))
    json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "./stop-hook.sh"}]}]}},
              open(os.path.join(td, ".claude", "settings.json"), "w"))
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", td, "--out", os.path.join(td, "r"), "--fleet", "t"],
                       capture_output=True, text=True)
    rep = json.load(open(os.path.join(td, "r.t.Fleet.json")))
    check("hooks: an agent-less project with hooks still scans (hooks + session)",
          r.returncode == 0 and entry(rep, "hooks") is not None
          and "stop-hook.sh" in entry(rep, "hooks").get("cmds", []), r.stderr)
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, ".claude"))
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", td, "--out", os.path.join(td, "r"), "--fleet", "t"],
                       capture_output=True, text=True)
    check("hooks: no agents AND no hooks still exits 2 (nothing to analyze)", r.returncode == 2)

# a real agent named `hooks` or `session` must NOT be clobbered by the synthetic units
with tempfile.TemporaryDirectory() as td:
    adir = os.path.join(td, ".claude", "agents"); os.makedirs(adir)
    open(os.path.join(adir, "hooks.md"), "w").write(agent("hooks", "WebFetch"))
    open(os.path.join(adir, "session.md"), "w").write(agent("session", "Grep"))
    json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "./h.sh"}]}]}},
              open(os.path.join(td, ".claude", "settings.json"), "w"))
    out = os.path.join(td, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", td, "--out", out, "--fleet", "t"], capture_output=True, text=True)
    rep = json.load(open(f"{out}.t.Fleet.json"))
    eh, es = entry(rep, "hooks"), entry(rep, "session")
    check("reserved-name: an agent named `hooks` keeps its own grant (Net), not clobbered to Exec",
          eh is not None and eh["direct"] == ["Net"] and eh["unitKind"] == "agent", json.dumps(eh))
    check("reserved-name: an agent named `session` keeps its Grep grant, not the ambient root set",
          es is not None and es["direct"] == ["Fs"] and es["unitKind"] == "agent", json.dumps(es))
    check("reserved-name: the synthetic units get disambiguated names + a warning",
          entry(rep, "hooks-unit") is not None and entry(rep, "session-root") is not None
          and "to avoid clobbering" in r.stderr, r.stderr)

# drift must NOT call an unobserved hooks unit a trim candidate (hook runs aren't tool_use events)
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, ".claude"))
    json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "./h.sh"}]}]}},
              open(os.path.join(td, ".claude", "settings.json"), "w"))
    tdir = os.path.join(td, "tr"); os.makedirs(tdir)
    open(os.path.join(tdir, "s.jsonl"), "w").write(json.dumps({"message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}}]}}) + "\n")
    r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", td, "--transcripts", tdir],
                       capture_output=True, text=True)
    check("drift: hooks are 'not observable', never a trim candidate",
          "hook runs are not observable" in r.stdout and "hooks: declared {Exec} but NEVER" not in r.stdout,
          r.stdout)

# drift CLI guards: unknown flags fail, a value-less --transcripts fails (not IndexError), and the
# target isn't dropped when it equals the transcripts value.
def driftcli(*a):
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", *a], capture_output=True, text=True)
check("drift: an unknown flag fails with exit 2 (not a silent non-strict run)",
      driftcli("fixture", "--strcit", "--transcripts", "fixture/transcripts").returncode == 2)
check("drift: a value-less trailing --transcripts fails cleanly (no IndexError)",
      driftcli("fixture", "--transcripts").returncode == 2)
r = driftcli("fixture", "--transcripts", "fixture")  # target == transcripts value
check("drift: a target equal to the transcripts value is not dropped to '.'",
      "fleet `fixture`" in r.stdout or "no agent definitions under fixture" in (r.stdout + r.stderr), r.stdout + r.stderr)

# drift is a COMPARISON, not a gate run: a standing CANDOR_POLICY (or a checked-in .candor/config
# policy) must NOT gate drift's INTERNAL scan/observe — it made the internal scan exit 1 on any
# violation, and drift aborted as a scan error before comparing anything (review find). The child
# env is scrubbed + marked gate-free.
_dpol = os.path.join(tempfile.mkdtemp(), "deny-net"); open(_dpol, "w").write("deny Net\n")
_denv = dict(os.environ); _denv["CANDOR_POLICY"] = _dpol
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", "fixture",
                    "--transcripts", "fixture/transcripts"], capture_output=True, text=True, env=_denv)
check("drift: a standing $CANDOR_POLICY does NOT abort drift's internal scan (the comparison runs, exit 0)",
      r.returncode == 0 and "drift — declared" in r.stdout and "AS-EFF-006" not in r.stderr,
      f"rc={r.returncode} err={r.stderr[-200:]!r}")
_dcd = tempfile.mkdtemp()
os.makedirs(os.path.join(_dcd, ".claude", "agents")); os.makedirs(os.path.join(_dcd, ".candor"))
open(os.path.join(_dcd, ".claude", "agents", "n.md"), "w").write(agent("n", "WebFetch"))
open(os.path.join(_dcd, ".candor", "config"), "w").write("policy fleet.policy\n")
open(os.path.join(_dcd, "fleet.policy"), "w").write("deny Net\n")
_dtd = tempfile.mkdtemp(); open(os.path.join(_dtd, "s.jsonl"), "w").write("")
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "drift", _dcd, "--transcripts", _dtd],
                   capture_output=True, text=True)
check("drift: a checked-in .candor/config policy does not abort drift either (gate-free children)",
      r.returncode == 0 and "drift — declared" in r.stdout, f"rc={r.returncode} err={r.stderr[-200:]!r}")
check("drift: the SAME config still gates a direct scan (only drift's children are gate-free)",
      subprocess.run([sys.executable, "-m", "candor_agents.scan", _dcd, "--out",
                      os.path.join(_dcd, "r")], capture_output=True, text=True).returncode == 1)

# transcript slug flattens ALL non-alphanumerics (not just / and .) — verify by planting a fake
# project transcript dir under a temp HOME for an underscore-named target and confirming it resolves.
with tempfile.TemporaryDirectory() as home:
    proj = os.path.join(home, "git", "my_app")
    os.makedirs(proj)
    slug = "-" + os.path.join(home, "git", "my-app").lstrip("/").replace("/", "-")
    # the real slug flattens '_' -> '-' too; build it the same way observe should
    import re as _re
    slug = _re.sub(r"[^a-zA-Z0-9]", "-", os.path.abspath(proj))
    pdir = os.path.join(home, ".claude", "projects", slug); os.makedirs(pdir)
    open(os.path.join(pdir, "s.jsonl"), "w").write("")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        import importlib; from candor_agents import observe as _obs
        importlib.reload(_obs)
        check("transcript slug flattens '_' (underscore project resolves its transcripts)",
              _obs.transcript_dir_for(proj) == pdir, f"{_obs.transcript_dir_for(proj)} != {pdir}")
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        import importlib; from candor_agents import observe as _obs
        importlib.reload(_obs)

print()


# ── --agents: the self-describing engine (the contract is an embedded module) ─────────────────────
from candor_agents import agentsmd
check("embedded contract matches AGENTS.md (drift gate — regen: python3 gen-agentsmd.py)",
      agentsmd.AGENTS_MD == open(os.path.join(HERE, "AGENTS.md")).read())
check("the wheel ships the candor_agents package (so agentsmd + the modules are included, no top-level clash)",
      '"candor_agents"' in open(os.path.join(HERE, "pyproject.toml")).read()
      and "py-modules" not in open(os.path.join(HERE, "pyproject.toml")).read()
      and os.path.exists(os.path.join(HERE, "candor_agents", "agentsmd.py")))
r = subprocess.run([sys.executable, "-m", "candor_agents.cli", "--agents"], capture_output=True, text=True)
# The expected header is DERIVED from the installed VERSION, not hardcoded. It used to read
# `startswith("<!-- candor-agents 0.25")`, which is a version-coupled assertion: every release breaks it,
# and the fix each time is to edit a literal in a test — the same hand-edit class that cost the 0.25
# release. Found by REHEARSING the 0.26 bump (release-stage.sh) and running the suite against it.
_expect_hdr = "<!-- candor-%s ·" % __import__("candor_agents.scan", fromlist=["VERSION"]).VERSION.replace("-", " ", 1)
check("--agents prints the version header + the exact installed contract",
      r.returncode == 0 and r.stdout.startswith(_expect_hdr)
      and r.stdout.endswith(agentsmd.AGENTS_MD), r.stdout[:120])

# ══ permissions.deny (sound subtraction) + slash-commands/skills (0.4.7) ══════════════════════════
def build(agents_files=None, settings=None, commands=None, skills=None, mcp=None, crons=None):
    """A full project: agents + .claude/settings.json + .claude/commands + .claude/skills + .mcp.json
    + .claude/scheduled_tasks.json."""
    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    for fn, c in (agents_files or {}).items():
        open(os.path.join(adir, fn), "w").write(c)
    if settings is not None:
        json.dump(settings, open(os.path.join(d, ".claude", "settings.json"), "w"))
    if crons is not None:
        json.dump(crons, open(os.path.join(d, ".claude", "scheduled_tasks.json"), "w"))
    for rel, c in (commands or {}).items():
        p = os.path.join(d, ".claude", "commands", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(c)
    for name, c in (skills or {}).items():
        sp = os.path.join(d, ".claude", "skills", name)
        os.makedirs(sp, exist_ok=True)
        open(os.path.join(sp, "SKILL.md"), "w").write(c)
    if mcp is not None:
        json.dump({"mcpServers": {s: {} for s in mcp}}, open(os.path.join(d, ".mcp.json"), "w"))
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "t"], capture_output=True, text=True)
    rep = json.load(open(f"{out}.t.Fleet.json")) if r.returncode == 0 else None
    return rep, r

# a WHOLE-tool deny is hard-enforced by the harness → candor SUBTRACTS the effect
rep, r = build({"net.md": agent("net", "WebFetch, Read")}, settings={"permissions": {"deny": ["WebFetch"]}})
e = entry(rep, "net")
check("deny WebFetch subtracts Net but keeps Fs (whole-tool deny is sound to remove)",
      e is not None and "Net" not in e["inferred"] and "Fs" in e["inferred"], json.dumps(e))
check("deny: the receipt names what was removed from the surface",
      "permissions.deny removed" in r.stderr and "WebFetch" in r.stderr, r.stderr)

# a SCOPED deny removes only a subset of uses — the tool stays usable, so it is NOT subtracted
rep, r = build({"sh.md": agent("sh", "Bash")}, settings={"permissions": {"deny": ["Bash(curl:*)"]}})
e = entry(rep, "sh")
check("scoped deny Bash(curl:*) does NOT remove Exec (Bash stays usable — the cliff)",
      e is not None and "Exec" in e["inferred"], json.dumps(e))
check("scoped deny is disclosed as seen-but-not-subtracted",
      "scoped" in r.stderr and "Bash(curl:*)" in r.stderr, r.stderr)

# a whole-server mcp deny (`mcp__server`) removes that server's effects
rep, r = build({"gh.md": agent("gh", "mcp__github__create_pr")}, settings={"permissions": {"deny": ["mcp__github"]}})
check("deny mcp__github removes the github server (the only unit becomes pure → omitted)",
      entry(rep, "gh") is None, json.dumps(rep["functions"]))

# denying the Agent tool removes delegation (the edge-maker is gone)
rep, r = build({"boss.md": agent("boss", "Agent", body="Spawn the `worker`."),
                "worker.md": agent("worker", "WebFetch")}, settings={"permissions": {"deny": ["Agent"]}})
b = entry(rep, "boss")
check("deny Agent removes delegation (boss no longer inherits the worker's Net)",
      b is None or "Net" not in b["inferred"], json.dumps(b))

# hooks BYPASS the permission system — a `deny Bash` must not strip the hooks unit's Exec
rep, r = build({"a.md": agent("a", "Read")},
               settings={"permissions": {"deny": ["Bash"]},
                         "hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash run.sh"}]}]}})
hk = entry(rep, "hooks")
check("hooks bypass permissions: deny Bash leaves the hooks unit's Exec intact",
      hk is not None and "Exec" in hk["inferred"], json.dumps(hk))

# a slash command is a unit; effects come from its allowed-tools (specifier stripped to base tool)
rep, r = build(commands={"deploy.md": "---\nallowed-tools: Bash(kubectl:*), Read\n---\nDeploy it.\n"})
e = entry(rep, "command:deploy")
check("a slash command becomes a unit carrying its allowed-tools effects",
      e is not None and e["unitKind"] == "command" and set(e["inferred"]) == {"Exec", "Fs"}, json.dumps(e))
check("the session root invokes the command",
      "command:deploy" in entry(rep, "session")["calls"])

# a `!`-shell line in a command body surfaces Exec + the literal command head
rep, r = build(commands={"st.md": "---\nallowed-tools: Bash(git status:*)\n---\nStatus:\n!`git status`\n"})
e = entry(rep, "command:st")
check("a command's !-shell surfaces Exec + the literal command head",
      e is not None and "Exec" in e["inferred"] and "git" in e.get("cmds", []), json.dumps(e))

# absent allowed-tools + no shell = a PURE command (omitted) — the OPPOSITE of an agent's absent tools:
rep, r = build(agents_files={"a.md": agent("a", "Read")},
               commands={"note.md": "---\ndescription: just a prompt\n---\nWrite a haiku.\n"})
check("a prompt-only command (no allowed-tools, no shell) is pure → omitted, NOT ambient",
      entry(rep, "command:note") is None, json.dumps(rep["functions"]))

# a skill is a unit from its allowed-tools
rep, r = build(skills={"fetcher": "---\nname: fetcher\nallowed-tools: WebFetch\n---\nFetch.\n"})
e = entry(rep, "skill:fetcher")
check("a skill becomes a unit carrying its allowed-tools effects",
      e is not None and e["unitKind"] == "skill" and e["inferred"] == ["Net"], json.dumps(e))
# a skill's Bash specifier head refines the Exec cliff too — SAME as a command (was a gap)
rep, r = build(skills={"runner": "---\nname: runner\nallowed-tools: Bash(curl:*)\n---\nRun.\n"})
e = entry(rep, "skill:runner")
check("a skill's Bash(curl:*) head refines the cliff: Exec + Net (consistent with commands)",
      e is not None and set(e["inferred"]) == {"Exec", "Net"} and "curl" in e.get("cmds", []), json.dumps(e))

# permissions.deny applies to commands too
rep, r = build(commands={"f.md": "---\nallowed-tools: WebFetch\n---\nFetch.\n"},
               settings={"permissions": {"deny": ["WebFetch"]}})
check("permissions.deny applies to a command (WebFetch-only command → pure → omitted)",
      entry(rep, "command:f") is None, json.dumps(rep["functions"]))

# ══ scheduled tasks: autonomous entry points (.claude/scheduled_tasks.json) ════════════════════════
# a durable cron job is a `cron:<id>` entry point that drives a full session → inherits its reach
rep, r = build(agents_files={"net.md": agent("net", "WebFetch")},
               crons=[{"id": "nightly", "cron": "7 9 * * *", "prompt": "fetch the digest", "durable": True}])
e = entry(rep, "cron:nightly")
check("a durable scheduled task is a cron entry-point unit that drives the session",
      e is not None and e["unitKind"] == "cron" and e.get("entryPoint") is True and e["calls"] == ["session"],
      json.dumps(e))
check("the cron task inherits the session's transitive reach (its autonomous blast radius)",
      e is not None and "Net" in e["inferred"], json.dumps(e))
check("the cron loc carries the schedule expression",
      e is not None and "7 9 * * *" in e["loc"], json.dumps(e))

# a project with ONLY a scheduled task (no agents) still has an autonomous surface
rep, r = build(crons=[{"id": "solo", "cron": "0 * * * *", "prompt": "do the thing", "durable": True}])
check("a scheduled-task-only project scans (the session+cron autonomous surface)",
      rep is not None and entry(rep, "cron:solo") is not None, r.stderr)

# no scheduled_tasks.json → no cron units
rep, r = build(agents_files={"a.md": agent("a", "Read")})
check("no scheduled_tasks.json → no cron units",
      rep is not None and not any(f["fn"].startswith("cron:") for f in rep["functions"]))

# a malformed scheduled_tasks.json is disclosed, not fatal
rep, r = build(agents_files={"a.md": agent("a", "Read")}, crons={"not": "a list of tasks"})
check("a malformed scheduled_tasks.json does not crash the scan (no cron units, scan still succeeds)",
      rep is not None and not any(f["fn"].startswith("cron:") for f in rep["functions"]), r.stderr)

# ══ Exec-cliff refinement: known sub-command heads classify (spec §4 ⟨0.5⟩) ═══════════════════════
# a known head ADDS its effect and keeps Exec (a subprocess was still spawned)
rep, r = build(commands={"net.md": "---\nallowed-tools: Bash(curl:*)\n---\nFetch:\n!`curl https://x`\n"})
e = entry(rep, "command:net")
check("a curl head refines the cliff: Exec + Net (Exec not dropped)",
      e is not None and set(e["inferred"]) == {"Exec", "Net"}, json.dumps(e))

# the candor self-case is spec-SUPPLIED (§7 item 12: analyzers do Fs/Env only); head from the Bash specifier
rep, r = build(commands={"c.md": "---\nallowed-tools: Bash(*candor-run.sh*)\n---\nRun candor over the code.\n"})
e = entry(rep, "command:c")
check("a candor-engine head classifies to Fs/Env (§7.12-supplied), head read from the Bash specifier",
      e is not None and set(e["inferred"]) == {"Exec", "Fs", "Env"} and "candor-run.sh" in e.get("cmds", []),
      json.dumps(e))

# an UNKNOWN head keeps the bare cliff — never fabricate an effect
rep, r = build(commands={"u.md": "---\nallowed-tools: Bash(mystery-tool:*)\n---\nRun it.\n"})
e = entry(rep, "command:u")
check("an unknown head keeps the bare Exec cliff (no fabricated effect)",
      e is not None and e["inferred"] == ["Exec"], json.dumps(e))

# a hook command's head refines the hooks unit too
rep, r = build({"a.md": agent("a", "Read")},
               settings={"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "curl https://x"}]}]}})
hk = entry(rep, "hooks")
check("a hook running curl refines the hooks unit: Exec + Net",
      hk is not None and "Net" in hk["inferred"] and "Exec" in hk["inferred"], json.dumps(hk))

# --link transitive bound: a command that only runs candor doesn't perform the analysed code's effects
def build_linked(cmds, code_effects):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".claude", "agents"))
    for rel, c in cmds.items():
        p = os.path.join(d, ".claude", "commands", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(c)
    json.dump({"candor": {"version": "x", "toolchain": "t", "spec": "0.7"}, "package": "code",
               "functions": [{"fn": "main", "inferred": code_effects, "direct": code_effects,
                              "entryPoint": True, "calls": [], "hash": "code#main"}]},
              open(os.path.join(d, "code.code.Main.json"), "w"))
    out = os.path.join(d, "r")
    rr = subprocess.run([sys.executable, "-m", "candor_agents.scan", d, "--out", out, "--fleet", "t", "--link", os.path.join(d, "code")],
                        capture_output=True, text=True)
    return (json.load(open(f"{out}.t.Fleet.json")) if rr.returncode == 0 else None), rr

rep, r = build_linked({"ca.md": "---\nallowed-tools: Bash(*candor-run.sh*)\n---\nRun candor.\n",   # only candor
                       "app.md": "---\nallowed-tools: Bash(myapp:*)\n---\nRun the app.\n"},        # unknown head
                      ["Net", "Db"])
ca, app = entry(rep, "command:ca"), entry(rep, "command:app")
check("--link: a command that only runs candor does NOT inherit the code's Net/Db (§4 transitive bound)",
      ca is not None and "Net" not in ca["inferred"] and "Db" not in ca["inferred"], json.dumps(ca))
check("--link: a command with an unknown head DOES inherit the code's effects (sound cliff kept)",
      app is not None and "Net" in app["inferred"], json.dumps(app))

# ══ curated-table growth: MCP servers + command heads ═════════════════════════════════════════════
rep, r = build(agents_files={"a.md": agent("a", "mcp__notion__search")}, mcp=["notion"])
check("a newly-curated MCP server (notion) classifies to Net, not Unknown",
      entry(rep, "a") is not None and entry(rep, "a")["inferred"] == ["Net"], json.dumps(entry(rep, "a")))
rep, r = build(agents_files={"a.md": agent("a", "mcp__mongodb__find")}, mcp=["mongodb"])
check("a newly-curated MCP server (mongodb) classifies to Db",
      entry(rep, "a") is not None and entry(rep, "a")["inferred"] == ["Db"], json.dumps(entry(rep, "a")))
# the posture HOLDS: an uncurated server still reads Unknown with a named origin
rep, r = build(agents_files={"a.md": agent("a", "mcp__weirdcustom__op")}, mcp=["weirdcustom"])
check("an uncurated MCP server still reads Unknown (the under-report posture holds)",
      entry(rep, "a") is not None and "Unknown" in entry(rep, "a")["inferred"]
      and any("weirdcustom" in w for w in entry(rep, "a").get("unknownWhy", [])), json.dumps(entry(rep, "a")))
# new command heads (the shared 5-engine table)
rep, r = build(commands={"t.md": "---\nallowed-tools: Bash(telnet:*)\n---\nx\n"})
check("new command head telnet refines to Net",
      "Net" in entry(rep, "command:t")["inferred"], json.dumps(entry(rep, "command:t")))
rep, r = build(commands={"m.md": "---\nallowed-tools: Bash(mongo:*)\n---\nx\n"})
check("new command head mongo refines to Db",
      "Db" in entry(rep, "command:m")["inferred"], json.dumps(entry(rep, "command:m")))

# ══ guard: compile a deny-policy into RUNTIME enforcement (may→enforced, roadmap #2) ═══════════════
from candor_agents import guard
g = guard.compile_guard("deny Net")
check("guard: deny Net compiles to permissions.deny of the Net-producing tools",
      set(g["deny"]) == {"WebFetch", "WebSearch"}, json.dumps(g))
check("guard: deny Net warns about the Exec cliff (a granted Bash can still curl)",
      any("Exec cliff" in w for w in g["warnings"]), json.dumps(g["warnings"]))
g2 = guard.compile_guard("deny Exec")
check("guard: deny Exec denies Bash with no cliff warning (it IS the subprocess effect)",
      g2["deny"] == ["Bash"] and not g2["warnings"], json.dumps(g2))
# real-world sweep find: `deny Net` + `deny Exec` together CLOSES the cliff (Bash is denied) → the
# "add deny Exec" warning must NOT fire (it did, misleadingly, on a real policy).
gne = guard.compile_guard("deny Net\ndeny Exec")
check("guard: deny Net + deny Exec closes the cliff → no misleading 'add deny Exec' warning",
      "Bash" in gne["deny"] and "WebFetch" in gne["deny"] and not gne["warnings"], json.dumps(gne))
g3 = guard.compile_guard("deny Net researcher")
check("guard: a scoped deny isn't project-wide-enforceable → a grant-tightening note, no deny emitted",
      not g3["deny"] and any("researcher" in n for n in g3["notes"]), json.dumps(g3))
# POSITIONAL parse, faithful to the engine (candor-classify policy.rs): the first non-effect token is
# the scope and ENDS the rule — `deny Net foo Db` is `deny Net in foo`, the trailing Db is NOT a rule.
# A set-membership partition would diverge here (treating Db as a second scoped deny the engine never
# gates), making guard an UNFAITHFUL dual.
g3p = guard.compile_guard("deny Net foo Db")
check("guard: positional parse — `deny Net foo Db` scopes Net to foo and drops the post-scope Db (engine-faithful)",
      not g3p["deny"] and len(g3p["notes"]) == 1 and "Net" in g3p["notes"][0]
      and not any("Db" in n for n in g3p["notes"]), json.dumps(g3p))
# and the unscoped multi-effect form is unaffected: every token an effect → all denied fleet-wide.
g3m = guard.compile_guard("deny Net Db")
check("guard: `deny Net Db` (all-effect, no scope) denies the producers of BOTH effects fleet-wide",
      "WebFetch" in g3m["deny"] and not g3m["notes"], json.dumps(g3m))
# deny Db: no built-in tool PRODUCES Db (only Bash/MCP) → a clear message, not "denies []"
gdb = guard.compile_guard("deny Db")
check("guard: deny Db (no built-in Db tool) explains it's reached via Bash/MCP, not 'denies []'",
      not gdb["deny"] and any("no built-in tool produces Db" in w for w in gdb["warnings"]), json.dumps(gdb))
# a configured Net MCP server is denied too (mcp__server)
_gd = tempfile.mkdtemp()
json.dump({"mcpServers": {"github": {}}}, open(os.path.join(_gd, ".mcp.json"), "w"))
g4 = guard.compile_guard("deny Net", project_dir=_gd)
check("guard: deny Net also denies a configured Net MCP server (mcp__github)",
      "mcp__github" in g4["deny"], json.dumps(g4))
# guard VALIDATES a declared candorEffects like scan (SPEC §5.1) — a typo voids+warns, not silent under-protect
_gv = tempfile.mkdtemp()
json.dump({"mcpServers": {"db": {"candorEffects": ["Database"]}}}, open(os.path.join(_gv, ".mcp.json"), "w"))
g5 = guard.compile_guard("deny Db", project_dir=_gv)
check("guard: a typo'd candorEffects voids loudly (not a silent under-protect)",
      "mcp__db" not in g5["deny"] and any("voided" in w and "db" in w for w in g5["warnings"]), json.dumps(g5))
_gw = tempfile.mkdtemp()
json.dump({"mcpServers": {"mydb": {"candorEffects": ["Db"]}}}, open(os.path.join(_gw, ".mcp.json"), "w"))
check("guard: a VALID candorEffects denies the declared server (mcp__mydb)",
      "mcp__mydb" in guard.compile_guard("deny Db", project_dir=_gw)["deny"])
# THE DUAL CLOSES THE LOOP: guard WRITES permissions.deny, scan READS it → the fleet loses the effect
rep, r = build(agents_files={"net.md": agent("net", "WebFetch")},
               settings={"permissions": {"deny": guard.compile_guard("deny Net")["deny"]}})
check("guard↔scan dual: the guard's permissions.deny makes scan show the fleet can no longer reach Net",
      entry(rep, "net") is None, json.dumps(rep["functions"]))

print()

# ══ regressions: config/transcript shapes a review found could crash or mis-disclose ═══════════════
# (1) settings.json that is valid JSON but the WRONG shape (a top-level list/scalar, or a non-dict
# permissions/hooks) must degrade-and-disclose, never crash the scan — a traceback wrote no report,
# so the architecture gate then silently did not run.
for _bad in ([1, 2, 3], {"permissions": [1, 2]}, {"hooks": "oops"}, "a string", 42):
    rep_b, r_b = build(agents_files={"a.md": agent("a", "WebFetch")}, settings=_bad)
    check(f"scan: malformed settings.json ({type(_bad).__name__}) degrades, doesn't crash",
          r_b.returncode == 0 and rep_b is not None and entry(rep_b, "a") is not None,
          (r_b.stderr or "")[-300:])
_, r_disc = build(agents_files={"a.md": agent("a", "WebFetch")}, settings=[1, 2, 3])
check("scan: a non-object settings.json is DISCLOSED (not a JSON object)",
      "not a JSON object" in (r_disc.stderr or ""), (r_disc.stderr or "")[-300:])

# (2) observe: a NESTED subagent (a subagent that itself spawns one) must be observed, not silently
# dropped (the cardinal sin); and a MISSING meta sidecar is the normal optional case, NOT an
# "unreadable file" in the best-effort-coverage receipt.
def _jl(path, name, inp=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"message": {"content": [{"type": "tool_use", "id": os.path.basename(path),
                                    "name": name, "input": inp or {}}]}}
    open(path, "w").write(json.dumps(rec) + "\n")
_otd = tempfile.mkdtemp()
_jl(os.path.join(_otd, "s.jsonl"), "Read", {"file_path": "/a"})                                  # session: Fs
_sa = os.path.join(_otd, "sess", "subagents")
_jl(os.path.join(_sa, "w.jsonl"), "WebFetch", {"url": "https://x"})                              # worker: Net
json.dump({"agentType": "worker"}, open(os.path.join(_sa, "w.meta.json"), "w"))
_jl(os.path.join(_sa, "w", "subagents", "deep.jsonl"), "Bash", {"command": "curl x"})           # NESTED: Exec
json.dump({"agentType": "digger"}, open(os.path.join(_sa, "w", "subagents", "deep.meta.json"), "w"))
_jl(os.path.join(_sa, "nometa.jsonl"), "Read", {"file_path": "/b"})                              # subagent, no sidecar
_oout = os.path.join(_otd, "o")
r_o = subprocess.run([sys.executable, "-m", "candor_agents.observe", _otd, "--transcripts", _otd, "--out", _oout, "--fleet", "t"],
                     capture_output=True, text=True)
obs_n = json.load(open(f"{_oout}.t.Observed.json"))
byn = {e["fn"]: e for e in obs_n["functions"]}
check("observe: a nested subagent (subagents/x/subagents/y) is observed, not dropped",
      "digger" in byn and "Exec" in byn["digger"]["inferred"], json.dumps(list(byn)))
check("observe: a missing meta sidecar is not reported as an unreadable file",
      "unreadable file" not in r_o.stderr, r_o.stderr)

# (3) guard: a miscased effect (`deny net`) enforces nothing — it must WARN, not silently no-op.
g_lc = guard.compile_guard("deny net")
check("guard: `deny net` (miscased) warns it's case-sensitive, not the effect `Net` (no silent no-op)",
      not g_lc["deny"] and any("case-sensitive" in w and "Net" in w for w in g_lc["warnings"]), json.dumps(g_lc))
# (4) guard: `deny Exec Db` must STILL disclose Db's residual reach — denying Bash doesn't suppress it.
g_ed = guard.compile_guard("deny Exec Db")
check("guard: deny Exec Db still discloses Db's residual reach (not suppressed by Bash being denied)",
      "Bash" in g_ed["deny"] and any("no built-in tool produces Db" in w for w in g_ed["warnings"]), json.dumps(g_ed))

# ── guard as a PROCESS surface (`candor-agents guard …` — cli.py dispatch + guard.main) ──────────
# The compile_guard logic above is unit-covered; this is the user-facing CLI contract (exit codes,
# the stdout fragment, stderr notes) — the exact CLI class that shipped a Critical bug untested.
def _guard_cli(*a):
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", "guard", *a],
                          capture_output=True, text=True, cwd=HERE)

_gpd = tempfile.mkdtemp(); _gpol = os.path.join(_gpd, "p"); open(_gpol, "w").write("deny Net\n")
_rg = _guard_cli(_gpol)
try:
    _gfrag = json.loads(_rg.stdout)
except Exception:
    _gfrag = None
check("guard CLI: `deny Net` → the settings.json fragment on STDOUT, shape {permissions:{deny:[…]}}, exit 0",
      _rg.returncode == 0 and _gfrag == {"permissions": {"deny": ["WebFetch", "WebSearch"]}},
      f"rc={_rg.returncode} out={_rg.stdout[:120]!r}")
check("guard CLI: the merge instruction + cliff warning go to STDERR (stdout stays pure JSON)",
      "merge into .claude/settings.json" in _rg.stderr and "Exec cliff" in _rg.stderr, _rg.stderr[-200:])
check("guard CLI: no args → usage to stderr, exit 2", _guard_cli().returncode == 2
      and "candor-agents guard" in _guard_cli().stderr)
check("guard CLI: -h → usage, exit 0 (asked for help)", _guard_cli("-h").returncode == 0)
_rgm = _guard_cli(os.path.join(tempfile.mkdtemp(), "no-such-policy"))
check("guard CLI: an unreadable policy exits 2 with the one-line diagnostic (never a silent empty fragment)",
      _rgm.returncode == 2 and "cannot read policy" in _rgm.stderr and _rgm.stdout == "",
      f"rc={_rgm.returncode} err={_rgm.stderr[-160:]!r}")
_gsc = os.path.join(_gpd, "scoped"); open(_gsc, "w").write("deny Net researcher\n")
_rgs2 = _guard_cli(_gsc)
check("guard CLI: a scoped-only policy → the no-fleet-deny note, NO fragment on stdout, exit 0",
      _rgs2.returncode == 0 and "no fleet-wide deny rule" in _rgs2.stderr and _rgs2.stdout == "",
      f"rc={_rgs2.returncode} out={_rgs2.stdout!r}")
# the project-dir positional flows through the cli dispatch to compile_guard (.mcp.json denies)
_gmp = tempfile.mkdtemp(); json.dump({"mcpServers": {"github": {}}}, open(os.path.join(_gmp, ".mcp.json"), "w"))
_rgp = _guard_cli(_gpol, _gmp)
check("guard CLI: the <project-dir> positional reaches compile_guard — mcp__github lands in the fragment",
      _rgp.returncode == 0 and "mcp__github" in json.loads(_rgp.stdout)["permissions"]["deny"], _rgp.stdout)
# guard takes NO flags: an unknown flag or an extra positional must exit 2, never be silently ignored
# (the gateless-ignore class — guard previously dropped both on the floor and emitted the fragment).
_rgf = _guard_cli(_gpol, "--bogus")
check("guard CLI: an unknown flag exits 2 (was silently ignored — the gateless-ignore class)",
      _rgf.returncode == 2 and "unknown flag" in _rgf.stderr, f"rc={_rgf.returncode} err={_rgf.stderr[-120:]!r}")
_rge = _guard_cli(_gpol, _gmp, "extra-arg")
check("guard CLI: an unexpected extra argument exits 2 (was silently ignored)",
      _rge.returncode == 2 and "unexpected extra argument" in _rge.stderr, f"rc={_rge.returncode}")

# ---- stats + savings: measured gate activity & the labelled savings model. Regression tests for the
#      max-effort review fixes: non-object/invalid lines, bool-as-int, --since vs null-ts, subagent walk,
#      crash-safety on corrupt transcripts, blast-based estimate, anchored query matching. ----
import atexit
import shutil
import tempfile
_tmpdirs = []
def _mkd():
    d = tempfile.mkdtemp(); _tmpdirs.append(d); return d
atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _tmpdirs])

_sd = _mkd()
os.makedirs(os.path.join(_sd, ".candor"), exist_ok=True)
with open(os.path.join(_sd, ".candor", "activity.jsonl"), "w") as _f:
    _f.write("\n".join([
        '{"ts":"2026-06-23T19:40:00Z","sessionId":"s1","engine":"java","edited":["src/A.java"],"gained":[],"blastRadius":0,"verdict":"clean","violations":[],"unknowns":6,"reviewMs":1000,"effects":["Db"]}',
        '{"ts":"2026-06-23T19:42:00Z","sessionId":"s1","engine":"java","edited":["src/B.java","src/C.java"],"gained":["Db"],"blastRadius":5,"maxHops":4,"verdict":"blocked","violations":["AS-EFF-006"],"unknowns":3,"reviewMs":2000,"effects":["Db"]}',
        '5',                                   # valid JSON, NOT an object -> skip, never crash (review #3)
        '{ corrupt — invalid json, skip }',    # invalid JSON -> skip
        '{"ts":"2026-06-23T20:10:00Z","sessionId":"s2","engine":"ts","edited":["x.ts"],"verdict":"clean","violations":[],"unknowns":0,"reviewMs":0,"effects":[]}',
        '{"sessionId":"s1","verdict":"clean","violations":[]}',          # null ts -> must NOT be dropped by --since (#8)
        '{"ts":"2026-06-23T19:50:00Z","sessionId":"s1","verdict":"clean","blastRadius":true,"unknowns":true,"violations":[]}',  # bool, not int (#16)
    ]) + "\n")
def _stats(*a):
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", "stats", *a], capture_output=True, text=True)
_sj = json.loads(_stats(_sd, "--json").stdout)
check("stats: skips invalid AND valid-non-object lines without crashing (#3)", _sj["turns"] == 5, json.dumps(_sj))
check("stats: clean/blocked verdicts counted", _sj["clean"] == 4 and _sj["blocked"] == 1, json.dumps(_sj))
check("stats: violations counted by AS-EFF code", _sj["violations"].get("AS-EFF-006") == 1, json.dumps(_sj))
check("stats: distinct files/sessions/max-blast; bool blastRadius NOT counted as 1 (#16)",
      _sj["filesTouched"] == 4 and _sj["sessions"] == 2 and _sj["largestBlastRadius"] == 5, json.dumps(_sj))
check("stats: deepestPropagation aggregated from maxHops (FEEDBACK-SPEC P2.2)",
      _sj["deepestPropagation"] == 4, json.dumps(_sj))
check("stats: unknownsMax + candorMs; a 0-reviewMs turn doesn't hide the line (#13)",
      _sj["unknownsMax"] == 6 and _sj["hasUnknowns"] is True and _sj["candorMs"] == 3000 and _sj["hasReviewMs"] is True, json.dumps(_sj))
check("stats: effects-present (trailer) surfaced, distinct from effects-introduced (#7)",
      _sj["effectsPresent"] == ["Db"], json.dumps(_sj))
check("stats: --since drops older records but KEEPS the null-ts one (#8)",
      json.loads(_stats(_sd, "--since", "2026-06-23T19:41:00Z", "--json").stdout)["turns"] == 4, "")
check("stats: --session filters to one session",
      json.loads(_stats(_sd, "--session", "s2", "--json").stdout)["turns"] == 1)
_rm = _stats("/tmp/candor-no-such-dir-xyz")
check("stats: missing log is a clean no-op (exit 0)",
      _rm.returncode == 0 and "no activity log" in _rm.stdout, _rm.stdout + _rm.stderr)
check("stats: unknown flag exits 2", _stats(_sd, "--bogus").returncode == 2)

# ── the DEFAULT human output (_print_human) over the same real-shaped log — the path every user
# without --json hits; it had never executed. Pin the key lines, incl. the presence-gated ones.
_rh = _stats(_sd)
check("stats human: the default path renders without crashing, exit 0",
      _rh.returncode == 0 and "Traceback" not in _rh.stderr, _rh.stderr[-160:])
for _line in ("gate activity", "span: 2026-06-23T19:40:00Z → 2026-06-23T20:10:00Z", "2 session(s)",
              "5 edits checked — 4 clean, 1 blocked, 0 setup",
              "blocked by policy: AS-EFF-006 ×1",
              "effects introduced this period: Db",
              "effects present in the code: Db",
              "largest blast radius seen: 5 function(s)",
              "deepest propagation seen: 4 hop(s) from a new source",
              "Unknowns disclosed (max in a turn): 6",
              "files touched: 4",
              "candor's own time: 3.0s across 5 checks"):
    check(f"stats human: prints {_line!r}", _line in _rh.stdout, _rh.stdout)
# a log WITHOUT the optional unknowns/reviewMs fields: those lines are ABSENT (presence-gated, not
# zero-rendered) and the rest still renders
_sd2 = _mkd(); os.makedirs(os.path.join(_sd2, ".candor"))
open(os.path.join(_sd2, ".candor", "activity.jsonl"), "w").write(
    '{"ts":"2026-06-23T19:40:00Z","sessionId":"s1","edited":["a.py"],"verdict":"clean","violations":[]}\n')
_rh2 = _stats(_sd2)
check("stats human: a log without unknowns/reviewMs renders WITHOUT those lines (no crash, no fake 0s)",
      _rh2.returncode == 0 and "1 edits checked" in _rh2.stdout
      and "Unknowns disclosed" not in _rh2.stdout and "candor's own time" not in _rh2.stdout, _rh2.stdout)
# an all-filtered log (0 matching turns) is the no-matching-activity line, not a crash on span[None]
check("stats human: 0 matching turns → the no-matching-activity line, exit 0",
      "no matching gate activity" in _stats(_sd, "--session", "no-such-session").stdout)
# flag-value errors: each value-taking flag at end of line exits 2 (never a silent default)
for _fl in ("--log", "--session", "--since"):
    check(f"stats: {_fl} with no value exits 2", _stats(_sd, _fl).returncode == 2)
# a non-ISO --since gets the lexical-compare warning (the compare may drop everything)
_rw = _stats(_sd, "--since", "yesterday", "--json")
check("stats: a non-ISO --since warns on stderr (lexical compare may drop everything)",
      _rw.returncode == 0 and "doesn't look like an ISO timestamp" in _rw.stderr, _rw.stderr)

_td = _mkd()
os.makedirs(os.path.join(_td, "subagents"), exist_ok=True)
def _ev(i, name, inp):
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
                       "content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}})
with open(os.path.join(_td, "s.jsonl"), "w") as _f:
    _f.write("\n".join([
        _ev("1", "Bash", {"command": "candor-query callers r.json Foo 1"}),   # blast
        _ev("2", "Bash", {"command": "candor-query where r.json Net 1"}),     # blast
        _ev("3", "Bash", {"command": "candor-query show r.json Foo 0"}),      # query, not blast
        _ev("4", "Bash", {"command": "echo 'see candor-query docs'"}),        # mention only -> NOT a query (#6)
        _ev("5", "Bash", {"command": "grep callers src/x.rs"}),               # NOT a query (#6)
        _ev("6", "Read", {"file_path": "/x"}),
        '{ corrupt half-written tail line }',                                  # must NOT crash savings (#2)
    ]) + "\n")
with open(os.path.join(_td, "subagents", "sub.jsonl"), "w") as _f:            # subagent transcript (#5)
    _f.write(_ev("7", "Bash", {"command": "candor-query callers r.json Bar 1"}) + "\n")
def _sv(*a):
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", "savings", *a], capture_output=True, text=True)
_svr = _sv("--transcript", _td, "--json")
check("savings: does NOT crash on a corrupt transcript line (#2)",
      _svr.returncode == 0 and _svr.stdout.strip().startswith("{"), _svr.stdout + _svr.stderr)
_vj = json.loads(_svr.stdout)
check("savings: counts real queries incl. subagent, ignores echo/grep mentions (#5,#6)",
      _vj["measured"]["queries"] == 4 and _vj["measured"]["blastRadiusQueries"] == 3, json.dumps(_vj))
check("savings: estimate is blast-based and flagged a model, not a measurement (#4)",
      _vj.get("modelled") is True and _vj["estimate"]["basis"] == "blastRadiusQueries", json.dumps(_vj))
_sv_out = _sv("--transcript", _td).stdout
check("savings: human output labels it a model and cites the benchmark",
      "model, not measured" in _sv_out and "candor.poly.io/agents" in _sv_out, _sv_out)
check("savings: no candor-query calls → clean no-op (exit 0)", _sv("--transcript", _mkd()).returncode == 0)
# the token-estimate block, incl. _h's millions formatting: 3 blast queries × 24k × (17−1) = 1,152,000
# → "~1.2M"; tool calls 3 × (50−1) = 147 → "~147"; the measured line counts 4 calls / 3 blast
check("savings human: the measured line counts calls and blast-radius queries",
      "4 call(s), 3 blast-radius (callers/where)" in _sv_out, _sv_out)
check("savings human: the estimate block renders _h in millions (~1.2M tokens) and ~147 tool calls",
      "~1.2M tokens" in _sv_out and "~147 tool calls" in _sv_out, _sv_out)
# blast == 0 with queries > 0: the explainer (benchmark covers only callers/where), exit 0, no estimate
_bd = _mkd()
open(os.path.join(_bd, "s.jsonl"), "w").write(_ev("1", "Bash", {"command": "candor-query show r.json Foo 0"}) + "\n")
_rb0 = _sv("--transcript", _bd)
check("savings human: queries but NO blast-radius ones → the nothing-to-model explainer, exit 0",
      _rb0.returncode == 0 and "No blast-radius (callers/where) queries" in _rb0.stdout
      and "tokens and" not in _rb0.stdout, _rb0.stdout)
# no transcripts at all (an empty project dir, no slug): the pointer note, exit 0 — both surfaces
_nv = _sv(_mkd())
check("savings human: no transcripts found → the pointer note, exit 0",
      _nv.returncode == 0 and "no transcripts found" in _nv.stdout
      and "point --transcript" in _nv.stdout, _nv.stdout)
_nvj = _sv(_mkd(), "--json")
check("savings --json: no transcripts found → {queries: 0, note}, exit 0",
      _nvj.returncode == 0 and json.loads(_nvj.stdout)["queries"] == 0
      and "no transcripts found" in json.loads(_nvj.stdout)["note"], _nvj.stdout)
# flag errors: a value-less --transcript and an unknown flag each exit 2
check("savings: --transcript with no value exits 2", _sv("--transcript").returncode == 2)
check("savings: unknown flag exits 2", _sv("--bogus").returncode == 2)
# _is_query's MCP-tool-name branch: a candor-query wired as an MCP tool counts; lookalikes don't
from candor_agents.savings import _is_query as _isq
check("savings _is_query: an MCP candor-query tool name matches (the non-Bash branch)",
      _isq("mcp__candor__query", {}) is True and _isq("mcp__candor-query__callers", None) is True)
check("savings _is_query: non-candor tools and Bash without a command don't match",
      _isq("mcp__github__search", {}) is False and _isq("Bash", {"cmd": "x"}) is False
      and _isq(None, {}) is False)

print()


# ══ CLI / gate behaviour matrix (spawn the engine; assert stdout/stderr/exit) ══════════════════════
# A Critical bug shipped because the CLI surface (--version/-V/--json/--policy/subcommand -h) was
# UNTESTED. These spawn `python3 -m candor_agents.cli` (the same entry the console_script `candor-agents`
# resolves to) and assert the contract the README/spec §3.3 promise: exit codes, that --json is the §2
# envelope on stdout with NO files written, that --policy gates with the right code, and that a
# subcommand's `-h` is a usage exit 0 — never swallowed as a scan target.
def cli(*a, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", *a],
                          capture_output=True, text=True, cwd=HERE, env=e)

# A minimal real fleet (one Net leaf) the matrix gates over, in a dir that holds NO pre-existing report.
_cd = _mkd()
os.makedirs(os.path.join(_cd, ".claude", "agents"))
open(os.path.join(_cd, ".claude", "agents", "leaf.md"), "w").write(agent("leaf", "WebFetch"))
_viol = _mkd(); _pv = os.path.join(_viol, "p"); open(_pv, "w").write("deny Net\n")          # leaf performs Net → violation
_clean = _mkd(); _pc = os.path.join(_clean, "p"); open(_pc, "w").write("deny Db\n")          # leaf has no Db → clean
_missing = os.path.join(_mkd(), "no-such-policy")                                            # never created

# --version / -V (spec §3.3): `candor-agents <ver> (candor-spec <X>)`, offline, exit 0. Top-level AND
# as a subcommand flag (scan/observe route -V/--version before their arg walk).
from candor_agents.scan import VERSION as _VER, SPEC as _SPEC
_vline = f"candor-agents {_VER} (candor-spec {_SPEC})"
for _form in (["--version"], ["-V"], ["scan", "--version"], ["observe", "-V"]):
    rv = cli(*_form)
    check(f"cli {' '.join(_form)}: prints `candor-agents <ver> (candor-spec <X>)`, exit 0",
          rv.returncode == 0 and rv.stdout.startswith(_vline), f"rc={rv.returncode} out={rv.stdout[:80]!r}")

# bare scan → a report to disk, exit 0 (the static default)
rb = cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--fleet", "t")
check("cli scan (bare) → report written, exit 0", rb.returncode == 0, rb.stderr[-200:])

# `scan -h` / `scan --help` is a USAGE exit 0 — NOT consumed as a scan target (the swallow bug class)
for _h in ("-h", "--help"):
    rh = cli("scan", _h)
    check(f"cli scan {_h}: usage to stderr, exit 0 (not swallowed as a target)",
          rh.returncode == 0 and "USAGE" in rh.stderr and "scan" in rh.stderr, f"rc={rh.returncode}")
rho = cli("observe", "-h")
check("cli observe -h: usage exit 0", rho.returncode == 0 and "USAGE" in rho.stderr, f"rc={rho.returncode}")
# top-level bare `-h` is exit 0 (asked for help); NO args is exit 2 (usage error) — the §3.3 split
check("cli -h (top-level): help to stderr, exit 0", cli("-h").returncode == 0)
check("cli (no args): usage error, exit 2", cli().returncode == 2)

# an unknown flag FAILS with exit 2 — never silently ignored (the gateless-ignore class)
ru = cli("scan", _cd, "--bogus")
check("cli scan --bogus: unknown flag exits 2 (not silently ignored)",
      ru.returncode == 2 and "unknown flag" in ru.stderr, f"rc={ru.returncode}")
check("cli observe --bogus: unknown flag exits 2", cli("observe", _cd, "--transcripts", _cd, "--bogus").returncode == 2)

# --json: stdout PARSES as the §2 envelope, human/progress on stderr, NO files written, exit 0
_jdir = _mkd(); os.makedirs(os.path.join(_jdir, ".claude", "agents"))
open(os.path.join(_jdir, ".claude", "agents", "leaf.md"), "w").write(agent("leaf", "WebFetch"))
rj = cli("scan", _jdir, "--json")
try:
    _jrep = json.loads(rj.stdout)
    _ok_json = (rj.returncode == 0 and _jrep["candor"]["spec"] == _SPEC
                and isinstance(_jrep["functions"], list) and _jrep["package"]
                and any(f["fn"] == "leaf" and f["inferred"] == ["Net"] for f in _jrep["functions"]))
except Exception as _e:
    _ok_json = False; _jrep = str(_e)
check("cli scan --json: stdout parses as the §2 envelope, exit 0", _ok_json, str(_jrep)[:160])
check("cli scan --json: writes NO report files (stdout-only)",
      not any(f.endswith(".json") for f in os.listdir(_jdir)), os.listdir(_jdir))
check("cli scan --json: the receipt/progress is on stderr (stdout stays pure JSON)",
      "stdout (--json)" in rj.stderr, rj.stderr[-160:])

# --policy: clean → exit 0; violating → exit 1; missing/unreadable → exit 2 (never a silent gate-pass)
check("cli scan --policy <clean>: exit 0",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pc).returncode == 0)
rpv = cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pv)
check("cli scan --policy <violating>: exit 1 with the AS-EFF-006 line on stderr",
      rpv.returncode == 1 and "AS-EFF-006" in rpv.stderr, f"rc={rpv.returncode} err={rpv.stderr[-160:]!r}")
check("cli scan --policy <missing>: exit 2 (gate NOT enforced, never a silent pass)",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _missing).returncode == 2)
# ⟨0.24⟩ …and a policy that PARSES but cannot be honoured AS WRITTEN takes the same posture. This
# engine used to drop `deny Frobnicate` with a stderr note and exit 0 printing `policy ✓` — the
# silently-rewritten-policy fail-open the four code engines refuse. The gate-json rows below cover the
# document; these two cover the exit code on both the file and the stream sink.
_pbad = os.path.join(_mkd(), "bad.policy"); open(_pbad, "w").write("deny Frobnicate\n")
_rbad = cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pbad)
check("cli scan --policy <unhonourable token>: exit 2, never a green `policy ✓` over a rewritten policy",
      _rbad.returncode == 2 and "cannot be honoured" in _rbad.stderr and "policy ✓" not in _rbad.stderr,
      f"rc={_rbad.returncode} err={_rbad.stderr[-200:]!r}")
_pbadc = os.path.join(_mkd(), "badclass.policy"); open(_pbadc, "w").write("deny Unknown[dispatch,nativ] t\n")
check("cli scan --policy <unrecognised reason-class>: exit 2 — a NARROWED rewrite is the dangerous one",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pbadc).returncode == 2)
_rbadj = cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pbad, "--gate-json", "-")
try:
    _bd = json.loads(_rbadj.stdout)
except Exception:
    _bd = {}
check("cli scan --policy <unhonourable> --gate-json -: the refusal document is stdout's only content",
      _rbadj.returncode == 2 and _bd.get("refused") is True and _bd.get("ok") is False
      and "violations" not in _bd, f"rc={_rbadj.returncode} out={_rbadj.stdout[:200]!r}")
# $CANDOR_POLICY is honoured when the flag is absent (the flag wins); a violation via the env exits 1
check("cli scan: $CANDOR_POLICY (no flag) gates too — a violation exits 1",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--fleet", "t",
          env={"CANDOR_POLICY": _pv}).returncode == 1)

# --json --policy <violating>: stdout is PURE JSON (violations go to stderr), exit 1 — a clean `| jq` pipe
rjp = cli("scan", _jdir, "--json", "--policy", _pv)
try:
    json.loads(rjp.stdout); _pure = True
except Exception:
    _pure = False
check("cli scan --json --policy <violating>: stdout stays pure JSON, violation on stderr, exit 1",
      rjp.returncode == 1 and _pure and "AS-EFF-006" in rjp.stderr, f"rc={rjp.returncode} out={rjp.stdout[:80]!r}")

# observe mirrors the same gate surface (the OBSERVED path — the one that shipped the crash). A tiny
# transcript with a single Net tool_use, gated by `deny Net`.
_otdir = _mkd()
open(os.path.join(_otdir, "s.jsonl"), "w").write(
    '{"message": {"content": [{"type": "tool_use", "id": "t1", "name": "WebFetch", '
    '"input": {"url": "https://x.test"}}]}}\n')
check("cli observe (bare) → report written, exit 0",
      cli("observe", _otdir, "--transcripts", _otdir, "--out", os.path.join(_mkd(), "o"), "--fleet", "t").returncode == 0)
roj = cli("observe", _otdir, "--transcripts", _otdir, "--json", "--fleet", "t")
try:
    _orep = json.loads(roj.stdout); _ojson_ok = roj.returncode == 0 and _orep.get("mode") == "observed"
except Exception:
    _ojson_ok = False
check("cli observe --json: stdout parses as the §2 observed envelope, exit 0", _ojson_ok, roj.stdout[:120])
rop = cli("observe", _otdir, "--transcripts", _otdir, "--out", os.path.join(_mkd(), "o"), "--fleet", "t", "--policy", _pv)
check("cli observe --policy <violating>: exit 1 (the OBSERVED gate enforces)",
      rop.returncode == 1 and "AS-EFF-006" in rop.stderr, f"rc={rop.returncode}")
check("cli observe --policy <missing>: exit 2 (set-but-unreadable never silently passes)",
      cli("observe", _otdir, "--transcripts", _otdir, "--out", os.path.join(_mkd(), "o"), "--policy", _missing).returncode == 2)
rojp = cli("observe", _otdir, "--transcripts", _otdir, "--json", "--fleet", "t", "--policy", _pv)
try:
    json.loads(rojp.stdout); _opure = True
except Exception:
    _opure = False
check("cli observe --json --policy <violating>: pure JSON stdout, violation on stderr, exit 1",
      rojp.returncode == 1 and _opure, f"rc={rojp.returncode}")

# ── the direct `python -m candor_agents.{scan,observe}` arg-error matrices ────────────────────────
# Every arg error is exit 2 with a clean ONE-LINE diagnostic — never a traceback (a traceback means
# no report and the gate silently not running), never silently ignored/reinterpreted.
def _direct(mod, *a):
    return subprocess.run([sys.executable, "-m", f"candor_agents.{mod}", *a],
                          capture_output=True, text=True, cwd=HERE)

for _mod, _case, _args, _want in [
    ("scan", "a value-taking flag at end of line", ["x", "--out"], "requires a value"),
    ("scan", "a flag-shaped value", ["x", "--policy", "--json"], "requires a value"),
    ("scan", "an unknown flag", ["x", "--frobnicate"], "unknown flag"),
    ("scan", "a second positional", ["x", "y"], "unexpected extra argument"),
    ("scan", "no project dir", ["--json"], "project dir is required"),
    ("observe", "a value-taking flag at end of line", ["x", "--out"], "requires a value"),
    ("observe", "a flag-shaped value", ["x", "--transcripts", "--json"], "requires a value"),
    ("observe", "an unknown flag", ["x", "--frobnicate"], "unknown flag"),
]:
    _dr = _direct(_mod, *_args)
    check(f"{_mod} (direct -m): {_case} → exit 2, one-line error, no traceback",
          _dr.returncode == 2 and _want in _dr.stderr and "Traceback" not in _dr.stderr,
          f"rc={_dr.returncode} err={_dr.stderr[-160:]!r}")
# observe: transcripts not found (no *.jsonl in the target, no ~/.claude/projects slug) → exit 2
_ntd = _mkd()
_rnt = _direct("observe", _ntd)
check("observe (direct -m): no transcripts found → exit 2 with the one-line diagnostic",
      _rnt.returncode == 2 and "no transcripts found" in _rnt.stderr and "Traceback" not in _rnt.stderr,
      f"rc={_rnt.returncode} err={_rnt.stderr[-160:]!r}")
_ntf = os.path.join(_mkd(), "afile"); open(_ntf, "w").write("x")
_rntf = _direct("observe", _ntd, "--transcripts", _ntf)
check("observe (direct -m): --transcripts naming a FILE → exit 2 `not a directory` (never an os.listdir crash)",
      _rntf.returncode == 2 and "not a directory" in _rntf.stderr, f"rc={_rntf.returncode}")
# THE POSITIONAL-SWALLOW CLASS: observe/stats/savings silently OVERWROTE the target on a second
# positional (`observe a b` analyzed b, dropped a — rc 0), while scan/drift exit 2. One contract:
# a second positional is exit 2 everywhere.
_rop2 = _direct("observe", "first", _otdir, "--transcripts", _otdir)
check("observe (direct -m): a SECOND positional exits 2 (was silently replacing the target)",
      _rop2.returncode == 2 and "unexpected extra argument" in _rop2.stderr,
      f"rc={_rop2.returncode} err={_rop2.stderr[-140:]!r}")
_rsp2 = subprocess.run([sys.executable, "-m", "candor_agents.cli", "stats", "a", "b"],
                       capture_output=True, text=True, cwd=HERE)
check("stats: a SECOND positional exits 2 (was silently replacing the target)",
      _rsp2.returncode == 2 and "unexpected extra argument" in _rsp2.stderr, f"rc={_rsp2.returncode}")
_rvp2 = subprocess.run([sys.executable, "-m", "candor_agents.cli", "savings", "a", "b"],
                       capture_output=True, text=True, cwd=HERE)
check("savings: a SECOND positional exits 2 (was silently replacing the target)",
      _rvp2.returncode == 2 and "unexpected extra argument" in _rvp2.stderr, f"rc={_rvp2.returncode}")

print()


# ══ spec 0.8: --gate-json (§3.3) + .candor/config (§3.4) ══════════════════════════════════════════
# --gate-json: the structured verdict {spec, ok, violations:[{rule,fn,effects,detail}]}, from the
# SAME records that set the exit code — so a SARIF consumer can never see a verdict that disagrees
# with the gate. Written whenever the flag is given; unwritable → exit 2, never silent.
_gj = os.path.join(_mkd(), "verdict.json")
rgv = cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pv, "--gate-json", _gj)
_gv = json.load(open(_gj))
check("scan --gate-json <violating>: verdict {spec:%s, ok:false} agrees with exit 1" % _SPEC,
      rgv.returncode == 1 and _gv["spec"] == _SPEC and _gv["ok"] is False, json.dumps(_gv))
check("scan --gate-json: each violation carries {rule, fn, effects, detail} (fn = the unit name)",
      any(v["rule"] == "AS-EFF-006" and v["fn"] == "leaf" and v["effects"] == ["Net"]
          and "detail" in v for v in _gv["violations"]), json.dumps(_gv))
_gj2 = os.path.join(_mkd(), "clean.json")
check("scan --gate-json <clean policy>: ok:true, violations [], exit 0",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pc, "--gate-json", _gj2).returncode == 0
      and json.load(open(_gj2)) == {"spec": _SPEC, "ok": True, "violations": []})
_gj3 = os.path.join(_mkd(), "nogate.json")
check("scan --gate-json with NO gate configured: still writes the clean verdict (ok:true, [])",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--gate-json", _gj3).returncode == 0
      and json.load(open(_gj3))["ok"] is True)
check("scan --gate-json <unwritable path>: exit 2 — the verdict surface must not vanish silently",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pc,
          "--gate-json", os.path.join(_mkd(), "no-such-dir", "v.json")).returncode == 2)
rgs = cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), "--policy", _pv, "--gate-json", "-")
try:
    _gvs = json.loads(rgs.stdout)
except Exception:
    _gvs = None
check("scan --gate-json -: the verdict streams to stdout (violation lines stay on stderr), exit 1",
      rgs.returncode == 1 and _gvs and _gvs["ok"] is False and "AS-EFF-006" in rgs.stderr, rgs.stdout[:120])
check("scan --json --gate-json -: refused (two JSON documents can't share stdout), exit 2",
      cli("scan", _cd, "--json", "--policy", _pc, "--gate-json", "-").returncode == 2)
# observe mirrors the surface
_gjo = os.path.join(_mkd(), "ov.json")
rgo = cli("observe", _otdir, "--transcripts", _otdir, "--out", os.path.join(_mkd(), "o"),
          "--fleet", "t", "--policy", _pv, "--gate-json", _gjo)
_gvo = json.load(open(_gjo))
check("observe --gate-json <violating>: the OBSERVED gate emits the same verdict shape, exit 1",
      rgo.returncode == 1 and _gvo["ok"] is False
      and any(v["rule"] == "AS-EFF-006" and v["effects"] == ["Net"] for v in _gvo["violations"]),
      json.dumps(_gvo))

# .candor/config (spec §3.4): target-anchored discovery (NEVER the CWD), $CANDOR_CONFIG override,
# fail-closed on a configured-but-unusable file, unknown keys warn, family-but-unimplemented keys
# warn loudly, the `policy` key gates with flag/env precedence, relative values resolve against the
# config's own directory. THE MIGRATION CASE this battery exists for: a repo moving its wiring from
# $CANDOR_POLICY to the checked-in config must NOT silently lose the fleet gate.
def _fleet_with_config(config_text, policy_files=None, nest=False):
    """A Net-leaf fleet + a .candor/config at the repo root; returns (repo_root, scan_target)."""
    d = _mkd()
    target = os.path.join(d, "sub", "fleet") if nest else d
    os.makedirs(os.path.join(target, ".claude", "agents"), exist_ok=True)
    open(os.path.join(target, ".claude", "agents", "leaf.md"), "w").write(agent("leaf", "WebFetch"))
    os.makedirs(os.path.join(d, ".candor"), exist_ok=True)
    open(os.path.join(d, ".candor", "config"), "w").write(config_text)
    for rel, text in (policy_files or {}).items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)
    return d, target

_d, _t = _fleet_with_config("policy fleet.policy  # the checked-in gate\n",
                            {"fleet.policy": "deny Net\n"})
rc1 = cli("scan", _t, "--out", os.path.join(_mkd(), "r"))
check("config: a discovered .candor/config `policy` GATES the scan (the env→config migration keeps "
      "the gate; relative value resolves against the config's directory, not the CWD)",
      rc1.returncode == 1 and "AS-EFF-006" in rc1.stderr, f"rc={rc1.returncode} err={rc1.stderr[-200:]!r}")
_d, _t = _fleet_with_config("policy fleet.policy\n", {"fleet.policy": "deny Net\n"}, nest=True)
check("config: discovery walks UP from the scan TARGET to the repo root's .candor/config",
      cli("scan", _t, "--out", os.path.join(_mkd(), "r")).returncode == 1)
check("config: observe honors the config `policy` too (the OBSERVED gate is not lost)",
      cli("observe", _d, "--transcripts", _otdir, "--out", os.path.join(_mkd(), "o"),
          "--fleet", "t").returncode == 1)
# precedence: the flag and the env var each OUTRANK the config floor
_d, _t = _fleet_with_config("policy violating.policy\n",
                            {"violating.policy": "deny Net\n", "clean.policy": "deny Db\n"})
check("config precedence: --policy (clean) outranks a violating config policy → exit 0",
      cli("scan", _t, "--out", os.path.join(_mkd(), "r"),
          "--policy", os.path.join(_d, "clean.policy")).returncode == 0)
check("config precedence: $CANDOR_POLICY (clean) outranks the config floor → exit 0",
      cli("scan", _t, "--out", os.path.join(_mkd(), "r"),
          env={"CANDOR_POLICY": os.path.join(_d, "clean.policy")}).returncode == 0)
check("config: a set-but-EMPTY $CANDOR_POLICY fails loud (exit 2), never a silent gate-skip",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), env={"CANDOR_POLICY": ""}).returncode == 2)
# key posture: unknown → warn; family-but-unimplemented → warn LOUDLY that the gate is not active
_d, _t = _fleet_with_config("polcy typo.policy\nstrict conformance\n")
rck = cli("scan", _t, "--out", os.path.join(_mkd(), "r"))
check("config: an unknown key (a misspelt `polcy`) warns — a typo must never silently drop a gate",
      rck.returncode == 0 and "unknown config key 'polcy'" in rck.stderr, rck.stderr[-240:])
check("config: a family key candor-agents does not implement (`strict`) warns 'NOT active here'",
      "not implemented by candor-agents" in rck.stderr and "NOT active here" in rck.stderr, rck.stderr[-240:])
# fail-closed: configured-but-unusable never degrades to "no config"
check("config: $CANDOR_CONFIG naming a missing path fails (exit 2)",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"),
          env={"CANDOR_CONFIG": os.path.join(_mkd(), "no-such-config")}).returncode == 2)
_ccf = os.path.join(_mkd(), "cfg")
open(_ccf, "w").write("policy " + _pv + "\n")
check("config: $CANDOR_CONFIG overrides discovery entirely (its policy gates → exit 1)",
      cli("scan", _cd, "--out", os.path.join(_mkd(), "r"), env={"CANDOR_CONFIG": _ccf}).returncode == 1)
_d, _t = _fleet_with_config("policy p\n", {"p": "deny Net\n"})
os.chmod(os.path.join(_d, ".candor", "config"), 0)
_rcu = cli("scan", _t, "--out", os.path.join(_mkd(), "r"))
os.chmod(os.path.join(_d, ".candor", "config"), 0o644)
check("config: a DISCOVERED file that exists but cannot be read fails (exit 2) — a silently-dropped "
      "config is a silently-dropped gate",
      _rcu.returncode == 2 or os.geteuid() == 0, f"rc={_rcu.returncode}")
_d, _t = _fleet_with_config("policy\n")  # a bare `policy` line: enabled with the empty value
check("config: a BARE `policy` line fails loud on the empty path (exit 2), never a silent drop",
      cli("scan", _t, "--out", os.path.join(_mkd(), "r")).returncode == 2)

print()


# ══ the κ-coverage ledger (spec §7 item 14 — a conformance MUST) ═══════════════════════════════════
# Per-scan receipt evidence of what the curated classifier does NOT cover, canonical cross-engine
# marker `classifier doesn't cover` (the grep target): uncurated MCP servers + unknown tools (Unknown per
# unit, aggregated here) and UNLISTED command heads (the bare Exec cliff — the head's own effects are
# INVISIBLE, item 14's silent-purity concern), plus the reviewed-pure grants the verdict relies on.
rep, r = build(agents_files={"u.md": agent("u", "mcp__billing__charge"),
                             "x.md": agent("x", "FrobnicateDisk"),
                             "p.md": agent("p", "TodoWrite, WebFetch")},
               commands={"mk.md": "---\nallowed-tools: Bash(make:*), Bash(curl:*)\n---\nBuild.\n"},
               mcp=["billing"])
check("κ ledger: the receipt line carries the canonical cross-engine `classifier doesn't cover` marker",
      "classifier doesn't cover" in r.stderr and "κ" not in r.stderr, r.stderr[-400:])
check("κ ledger: an uncurated MCP server is named with its unit count (agent + session root)",
      "mcp:billing (2 units)" in r.stderr, r.stderr[-400:])
check("κ ledger: an unknown tool is named", "tool:FrobnicateDisk (1 unit)" in r.stderr, r.stderr[-400:])
check("κ ledger: an UNLISTED command head (make — the bare Exec cliff) is named; a listed head (curl) is not",
      "head:make (1 unit)" in r.stderr and "head:curl" not in r.stderr, r.stderr[-400:])
check("κ ledger: the reviewed-pure grants the verdict relies on are disclosed as a CLAIM",
      "reviewed-pure" in r.stderr and "TodoWrite" in r.stderr and "claim, not a measurement" in r.stderr,
      r.stderr[-400:])
# control: a fleet κ covers verb-precisely emits NO ledger line (item 14's exemption — zero gaps)
rep, r = build(agents_files={"a.md": agent("a", "Read, WebFetch")},
               commands={"c.md": "---\nallowed-tools: Bash(curl:*)\n---\nFetch.\n"})
check("κ ledger: a fully-covered fleet (curated tools, listed heads, no pure grants) emits NO κ line",
      "κ" not in r.stderr, r.stderr[-300:])
# a voided candorEffects declaration is a κ gap too (the server is back to unclassifiable)
import subprocess as _sp
_kd = tempfile.mkdtemp()
os.makedirs(os.path.join(_kd, ".claude", "agents"))
open(os.path.join(_kd, ".claude", "agents", "t.md"), "w").write(agent("t", "mcp__typoed__op"))
json.dump({"mcpServers": {"typoed": {"command": "x", "candorEffects": ["net"]}}},
          open(os.path.join(_kd, ".mcp.json"), "w"))
_kr = _sp.run([sys.executable, "-m", "candor_agents.scan", _kd, "--out", os.path.join(_kd, "r"),
               "--fleet", "t"], capture_output=True, text=True)
check("κ ledger: a VOIDED candorEffects declaration aggregates as a κ gap (mcp:typoed)",
      "classifier doesn't cover" in _kr.stderr and "mcp:typoed" in _kr.stderr, _kr.stderr[-300:])

print()


# ══ observed `paths` truncation: disclosed + fail-closed (the position-201 evasion) ════════════════
# The emit bound (200) sorted lexicographically meant an `allow Fs` gate evaluated only the first 200
# paths — a disallowed path sorted past position 200 passed CLEAN with no disclosure. Now: the receipt
# discloses the truncation, and the unit's Fs surface reads INCOMPLETE to the gate (uncertifiable —
# the AS-EFF-008 fail-closed posture on an incomplete literal surface).
_ptd = _mkd()
with open(os.path.join(_ptd, "s.jsonl"), "w") as _pf:
    for _i in range(201):  # /repo/f000 … /repo/f200 — ALL inside the allowlist below
        _pf.write(json.dumps({"message": {"content": [{"type": "tool_use", "id": f"t{_i}",
                  "name": "Read", "input": {"file_path": f"/repo/f{_i:03d}"}}]}}) + "\n")
_pallow = os.path.join(_mkd(), "p"); open(_pallow, "w").write("allow Fs /repo\n")
_pout = os.path.join(_mkd(), "o")
rpt = cli("observe", _ptd, "--transcripts", _ptd, "--out", _pout, "--fleet", "t", "--policy", _pallow)
_prep = json.load(open(f"{_pout}.t.Observed.json"))
_psess = next(f for f in _prep["functions"] if f["fn"] == "session")
check("observe: >200 observed paths are truncated at the emit bound (200 in the report)",
      len(_psess["paths"]) == 200, len(_psess.get("paths", [])))
check("observe: the truncation is DISCLOSED on the receipt (unit + total, INCOMPLETE, fail-closed note)",
      "TRUNCATED at 200" in rpt.stderr and "session (201 paths)" in rpt.stderr
      and "INCOMPLETE" in rpt.stderr, rpt.stderr[-300:])
check("observe: `allow Fs` over a TRUNCATED surface fails closed (exit 1, uncertifiable) even though "
      "every VISIBLE path is allowed",
      rpt.returncode == 1 and "AS-EFF-008" in rpt.stderr and "INCOMPLETE" in rpt.stderr,
      f"rc={rpt.returncode} err={rpt.stderr[-240:]!r}")
# control: the same fleet under the cap certifies clean — fail-closed bites ONLY on truncation
_ptc = _mkd()
with open(os.path.join(_ptc, "s.jsonl"), "w") as _pf:
    for _i in range(3):
        _pf.write(json.dumps({"message": {"content": [{"type": "tool_use", "id": f"t{_i}",
                  "name": "Read", "input": {"file_path": f"/repo/f{_i}"}}]}}) + "\n")
rptc = cli("observe", _ptc, "--transcripts", _ptc, "--out", os.path.join(_mkd(), "o"),
           "--fleet", "t", "--policy", _pallow)
check("observe: the same `allow Fs` over a COMPLETE (untruncated) surface passes clean (control)",
      rptc.returncode == 0 and "TRUNCATED" not in rptc.stderr, f"rc={rptc.returncode}")

print()


# ══ policy.py: the in-process §6.2 gate, unit-tested + verdict PARITY with candor-query ════════════
# The new in-process gate (policy.py) is the property that lets ONE policy file gate code AND fleets
# identically — so it must (1) implement AS-EFF-006/008/009 exactly, and (2) AGREE with the unmodified
# candor-query (candor-rust) on the same report. A divergence here is the silent-disagreement bug the
# "no candor tool changed" design exists to prevent.
from candor_agents import policy as _pol

def _gate(policy_text, functions, callgraph):
    return _pol.evaluate_policy(_pol.parse_policy(policy_text), functions, callgraph)

# --- AS-EFF-006 (deny / pure) over transitive `inferred` ---
# evaluate_policy returns STRUCTURED records {rule, fn, effects, detail} (spec §3.3 ⟨0.8⟩ — the
# --gate-json shape); render() rebuilds the console `[AS-EFF-00x] …` line from the same record.
_f006 = [{"fn": "boss", "inferred": ["Net", "Unknown"], "calls": ["leaf"]},
         {"fn": "leaf", "inferred": ["Net"], "calls": []},
         {"fn": "quiet", "inferred": ["Fs"], "calls": []}]
_v = _gate("deny Net", _f006, {"boss": ["leaf"], "leaf": [], "quiet": []})
check("policy AS-EFF-006: `deny Net` flags every unit that REACHES Net (boss+leaf), not the Fs unit",
      sorted(v["fn"] for v in _v if v["rule"] == "AS-EFF-006") == ["boss", "leaf"]
      and all(v["rule"] == "AS-EFF-006" for v in _v), _v)
check("policy AS-EFF-006: `effects` carries the DENIED INTERSECTION (Net), never the unit's full set",
      all(v["effects"] == ["Net"] for v in _v), _v)
check("policy render(): the console line is `[rule] detail` from the same record --gate-json emits",
      _pol.render(_v[0]) == f"[AS-EFF-006] {_v[0]['detail']}" and "forbidden by policy" in _pol.render(_v[0]), _v)
check("policy AS-EFF-006: a clean policy (`deny Db`) over a Net/Fs fleet yields no violations",
      _gate("deny Db", _f006, {}) == [])
# `pure <scope>` is a deny with NO effects → any DETERMINED effect on the scope is a violation.
# NOT "any inferred effect", which is what this comment said until spec ⟨0.24⟩ and what the code did:
# `pure` fires iff `S ≠ ∅` (§4.0's verb table), and `S` is `inferred` MINUS the `Unknown` marker.
_vp = _gate("pure leaf", _f006, {})
check("policy AS-EFF-006: `pure leaf` (deny with no effects) flags leaf for performing Net",
      len(_vp) == 1 and _vp[0]["fn"] == "leaf" and _vp[0]["rule"] == "AS-EFF-006", _vp)

# ══ SPEC ⟨0.24⟩ — `pure` is UNAFFECTED by `Unknown` (§4.0's verb table, conformance PART 16) ═══════
# `pure <scope>` fires iff `S ≠ ∅`, where `S` is the DETERMINED effects — `inferred` MINUS the
# `Unknown` visibility marker. `D ≠ ∅` alone is AS-EFF-003 DISCLOSURE, not an AS-EFF-006 violation;
# `unverified` is the verb that exists to surface it. Until ⟨0.24⟩ this engine counted `Unknown` as an
# effect, so a fleet whose units were determined-pure but sat behind one uncurated MCP server failed
# `pure` — and no bare-`Unknown` fixture in this file could show it, because every such unit also had
# a real effect. THE SECOND ASSERTION IS THE ONE THAT MATTERS: killing an over-charge is exactly where
# a silent UNDER-report gets introduced, so the fixture proving `pure` stopped firing on `Unknown`
# cannot also prove it still fires on `Net`. Both, or neither is evidence.
_f024 = [{"fn": "blind", "direct": ["Unknown"], "inferred": ["Unknown"],
          "unknownWhy": ["mcp-uncurated:mystery"], "calls": []},
         {"fn": "doer", "direct": ["Net", "Unknown"], "inferred": ["Net", "Unknown"],
          "unknownWhy": ["tool-unknown:Frobnicate"], "calls": []}]
_cg024 = {"blind": [], "doer": []}
check("⟨0.24⟩ policy `pure`: a determined-PURE unit with an undischarged Unknown (S=∅, D≠∅) PASSES — "
      "`Unknown` is the §4 trust marker, not an effect (§4.0 verb table / conformance PART 16)",
      _gate("pure blind", _f024, _cg024) == [], _gate("pure blind", _f024, _cg024))
_vpn = _gate("pure doer", _f024, _cg024)
check("⟨0.24⟩ policy `pure` STILL fires on a DETERMINED effect beside the Unknown, and reports S only "
      "(the control against fixing the over-charge into a silent under-report)",
      len(_vpn) == 1 and _vpn[0]["fn"] == "doer" and _vpn[0]["effects"] == ["Net"], _vpn)

# ══ SPEC §6.2 — the reason-class projection + the `Unknown[class]` filter ══════════════════════════
# A DOMAIN engine (§4 ⟨0.7⟩) emits none of the five code kinds, so EVERY reason candor-agents writes
# reaches `unresolved` through §6.2's conservative catch-all. That makes the filter cheap here — and
# makes its absence a live FAIL-OPEN, which is what it was: `Unknown[…]` parsed as the rule's SCOPE
# token, so `deny Unknown[*]` named no effect, was dropped, and exited 0 while bare `deny Unknown`
# on the same report exited 1. The spec says the two forms are byte-identical.
check("§6.2 projection: every FLEET reason kind classifies `unresolved` via the conservative catch-all",
      {_pol.classify_reason(w) for w in
       ["mcp-uncurated:s", "mcp-decl-invalid:s:net", "tool-unknown:X", "ambient:tools-unrestricted",
        "agent-spawn:x", "hooks-unreadable:settings.json", "hooks-malformed:s.json",
        "hook-type:Weird"]} == {"unresolved"})
check("§6.2 projection: a CODE reason arriving from a chained report classifies as the engine that "
      "wrote it does — incl. `ambiguous:` → dispatch, a §4 kind only since ⟨0.24⟩ but in this table "
      "all along",
      [_pol.classify_reason(w) for w in
       ["reflect:x", "native:x", "dispatch:A.b", "callback:f", "ambiguous:g", "indy:h",
        "task-handoff:t", "missing-config", "dep:abc123", "dep-stale:pkg"]]
      == ["reflect", "native", "dispatch", "indirect", "dispatch", "dispatch",
          "indirect", "setup", "unresolved", "unresolved"])
# THE FABRICATED KIND — a MUST, not a nicety (§4 ⟨0.24⟩). A mutation that rewrites the kind test from
# "is the kind in the SET" to "does the token have the `kind:detail` SHAPE" passes every assertion
# about every REAL kind — they all have the shape — and is caught solely by this one. A control only
# exercised by inputs the implementation already handles is not a control.
check("§4 ⟨0.24⟩ CONTROL: a FABRICATED off-vocabulary kind classifies through the conservative "
      "catch-all — never dropped, never guessed",
      _pol.classify_reason("banana:whatever") == "unresolved"
      and _pol.classify_reason("banana") == "unresolved"          # no `kind:detail` shape at all
      and _pol.classify_reason("dispatchoid:x") != "unresolved")   # prefix rule, deliberately
_f024b = [{"fn": "blind", "direct": ["Unknown"], "inferred": ["Unknown"],
           "unknownWhy": ["banana:whatever"], "calls": []}]
check("§4 ⟨0.24⟩ CONTROL: a fabricated kind still ARMS the gate — `deny Unknown[unresolved]` fires on "
      "it, so an unrecognized reason can never be a silent hole under a narrowed filter",
      [v["fn"] for v in _gate("deny Unknown[unresolved] blind", _f024b, {"blind": []})] == ["blind"])
for _form in ("deny Unknown blind", "deny Unknown[*] blind", "deny Unknown[unresolved] blind",
              "deny Unknown[dynamic] blind", "deny Unknown[banana] blind"):
    _vu = _gate(_form, _f024, _cg024)
    check(f"§6.2 `{_form}` fires on the fleet's Unknown (bare / `*` / `dynamic` / an all-unrecognized "
          f"bracket all fall back to ALL classes — fail-CLOSED)",
          [v["fn"] for v in _vu] == ["blind"] and _vu[0]["effects"] == ["Unknown"], _vu)
    check(f"§6.2 `{_form}`: the AS-EFF-006 verdict carries `reasonClass` — every class present on the "
          f"unit, not just the matched one (§3.3)", _vu[0].get("reasonClass") == ["unresolved"], _vu)
check("§6.2 `deny Unknown[dispatch]` does NOT fire on a fleet report — no domain reason projects to "
      "`dispatch`, and a filter that cannot be honoured must narrow, never widen",
      _gate("deny Unknown[dispatch] blind", _f024, _cg024) == [])
check("§6.2: a scoped `Unknown[…]` leaves a CONCRETE effect in the same rule unaffected",
      [v["effects"] for v in _gate("deny Net Unknown[dispatch] doer", _f024, _cg024)] == [["Net"]])
# The class travels TRANSITIVELY, exactly as the `Unknown` effect does: `unknownWhy` is direct-only by
# design (§4 — a reason names a site in the unit's OWN body), so a unit whose Unknown is INHERITED
# carries no reason and matching the direct field would answer a different question.
_ftr = [{"fn": "boss", "direct": [], "inferred": ["Unknown"], "calls": ["leaf"]},
        {"fn": "leaf", "direct": ["Unknown"], "inferred": ["Unknown"],
         "unknownWhy": ["mcp-uncurated:m"], "calls": []}]
_vtr = _gate("deny Unknown[unresolved] boss", _ftr, {"boss": ["leaf"], "leaf": []})
check("§6.2: the reason class resolves TRANSITIVELY — a unit whose `Unknown` is purely INHERITED is "
      "scoped by its callee's class, not by its own (absent) `unknownWhy`",
      [v["fn"] for v in _vtr] == ["boss"] and _vtr[0]["reasonClass"] == ["unresolved"], _vtr)
# ⟨0.24⟩ CONTRIBUTES, not "defaults to": keyed on a DIRECT `Unknown` the unit did not name, never on
# the class set being empty. Absence is also what an INHERITED Unknown looks like, and charging that
# one `unresolved` is the mirror fabrication. Monotone: adding a reasoned callee must not turn a red
# verdict green (the measured counterexample to the monotone-denial corollary).
_fc = [{"fn": "both", "direct": ["Unknown"], "inferred": ["Unknown"], "calls": ["named"]},
       {"fn": "named", "direct": ["Unknown"], "inferred": ["Unknown"],
        "unknownWhy": ["dispatch:A.b"], "calls": []}]
_vc = _gate("deny Unknown[unresolved] both", _fc, {"both": ["named"], "named": []})
check("§6.2 ⟨0.24⟩ CONTRIBUTES: a unit with a DIRECT Unknown it did not name contributes `unresolved` "
      "AT THE SOURCE, so calling a REASONED callee as well cannot turn a red verdict green",
      [v["fn"] for v in _vc] == ["both"]
      and _vc[0]["reasonClass"] == ["dispatch", "unresolved"], _vc)
check("§6.2 ⟨0.24⟩ CONTRIBUTES is gated on a DIRECT unnamed Unknown, NOT on an empty reason set — an "
      "INHERITED Unknown correctly classified at the callee is not charged `unresolved` too",
      _pol.transitive_reason_classes(
          [{"fn": "up", "direct": [], "inferred": ["Unknown"], "calls": ["dn"]},
           {"fn": "dn", "direct": ["Unknown"], "inferred": ["Unknown"],
            "unknownWhy": ["dispatch:A.b"], "calls": []}],
          {"up": ["dn"], "dn": []}) == {"up": {"dispatch"}, "dn": {"dispatch"}})

# --- AS-EFF-008 (allowlist, FAIL-CLOSED) over the literal surfaces ---
# A unit reaches Net to two hosts; the allowlist clears only one → the other is a violation.
_f008 = [{"fn": "fetcher", "inferred": ["Net"], "hosts": ["api.good.test", "evil.test"], "calls": []}]
_va = _gate("allow Net api.good.test", _f008, {})
# the violation names the BAD host in the `reaches { … }` list; the allowed host appears only in the
# echoed rule (`allow Net api.good.test`), never in the reached-bad set.
_va_bad = re.search(r"reaches \{ ([^}]*) \}", _va[0]["detail"]).group(1) if _va else ""
check("policy AS-EFF-008: a host OUTSIDE the allowlist is flagged (the cleared host is not)",
      len(_va) == 1 and _va[0]["rule"] == "AS-EFF-008" and _va[0]["effects"] == ["Net"]
      and "evil.test" in _va_bad and "api.good.test" not in _va_bad, _va)
check("policy AS-EFF-008: an allowlist covering every reached host clears it (no violation)",
      _gate("allow Net api.good.test evil.test", _f008, {}) == [])
# FAIL-CLOSED: the effect is reached but NO literal is visible (e.g. Db has no surface) → the surface
# CANNOT be certified, so the allowlist must NOT clear it — a clean pass here would hide an invisible
# forbidden endpoint behind the allowlist (the cardinal under-report).
_f008b = [{"fn": "dbuser", "inferred": ["Db"], "calls": []}]   # Db reached, no `tables` surface
_vc = _gate("allow Db public.users", _f008b, {})
check("policy AS-EFF-008 FAIL-CLOSED: an effect reached with NO visible literal is uncertifiable, flagged",
      len(_vc) == 1 and "no visible literal" in _vc[0]["detail"] and _vc[0]["rule"] == "AS-EFF-008", _vc)
# FAIL-CLOSED on an INCOMPLETE surface: literals visible, all inside the allowlist — but the surface
# was truncated (observe's paths emit bound), so the invisible remainder could hold the forbidden
# value. The incomplete map must make the allowlist uncertifiable (a benign visible literal must not
# MASK the dropped remainder — the masking evasion the code engines fail closed on).
_f008c = [{"fn": "walker", "inferred": ["Fs"], "paths": ["/repo/a", "/repo/b"], "calls": []}]
_vi = _pol.evaluate_policy(_pol.parse_policy("allow Fs /repo"), _f008c, {}, incomplete={"walker": {"Fs"}})
check("policy AS-EFF-008 FAIL-CLOSED: an INCOMPLETE (truncated) surface is uncertifiable even when "
      "every visible literal is allowed",
      len(_vi) == 1 and _vi[0]["rule"] == "AS-EFF-008" and "INCOMPLETE" in _vi[0]["detail"], _vi)
check("policy AS-EFF-008: the same allowlist over the same COMPLETE surface passes clean (control)",
      _pol.evaluate_policy(_pol.parse_policy("allow Fs /repo"), _f008c, {}) == [])

# --- AS-EFF-009 (forbid A -> B by callgraph reachability) ---
_f009 = [{"fn": "web", "inferred": ["Net"], "calls": ["svc"]},
         {"fn": "svc", "inferred": ["Fs"], "calls": ["db"]},
         {"fn": "db", "inferred": ["Db"], "calls": []}]
_cg009 = {"web": ["svc"], "svc": ["db"], "db": []}
_vf = _gate("forbid web -> db", _f009, _cg009)
check("policy AS-EFF-009: `forbid web -> db` fires on TRANSITIVE reachability (web→svc→db)",
      len(_vf) == 1 and _vf[0]["rule"] == "AS-EFF-009" and _vf[0]["fn"] == "web"
      and "db" in _vf[0]["detail"] and _vf[0]["effects"] == [], _vf)
check("policy AS-EFF-009: no path from the `from` layer → no violation (the reverse direction is clean)",
      _gate("forbid db -> web", _f009, _cg009) == [])

# --- the effect-specific literal matchers (§6.2) — table-driven, verified against the family ------
# These arms (Exec basename, Db qualified-name + schema.*, Net IPv6) had NEVER executed before these
# pins. Each vector below was checked against SPEC §6.2 (host by name port-ignored; exec by basename;
# db case-insensitive qualified name, `schema.*` boundary-respecting, a bare name never widening to a
# qualified one) AND against the sibling matchers (candor-rust policy.rs, candor-ts policy.mjs,
# candor-java Literals.cmdBase) — all agree. NOTE on _cmd_base's whitespace split: Java/TS split the
# first whitespace token before the basename (a command-LINE literal), Rust does not (its literals
# are bare programs); here both coincide — policy values are ASCII-WS-split tokens (never contain a
# space) and reached cmds are bash_cmds heads (bare names) — so this port follows Java/TS.
for _c, _want in [("/usr/bin/git", "git"), ("git", "git"), ("C:\\tools\\git.exe", "git.exe"),
                  ("git status", "git"), ("  /usr/bin/curl https://x  ", "curl"), ("", "")]:
    check(f"policy _cmd_base({_c!r}) == {_want!r} (§6.2 exec-by-basename, Rust/Java/TS parity)",
          _pol._cmd_base(_c) == _want, repr(_pol._cmd_base(_c)))
for _h, _want in [("[2001:db8::aa]:443", "2001:db8::aa"), ("[2001:db8::aa]", "2001:db8::aa"),
                  ("2001:db8::aa", "2001:db8::aa"), ("::1", "::1"),
                  ("api.stripe.com:443", "api.stripe.com"), ("api.stripe.com", "api.stripe.com")]:
    check(f"policy _host_part({_h!r}) == {_want!r} (§6.2 host-by-name; IPv6 bracket + bare branches)",
          _pol._host_part(_h) == _want, repr(_pol._host_part(_h)))
for _a, _r, _want in [("ledger.*", "ledger.entries", True),
                      ("ledger.*", "ledgerx.entries", False),   # the schema-wildcard BOUNDARY
                      ("ledger.*", "ledger", False),            # the bare schema name is not a table in it
                      ("LEDGER.ENTRIES", "ledger.entries", True),  # case-insensitive
                      ("entries", "ledger.entries", False),     # a bare name never widens to qualified
                      ("ledger.entries", "ledger.entries", True)]:
    check(f"policy _table_covered({_a!r}, {_r!r}) is {_want} (§6.2 db qualified-name + schema.*)",
          _pol._table_covered(_a, _r) is _want)

# gate-level: the Exec and Db arms through evaluate_policy (allow Exec git clears /usr/bin/git, flags
# /usr/bin/curl; allow Db ledger.* clears ledger.entries, flags ledgerx.entries)
_fex = [{"fn": "runner", "inferred": ["Exec"], "cmds": ["/usr/bin/git", "/usr/bin/curl"], "calls": []}]
_vex = _gate("allow Exec git", _fex, {})
check("policy AS-EFF-008 Exec arm: `allow Exec git` clears /usr/bin/git (basename) and flags /usr/bin/curl",
      len(_vex) == 1 and "curl" in _vex[0]["detail"] and "git" not in
      re.search(r"reaches \{ ([^}]*) \}", _vex[0]["detail"]).group(1), _vex)
check("policy AS-EFF-008 Exec arm: an allowlist covering both heads clears the unit",
      _gate("allow Exec git curl", _fex, {}) == [])
_fdb = [{"fn": "dbuser", "inferred": ["Db"], "tables": ["ledger.entries"], "calls": []}]
check("policy AS-EFF-008 Db arm: `allow Db ledger.*` clears ledger.entries",
      _gate("allow Db ledger.*", _fdb, {}) == [])
_fdbx = [{"fn": "dbuser", "inferred": ["Db"], "tables": ["ledgerx.entries"], "calls": []}]
_vdbx = _gate("allow Db ledger.*", _fdbx, {})
check("policy AS-EFF-008 Db arm: `allow Db ledger.*` does NOT cover ledgerx.entries (the wildcard boundary)",
      len(_vdbx) == 1 and _vdbx[0]["rule"] == "AS-EFF-008" and "ledgerx.entries" in _vdbx[0]["detail"], _vdbx)
# Net IPv6: one allowed IPv6 (with port) clears that address only — the naive first-colon split once
# collapsed every 2001:db8::* to `2001`, so one allowed IPv6 accepted the whole block (the Rust
# /code-review find; pin the class here too).
_f6a = [{"fn": "netter", "inferred": ["Net"], "hosts": ["2001:db8::aa"], "calls": []}]
_f6b = [{"fn": "netter", "inferred": ["Net"], "hosts": ["2001:db8::bb"], "calls": []}]
check("policy AS-EFF-008 Net IPv6: `allow Net [2001:db8::aa]:443` clears the bare reached 2001:db8::aa",
      _gate("allow Net [2001:db8::aa]:443", _f6a, {}) == [])
check("policy AS-EFF-008 Net IPv6: a DIFFERENT address in the same block is flagged (no first-colon collapse)",
      len(_gate("allow Net 2001:db8::aa", _f6b, {})) == 1)
# process-level (the layer that owns the exit code): the Exec arm end to end through observe —
# a transcript running /usr/bin/git under `allow Exec git` passes 0; under `allow Exec rsync` exits 1.
_exd = _mkd()
open(os.path.join(_exd, "s.jsonl"), "w").write(
    '{"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", '
    '"input": {"command": "/usr/bin/git status"}}]}}\n')
_pex_ok = os.path.join(_mkd(), "p"); open(_pex_ok, "w").write("allow Exec git\n")
_pex_no = os.path.join(_mkd(), "p"); open(_pex_no, "w").write("allow Exec rsync\n")
check("cli observe --policy `allow Exec git`: a /usr/bin/git run certifies clean (basename), exit 0",
      cli("observe", _exd, "--transcripts", _exd, "--out", os.path.join(_mkd(), "o"),
          "--fleet", "t", "--policy", _pex_ok).returncode == 0)
_rex = cli("observe", _exd, "--transcripts", _exd, "--out", os.path.join(_mkd(), "o"),
           "--fleet", "t", "--policy", _pex_no)
check("cli observe --policy `allow Exec rsync`: the git head is outside the allowlist → AS-EFF-008, exit 1",
      _rex.returncode == 1 and "AS-EFF-008" in _rex.stderr, f"rc={_rex.returncode} err={_rex.stderr[-160:]!r}")
# the default arm (unreachable via parse_policy — kept for Rust/TS parity + embedders): exact match
check("policy _literal_allowed default arm (embedder surface): unknown effect exact-matches, never crashes",
      _pol._literal_allowed("Env", "HOME", ["HOME"]) is True
      and _pol._literal_allowed("Env", "PATH", ["HOME"]) is False)

# --- the §6.2 parse-warning contract (drop-with-warning; mirrors candor-java's PolicyParserTest) ---
import contextlib, io

def _parse_warn(text):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        p = _pol.parse_policy(text)
    return p, buf.getvalue()

_pp, _w = _parse_warn("deny Exec   # this is a comment, not a scope")
check("policy parse: an inline `#` comment is stripped, never taken as the scope",
      _pp["deny"] == [{"effects": ["Exec"], "scope": "", "unknownClasses": [],
                       "raw": "deny Exec"}], _pp)
_pp, _w = _parse_warn("deny notaneffect")
check("policy parse: a deny naming NO known effect is DROPPED with a warning — never reinterpreted "
      "as `pure` (which would forbid everything)",
      _pp["deny"] == [] and "deny names no known effect" in _w, (_pp, _w))
_pp, _w = _parse_warn("pure com.acme.domain")
check("policy parse: `pure <scope>` IS the empty-effect deny",
      _pp["deny"] == [{"effects": [], "scope": "com.acme.domain", "unknownClasses": [],
                       "raw": "pure com.acme.domain"}], _pp)
_pp, _w = _parse_warn("forbid com.acme.web->com.acme.db")
check("policy parse: an UNSPACED `a->b` forbid is malformed → dropped with a warning",
      _pp["forbid"] == [] and "malformed forbid" in _w, (_pp, _w))
_pp, _w = _parse_warn("allow Net in")
check("policy parse: a value-less `allow Net in` is dropped with a warning (`in` not kept as a value)",
      _pp["allow"] == [] and "allow names no values" in _w, (_pp, _w))
_pp, _w = _parse_warn("allow Log com.acme.x")
check("policy parse: an allow on a non-literal-surface effect (Log) is dropped with a warning",
      _pp["allow"] == [] and "allow supports only Net hosts" in _w, (_pp, _w))
_pp, _w = _parse_warn("bogus rule here")
check("policy parse: an unknown rule kind is dropped with a warning",
      _pp == {"deny": [], "allow": [], "forbid": []} and "unknown rule kind" in _w, (_pp, _w))
_pp, _w = _parse_warn("deny Db Net   com.acme.domain")
check("policy parse: multi-effect deny — the first non-effect token is the scope and ENDS the rule",
      _pp["deny"] == [{"effects": ["Db", "Net"], "scope": "com.acme.domain", "unknownClasses": [],
                       "raw": "deny Db Net   com.acme.domain"}], _pp)
_pp, _w = _parse_warn("deny Unknown[dispatch] x")
check("policy parse §6.2: narrowing `Unknown[…]` without `unresolved` emits the advisory UNDER-GATING "
      "lint, and the rule SURVIVES (the message must not claim the rule was ignored)",
      _pp["deny"] == [{"effects": ["Unknown"], "scope": "x", "unknownClasses": ["dispatch"],
                       "raw": "deny Unknown[dispatch] x"}] and "UNDER-gate" in _w, (_pp, _w))
_pp, _w = _parse_warn("deny Unknown[dispatch,banana] x")
check("policy parse §6.2 ⟨0.24⟩: an unrecognized class token is a FATAL policy error — the rule that "
      "would RUN is narrower than the one WRITTEN, so the gate refuses rather than silently rewriting",
      _pp["deny"][0]["unknownClasses"] == ["dispatch"]
      and any(e["fatal"] and "banana" in e["why"] for e in _pol.LAST_POLICY_ERRORS)
      and "policy error" in _w, (_pp, _w, _pol.LAST_POLICY_ERRORS))
_pp, _w = _parse_warn("deny Unknown[banana] x")
check("policy parse §6.2: a bracket whose classes are ALL unrecognized falls back to the BARE form "
      "(all classes), never to a filter that matches nothing — fail-closed",
      _pp["deny"][0]["unknownClasses"] == [] and _pp["deny"][0]["effects"] == ["Unknown"], (_pp, _w))
_pp, _w = _parse_warn("deny Net Unknown x")
check("policy parse §6.2: bare `Unknown` means ALL classes ⇒ an EMPTY filter, so a pre-0.19 policy "
      "parses byte-identically", _pp["deny"][0]["unknownClasses"] == [], _pp)
_pp, _w = _parse_warn("deny Net[unknown-host] api")
check("policy parse §6.2: a destination-class filter on a CONCRETE effect keeps the EFFECT and drops "
      "the FILTER, loudly — this engine emits no `netClass`, so honouring it would match an absent "
      "field and pass, and dropping the whole rule exited 0 on a Net-reaching fleet. Both fail OPEN; "
      "widening is the family's policy-side rule and is safe under monotone denial",
      _pp["deny"] == [{"effects": ["Net"], "scope": "api", "unknownClasses": [],
                       "raw": "deny Net[unknown-host] api"}] and "enforced UNSCOPED" in _w, (_pp, _w))
_pp, _w = _parse_warn("deny Llm api")
check("policy parse §1 ⟨0.24⟩: `Llm` is a §1 effect (since ⟨0.13⟩) and parses as one — it was missing "
      "from this engine's vocabulary, so the whole rule was DROPPED and the gate exited 0 (fail-open)",
      _pp["deny"] == [{"effects": ["Llm"], "scope": "api", "unknownClasses": [],
                       "raw": "deny Llm api"}] and "no known effect" not in _w, (_pp, _w))
check("policy §1 ⟨0.24⟩: the effect vocabulary is §1's table minus `Unknown` — all ELEVEN, not `the "
      "ten` (the phrasing that went stale when `Llm` landed)",
      sorted(_pol.EFFECTS) == sorted(["Net", "Fs", "Db", "Exec", "Env", "Clock", "Ipc", "Log",
                                      "Rand", "Clipboard", "Llm"]) and "Unknown" not in _pol.EFFECTS)
from candor_agents import guard as _guard
check("policy §1 ⟨0.24⟩: guard (the runtime dual) reads the SAME vocabulary — three hand-typed copies "
      "of §1's table is how `Llm` came to be missing from all of them at once",
      _guard.VOCAB == set(_pol.EFFECTS))

# --- VERDICT PARITY with the UNMODIFIED candor-query (the property that makes ONE gate code+fleets) ---
# Parser parity: policy.parse_policy must agree with `candor-query parsepolicy` (the canonical shared
# parser) on each rule kind — same drops, same scopes, same values. Then VERDICT parity: the violating
# FUNCTION SET this gate produces over a real report must equal the set candor-query's gate (via
# `whatif`, which shares the Rust matcher) reports for the same effect+policy.
_fq2 = subprocess.run(["bash", os.path.join(HERE, "find-query.sh")], capture_output=True, text=True)
_Q = _fq2.stdout.strip() if _fq2.returncode == 0 else ""
if _Q and os.path.exists(_Q):
    def _canon(text):
        _t = os.path.join(_mkd(), "pol"); open(_t, "w").write(text + "\n")
        out = subprocess.run([_Q, "parsepolicy", _t], capture_output=True, text=True).stdout
        return json.loads(out)
    def _norm(p):  # compare structure only (mine carries an extra `raw`; canon does not)
        return {"deny": sorted((tuple(d["effects"]), d["scope"]) for d in p.get("deny", [])),
                "allow": sorted((a["effect"], a["scope"], tuple(a["values"])) for a in p.get("allow", [])),
                "forbid": sorted((f["from"], f["to"]) for f in p.get("forbid", []))}
    _parser_ok = True
    for _rule in ("deny Net", "pure helper", "deny Net api", "allow Net in api example.com",
                  "allow Net example.com api.test", "forbid web -> db", "deny Exec Db tool",
                  "allow Db in svc users orders", "allow", "deny", "bogus rule", "deny Net Db"):
        if _norm(_pol.parse_policy(_rule)) != _norm(_canon(_rule)):
            _parser_ok = False
            print(f"  parser DIFF on {_rule!r}: mine={_pol.parse_policy(_rule)} canon={_canon(_rule)}")
    check("policy parser parity: policy.parse_policy agrees with `candor-query parsepolicy` (canonical, "
          "all rule kinds incl. malformed drops)", _parser_ok)

    # Verdict parity: scan a real fleet, then for each effect compare this gate's AS-EFF-006 fn-set with
    # candor-query `whatif <prefix> <fn> <Effect> <deny-policy>` (its gate fires the same matcher set).
    _pd = _mkd(); os.makedirs(os.path.join(_pd, ".claude", "agents"))
    open(os.path.join(_pd, ".claude", "agents", "boss.md"), "w").write(agent("boss", "Agent", body="Use `leaf`."))
    open(os.path.join(_pd, ".claude", "agents", "leaf.md"), "w").write(agent("leaf", "WebFetch"))
    _pre = os.path.join(_pd, "r")
    subprocess.run([sys.executable, "-m", "candor_agents.scan", _pd, "--out", _pre, "--fleet", "t"],
                   capture_output=True, text=True, cwd=HERE)
    _prep = json.load(open(f"{_pre}.t.Fleet.json"))
    _pcg = json.load(open(f"{_pre}.t.Fleet.callgraph.json"))
    _polf = os.path.join(_pd, "denynet"); open(_polf, "w").write("deny Net\n")
    # MINE: the AS-EFF-006 violating function set for `deny Net`.
    _mine_fns = sorted(v["fn"] for v in _gate("deny Net", _prep["functions"], _pcg)
                       if v["rule"] == "AS-EFF-006")
    # candor-query: `whatif <prefix> leaf Net <deny Net>` lists every fn the (already-present) Net would
    # violate the policy on — the same Rust matcher candor's gate uses. Parse its `[AS-EFF-006] \`fn\`` lines.
    _wi = subprocess.run([_Q, "whatif", f"{_pre}.t", "leaf", "Net", _polf], capture_output=True, text=True)
    _q_fns = sorted(set(re.findall(r"\[AS-EFF-006\]\s+`([^`]+)`", _wi.stdout)))
    check("policy VERDICT parity: this in-process gate flags the SAME function set as candor-query's "
          "whatif for `deny Net` (one gate, code & fleets identically)",
          _mine_fns == _q_fns and _mine_fns == ["boss", "leaf", "session"],
          f"mine={_mine_fns} candor-query={_q_fns}")
else:
    print("  SKIP policy parity (candor-query not located/built)")

print()


# ══ adversarial input on EVERY parse path (the observe-crash lesson) ═══════════════════════════════
# A malformed/oversized/binary file on ANY parse path must DEGRADE-and-DISCLOSE, never crash: a crash
# aborts the run, no report is written, and the gate then silently does not run (the bug that shipped).
# settings.json + scheduled_tasks.json are already covered above; this closes the remaining paths:
# the agent `.md` readers, `.mcp.json` (scan AND guard), and asserts the disclosed bad-COUNT is exact.

# (a) a non-UTF-8 / unreadable agent .md is DISCLOSED with a count, the rest of the fleet still scans
_amd = _mkd(); os.makedirs(os.path.join(_amd, ".claude", "agents"))
open(os.path.join(_amd, ".claude", "agents", "good.md"), "w").write(agent("good", "Read"))
open(os.path.join(_amd, ".claude", "agents", "bad.md"), "wb").write(b"\xff\xfe\x00\x80 ---\nname: x\n--- not utf8")
_ar = subprocess.run([sys.executable, "-m", "candor_agents.scan", _amd, "--out", os.path.join(_amd, "r"),
                      "--fleet", "t"], capture_output=True, text=True, cwd=HERE)
_arep = json.load(open(os.path.join(_amd, "r.t.Fleet.json"))) if _ar.returncode == 0 else None
check("scan: a non-UTF-8 agent .md is disclosed by COUNT (not analyzed), never crashes the scan",
      _ar.returncode == 0 and "1 .md file(s) could not be read" in _ar.stderr and "bad.md" in _ar.stderr,
      (_ar.stderr or "")[-200:])
check("scan: the readable agent beside the poison .md still produces its unit (no silent total loss)",
      _arep is not None and entry(_arep, "good") is not None)

# (b) an oversized agent .md (lots of garbage frontmatter-ish lines) must parse without crashing or
# hanging — the frontmatter regex/line walk handles a big malformed body, disclosing nothing it shouldn't.
_big = _mkd(); os.makedirs(os.path.join(_big, ".claude", "agents"))
open(os.path.join(_big, ".claude", "agents", "huge.md"), "w").write(
    "---\nname: huge\ntools: Read\n" + "junk-key: " + ("x," * 50000) + "\n---\n" + ("body line\n" * 20000))
_bigr = subprocess.run([sys.executable, "-m", "candor_agents.scan", _big, "--out", os.path.join(_big, "r"),
                        "--fleet", "t"], capture_output=True, text=True, cwd=HERE, timeout=60)
check("scan: an oversized/garbage-laden agent .md parses without crashing or hanging",
      _bigr.returncode == 0 and entry(json.load(open(os.path.join(_big, "r.t.Fleet.json"))), "huge") is not None,
      (_bigr.stderr or "")[-160:])

# (c) malformed .mcp.json (scan): both invalid-JSON and a valid-but-WRONG-shape file must DISCLOSE
# (servers unknown), never crash — a crash kills the report and the gate. Mirrors the settings reader.
for _label, _content in (("invalid-json", "{ this is not json"), ("wrong-shape-list", "[1, 2, 3]"),
                         ("wrong-shape-scalar", "42")):
    _md = _mkd(); os.makedirs(os.path.join(_md, ".claude", "agents"))
    open(os.path.join(_md, ".claude", "agents", "a.md"), "w").write(agent("a", "WebFetch"))
    open(os.path.join(_md, ".mcp.json"), "w").write(_content)
    _mr = subprocess.run([sys.executable, "-m", "candor_agents.scan", _md, "--out", os.path.join(_md, "r"),
                          "--fleet", "t"], capture_output=True, text=True, cwd=HERE)
    _mrep = json.load(open(os.path.join(_md, "r.t.Fleet.json"))) if _mr.returncode == 0 else None
    check(f"scan: malformed .mcp.json ({_label}) is DISCLOSED (servers unknown), never crashes",
          _mr.returncode == 0 and "unreadable .mcp.json" in _mr.stderr and _mrep is not None
          and entry(_mrep, "a") is not None, (_mr.stderr or "")[-160:])

# (d) malformed .mcp.json (guard): guard must DISCLOSE the omission, not silently UNDER-PROTECT (emit a
# permissions.deny that quietly leaves a server un-denied). It warns and proceeds.
_gmd = _mkd(); open(os.path.join(_gmd, ".mcp.json"), "w").write("{ not json")
_gg = guard.compile_guard("deny Net", project_dir=_gmd)
check("guard: malformed .mcp.json is DISCLOSED (mcp server denies omitted — verify manually), not a "
      "silent under-protect", any("unreadable" in w and "omitted" in w for w in _gg["warnings"]), _gg["warnings"])

# (e) the observe-transcript lane (the path that shipped the crash): assert the disclosed bad-line count
# is EXACT — counted-bad lines all disclosed (no silent drop), wrong-shape-but-parseable lines NOT
# over-counted. (The fuzzer asserts this over random mixes; this is a pinned, readable case.)
_otl = _mkd()
_counted = ["5", '"x"', "true", "null", "[1, 2]", "not json {", "{unterminated"]              # 7 → all counted bad
_uncounted = ['{"message": "a string"}', '{"message": 5}', '{"message": {"content": "nope"}}',  # parse OK, skipped
              '{"other": 1}']
_valid = ['{"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", '
          '"input": {"command": "curl https://x.test"}}]}}']
open(os.path.join(_otl, "s.jsonl"), "w").write("\n".join(_counted + _uncounted + _valid) + "\n")
_olr = subprocess.run([sys.executable, "-m", "candor_agents.observe", _otl, "--transcripts", _otl,
                       "--out", os.path.join(_otl, "o"), "--fleet", "t"], capture_output=True, text=True, cwd=HERE)
_om = re.search(r"skipped (\d+) unparseable line", _olr.stderr)
check("observe: the disclosed bad-line count is EXACT — every counted-bad line disclosed, no wrong-shape "
      "over-count, no silent drop",
      _olr.returncode == 0 and _om is not None and int(_om.group(1)) == len(_counted),
      f"rc={_olr.returncode} stderr={_olr.stderr[-160:]!r}")
check("observe: a report IS written despite the malformed lines (the OBSERVED gate still runs)",
      os.path.exists(os.path.join(_otl, "o.t.Observed.json")))
# the valid tool_use was still observed past the bad lines (the reader recovered, didn't bail at line 1)
_oob = json.load(open(os.path.join(_otl, "o.t.Observed.json")))
check("observe: the one VALID tool_use is observed past the malformed lines (Exec from the curl)",
      any("Exec" in f["inferred"] for f in _oob["functions"]), json.dumps([f["fn"] for f in _oob["functions"]]))

print()


# ══ unreadable-config disclosure (chmod 000) + the observed curated-MCP split + small surfaces ═════
# A config file that EXISTS but cannot be read must be DISCLOSED (Unknown in the report where a unit
# carries it, a loud stderr warning otherwise) and the scan must complete — never silently dropped
# (a dropped settings.json is dropped hooks/permissions; a dropped scheduled_tasks.json is a dropped
# autonomous-trigger surface).
if os.geteuid() != 0:   # root reads through chmod 000; the config battery above skips the same way
    _ud = _mkd(); os.makedirs(os.path.join(_ud, ".claude", "agents"))
    open(os.path.join(_ud, ".claude", "agents", "a.md"), "w").write(agent("a", "WebFetch"))
    open(os.path.join(_ud, ".claude", "settings.json"), "w").write('{"hooks": {}}')
    os.chmod(os.path.join(_ud, ".claude", "settings.json"), 0)
    open(os.path.join(_ud, ".claude", "scheduled_tasks.json"), "w").write("{}")
    os.chmod(os.path.join(_ud, ".claude", "scheduled_tasks.json"), 0)
    _ur = subprocess.run([sys.executable, "-m", "candor_agents.scan", _ud, "--out",
                          os.path.join(_ud, "r"), "--fleet", "t"], capture_output=True, text=True, cwd=HERE)
    os.chmod(os.path.join(_ud, ".claude", "settings.json"), 0o644)
    os.chmod(os.path.join(_ud, ".claude", "scheduled_tasks.json"), 0o644)
    check("scan: an unreadable settings.json warns AND the hooks unit carries Unknown + unknownWhy "
          "in the REPORT (machine-visible, not stderr-only)",
          _ur.returncode == 0 and "unreadable .claude/settings.json" in _ur.stderr
          and (lambda _e: _e is not None and "Unknown" in _e["inferred"]
               and "hooks-unreadable:settings.json" in _e.get("unknownWhy", []))(
                   entry(json.load(open(os.path.join(_ud, "r.t.Fleet.json"))), "hooks")),
          _ur.stderr[-240:])
    check("scan: an unreadable scheduled_tasks.json is DISCLOSED on stderr (scheduled tasks UNKNOWN) "
          "and the scan still completes",
          _ur.returncode == 0 and "unreadable .claude/scheduled_tasks.json" in _ur.stderr
          and "scheduled tasks UNKNOWN" in _ur.stderr, _ur.stderr[-240:])
else:
    print("  SKIP unreadable-config disclosure (running as root — chmod 000 is readable)")

# observe: a CURATED MCP server's tool classifies by MCP_TABLE (github → Net) with NO Unknown/why —
# the observed-side twin of the scan-side curated/uncurated split (an uncurated one reads Unknown).
_omc = _mkd()
open(os.path.join(_omc, "s.jsonl"), "w").write("\n".join([
    '{"message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__github__create_issue", "input": {}}]}}',
    '{"message": {"content": [{"type": "tool_use", "id": "t2", "name": "mcp__mystery__op", "input": {}}]}}',
]) + "\n")
_omr = subprocess.run([sys.executable, "-m", "candor_agents.observe", _omc, "--transcripts", _omc,
                       "--out", os.path.join(_omc, "o"), "--fleet", "t"], capture_output=True, text=True, cwd=HERE)
_oms = entry(json.load(open(os.path.join(_omc, "o.t.Observed.json"))), "session")
check("observe: a curated MCP server (github) classifies by MCP_TABLE → Net; the uncurated one beside "
      "it reads Unknown with mcp-uncurated:<server> (never silence)",
      _omr.returncode == 0 and "Net" in _oms["inferred"] and "Unknown" in _oms["inferred"]
      and _oms.get("unknownWhy") == ["mcp-uncurated:mystery"], json.dumps(_oms))

# bare `candor-agents <dir>` = scan, the static default (cli.py's fall-through dispatch)
_bd2 = _mkd(); os.makedirs(os.path.join(_bd2, ".claude", "agents"))
open(os.path.join(_bd2, ".claude", "agents", "leaf.md"), "w").write(agent("leaf", "WebFetch"))
_benv = dict(os.environ); _benv["PYTHONPATH"] = HERE + os.pathsep + _benv.get("PYTHONPATH", "")
_rbd = subprocess.run([sys.executable, "-m", "candor_agents.cli", _bd2],
                      capture_output=True, text=True, cwd=_bd2, env=_benv)
check("cli: bare `candor-agents <dir>` dispatches to scan (the static default) and writes the report",
      _rbd.returncode == 0 and any(f.endswith(".Fleet.json") for f in os.listdir(_bd2)),
      f"rc={_rbd.returncode} ls={os.listdir(_bd2)}")

# __init__: VERSION/SPEC are exposed lazily (PEP 562) for embedders; unknown attrs still AttributeError
import candor_agents as _ca
check("__init__: lazy VERSION/SPEC match the scan module's (the embedder surface)",
      _ca.VERSION == _VER and _ca.SPEC == _SPEC, f"{_ca.VERSION} {_ca.SPEC}")
try:
    _ca.NOPE
    _lazy_ok = False
except AttributeError:
    _lazy_ok = True
check("__init__: an unknown attribute raises AttributeError (the lazy hook doesn't swallow typos)", _lazy_ok)

# ── coverage remnants: the contract arms a re-measure still flagged (each verified against the
# Rust/TS twins where one exists; the rest are behavioral one-liners, not implementation pins) ─────
# scope_matches: a scope LONGER than the name never matches (the len guard)
check("policy scope_matches: a scope with more segments than the name never matches",
      _pol.scope_matches("a.b", "a.b.c.d") is False)
# _path_covered: a reached path that climbs out via `..` is never covered; abs/rel never conflate
# (both arms mirror rust fs_path_covered / ts pathCovered)
check("policy _path_covered: a `..` climb-out is never covered; absolute never covers relative",
      _pol._path_covered("/etc/app", "/etc/../x") is False
      and _pol._path_covered("/a", "a/b") is False)
# an allow with a scope skips out-of-scope units; an allow whose effect the unit doesn't reach skips
_fsc = [{"fn": "inscope", "inferred": ["Net"], "hosts": ["evil.test"], "calls": []},
        {"fn": "outscope", "inferred": ["Net"], "hosts": ["evil.test"], "calls": []},
        {"fn": "noeffect", "inferred": ["Fs"], "paths": ["/x"], "calls": []}]
_vsc = _gate("allow Net in inscope good.test", _fsc, {})
check("policy AS-EFF-008: a scoped allow gates ONLY its scope; a unit not reaching the effect is skipped",
      [v["fn"] for v in _vsc] == ["inscope"], _vsc)
check("policy AS-EFF-008: an UNSCOPED allow skips a unit that never reaches the allow's effect",
      _gate("allow Net x.test", [{"fn": "fsonly", "inferred": ["Fs"], "paths": ["/x"], "calls": []}], {}) == [])
# AS-EFF-009 BFS: a diamond callgraph (two paths to the same node) fires once, no revisit loop
_cgd = {"web": ["b", "a"], "a": ["b"], "b": ["db"], "db": []}
_vfd = _gate("forbid web -> db", [{"fn": f, "inferred": [], "calls": []} for f in _cgd], _cgd)
check("policy AS-EFF-009: a diamond graph (two paths converging on b before db) fires ONCE on web "
      "(the seen-set skips the revisit, no duplicate violation)",
      len([v for v in _vfd if v["fn"] == "web"]) == 1, _vfd)
# savings: _h's thousands branch + non-.jsonl files in the transcript dir are skipped
from candor_agents.savings import _h as _hfmt
check("savings _h: thousands format (~384K) and small-int passthrough (~999)",
      _hfmt(384_000) == "~384K" and _hfmt(999) == "~999")
_kd2 = _mkd()
open(os.path.join(_kd2, "README.txt"), "w").write("candor-query callers x")  # not a .jsonl → not counted
open(os.path.join(_kd2, "s.jsonl"), "w").write(_ev("1", "Bash", {"command": "candor-query callers r F 1"}) + "\n")
check("savings: a non-.jsonl file in the transcript dir is never counted (only *.jsonl transcripts)",
      json.loads(_sv("--transcript", _kd2, "--json").stdout)["measured"]["queries"] == 1)
# stats: --log points anywhere (no project dir needed); --json missing-log shape; blank lines skipped
_lg = os.path.join(_mkd(), "anywhere.jsonl")
open(_lg, "w").write('{"verdict":"clean","violations":[]}\n\n{"verdict":"blocked","violations":[]}\n')
check("stats --log <path>: reads the named log (no project dir needed) and skips blank lines",
      json.loads(_stats("--log", _lg, "--json").stdout)["turns"] == 2)
_smj = _stats("/tmp/candor-no-such-dir-xyz", "--json")
check("stats --json: a missing log is the machine shape {turns:0, exists:false}, exit 0",
      _smj.returncode == 0 and json.loads(_smj.stdout) == {"turns": 0, "exists": False,
      "log": "/tmp/candor-no-such-dir-xyz/.candor/activity.jsonl"}, _smj.stdout)
# guard: non-deny policy lines (comments, allow/forbid) are skipped by parse_denies, not misread
check("guard: non-deny lines (comments/allow/forbid) are skipped — only deny rules compile",
      guard.compile_guard("# note\nallow Net x\nforbid a -> b\ndeny Net\n")["deny"] == ["WebFetch", "WebSearch"])
# drift: an internal observe failure PROPAGATES (no transcripts → exit 2, never a bogus comparison);
# an extra positional exits 2 (the same swallow class, drift's own arm)
_dfd = _mkd(); os.makedirs(os.path.join(_dfd, ".claude", "agents"))
open(os.path.join(_dfd, ".claude", "agents", "w.md"), "w").write(agent("w", "WebFetch"))
check("drift: the internal observe failing (no transcripts) propagates its exit 2 — never a bogus verdict",
      cli("drift", _dfd).returncode == 2)
check("drift: an unexpected extra argument exits 2", cli("drift", "a", "b").returncode == 2)
# drift's per-unit verdict lines: declarations-match, and declared-Unknown observed (the soft note)
_dtd = _mkd(); os.makedirs(os.path.join(_dtd, "subagents"))
open(os.path.join(_dtd, "s.jsonl"), "w").write(_ev("t1", "Read", {"file_path": "/x"}) + "\n")
open(os.path.join(_dtd, "subagents", "w.jsonl"), "w").write(_ev("t2", "WebFetch", {"url": "https://x.test"}) + "\n")
json.dump({"agentType": "w"}, open(os.path.join(_dtd, "subagents", "w.meta.json"), "w"))
open(os.path.join(_dtd, "subagents", "m.jsonl"), "w").write(_ev("t3", "Frobnicate", {}) + "\n")
json.dump({"agentType": "m"}, open(os.path.join(_dtd, "subagents", "m.meta.json"), "w"))
_dfd2 = _mkd(); os.makedirs(os.path.join(_dfd2, ".claude", "agents"))
open(os.path.join(_dfd2, ".claude", "agents", "w.md"), "w").write(agent("w", "WebFetch"))
open(os.path.join(_dfd2, ".claude", "agents", "m.md"), "w").write(agent("m", "Frobnicate"))
_rdt = cli("drift", _dfd2, "--transcripts", _dtd)
check("drift: a unit whose observation equals its declaration prints `declarations match observation`",
      _rdt.returncode == 0 and re.search(r"^  w: declarations match observation", _rdt.stdout, re.M),
      _rdt.stdout)
check("drift: an observed Unknown the agent DECLARED is the soft `declared; curate it` note, not an anomaly",
      "m: observed Unknown via tool-unknown:Frobnicate — declared" in _rdt.stdout
      and cli("drift", _dfd2, "--transcripts", _dtd, "--strict").returncode == 0, _rdt.stdout)

print()

# ---- digest: the OWNER-facing protection report (integrations/DIGEST-SPEC.md). The visibility surface
#      for the silent gate — over the SAME log stats reads. Owns the report's content contract. ----
def _digest(*a):
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", "digest", *a], capture_output=True, text=True)

# a fixture that exercises the caught-vs-allowed split: a BLOCKED introduction, an ALLOWED (clean)
# introduction, and a pure clean turn.
_dg = _mkd(); os.makedirs(os.path.join(_dg, ".candor"), exist_ok=True)
with open(os.path.join(_dg, ".candor", "activity.jsonl"), "w") as _f:
    _f.write("\n".join([
        '{"ts":"2026-06-05T14:00:00Z","sessionId":"s1","engine":"java","edited":["src/Secret.java"],"gained":["Db"],"blastRadius":41,"verdict":"blocked","violations":["AS-EFF-006"],"unknowns":2,"effects":["Db"],"reviewMs":150}',
        '{"ts":"2026-06-11T09:00:00Z","sessionId":"s2","engine":"java","edited":["src/Ok.java"],"gained":["Net"],"blastRadius":3,"verdict":"clean","violations":[],"unknowns":0,"effects":["Net"],"reviewMs":120}',
        '{"ts":"2026-06-20T11:00:00Z","sessionId":"s2","engine":"java","edited":["src/Pure.java"],"gained":[],"blastRadius":0,"verdict":"clean","violations":[],"unknowns":0,"effects":["Log"],"reviewMs":95}',
    ]) + "\n")
_dgout = _digest(_dg, "--out", "-").stdout
check("digest: leads with the catch — 'Held the line' names the AS-EFF-006 violation caught before merge",
      "Held the line" in _dgout and "AS-EFF-006 — performed an effect its policy forbids" in _dgout, _dgout)
check("digest: caught (blocked) and allowed (clean) introductions are SEPARATE — the Db block is not repeated as 'allowed', the Net clean IS",
      "New capability allowed" in _dgout and "(Net)" in _dgout and "(Db)" not in _dgout.split("New capability allowed")[1] if "New capability allowed" in _dgout else False, _dgout)
check("digest: ALWAYS carries the coverage/honesty line (the disclosure ethos on itself)",
      "**Coverage**" in _dgout and "could not fully resolve" in _dgout, _dgout)
check("digest: closes by reframing silence as coverage ('Quiet is good')",
      "Quiet is good" in _dgout and "silence is the gate working" in _dgout, _dgout)
check("digest: AGGREGATE only — no file path from the log ever appears in the report (privacy)",
      "Secret.java" not in _dgout and "Ok.java" not in _dgout and "src/" not in _dgout, _dgout)

# resolved-everything → the good-news coverage line, never 'up to 0'
_dgq = _mkd(); os.makedirs(os.path.join(_dgq, ".candor"), exist_ok=True)
open(os.path.join(_dgq, ".candor", "activity.jsonl"), "w").write(
    '{"ts":"2026-07-01T10:00:00Z","sessionId":"q","engine":"java","edited":["a.java"],"gained":[],"blastRadius":0,"verdict":"clean","violations":[],"unknowns":0,"effects":["Log"],"reviewMs":80}\n')
_dgqout = _digest(_dgq, "--out", "-").stdout
check("digest: a quiet period reports honestly — no catches, and coverage reads as good news not 'up to 0'",
      "no policy violation reached a merge" in _dgqout and "every change resolved to the leaf" in _dgqout
      and "up to 0" not in _dgqout, _dgqout)

# default writes a committable CANDOR-REPORT.md (aggregate → safe to commit, unlike the gitignored log)
_dgw = _digest(_dg)
check("digest: default writes CANDOR-REPORT.md and exits 0",
      _dgw.returncode == 0 and os.path.exists(os.path.join(_dg, "CANDOR-REPORT.md")), _dgw.stderr)

# no log yet → an honest note, exit 0, no file written (never an empty/misleading report)
_dgn = _mkd()
_dgnr = _digest(_dgn)
check("digest: no activity logged yet → honest note, exit 0, no CANDOR-REPORT.md written",
      _dgnr.returncode == 0 and "no activity logged yet" in _dgnr.stderr
      and not os.path.exists(os.path.join(_dgn, "CANDOR-REPORT.md")), _dgnr.stderr)

# arg matrix (TESTING.md §2 fail-closed on the CLI surface)
check("digest: an unknown flag exits 2", _digest(_dg, "--bogus").returncode == 2)
check("digest: a flag missing its value exits 2", _digest(_dg, "--since").returncode == 2)
check("digest: an unexpected second positional exits 2", _digest(_dg, "extra").returncode == 2)

# REGRESSION (found dogfooding candor-java 2026-07-10): a change BLOCKED for introducing a new effect
# (rc=1, no AS-EFF code — the edit-loop's non-policy block) was COUNTED in "Held the line" but never
# itemized, so the header ("2 caught") didn't reconcile with the bullets (one AS-EFF line). Every caught
# change must be explained; a code-less block reads as "introduced a new effect not in the baseline".
_dgb = _mkd(); os.makedirs(os.path.join(_dgb, ".candor"), exist_ok=True)
open(os.path.join(_dgb, ".candor", "activity.jsonl"), "w").write("\n".join([
    '{"ts":"2026-07-01T10:00:00Z","sessionId":null,"engine":"java","edited":null,"gained":["Env"],"blastRadius":8,"verdict":"blocked","violations":[],"unknowns":17,"effects":["Env","Fs"],"reviewMs":0}',
    '{"ts":"2026-07-01T11:00:00Z","sessionId":null,"engine":"jar","edited":null,"gained":[],"blastRadius":0,"verdict":"blocked","violations":["AS-EFF-006"],"unknowns":17,"effects":["Fs"],"reviewMs":null}',
]) + "\n")
_dgbout = _digest(_dgb, "--out", "-").stdout
check("digest: a code-less block (new-effect introduction) is ITEMIZED, naming the effect — not swallowed",
      "introduced a new effect (Env) not in the baseline — blocked for review" in _dgbout, _dgbout)
check("digest: 'Held the line' header reconciles — 2 caught, and 2 bullets (the AS-EFF line + the code-less one)",
      "**Held the line** — 2 changes caught before merge:" in _dgbout
      and _dgbout.count("\n  - ") == 2 and "AS-EFF-006" in _dgbout, _dgbout)
check("digest: the code-less block is NOT double-counted as 'allowed through' (it was blocked, not clean)",
      "New capability allowed" not in _dgbout, _dgbout)
# the time line reports only TIMED checks (a jar/CI record has null reviewMs) — never implies timings not taken
check("digest: 'candor's own time' counts only timed checks (1 here — the null-reviewMs jar record is excluded)",
      "across 1 timed check." in _dgbout, _dgbout)

# ---- log-gate: feed the digest from a PURE-JAR --gate-json CI run (adopt/candor.yml). Same record
#      shape as the stop-hook / review-script path, PATH-FREE (a CI gate has no transcript). ----
def _loggate(*a):
    return subprocess.run([sys.executable, "-m", "candor_agents.cli", "log-gate", *a], capture_output=True, text=True)
def _wjson(d, name, obj):
    p = os.path.join(d, name); open(p, "w").write(json.dumps(obj)); return p

_lg = _mkd()
# a BLOCKED gate: a deny-Net violation (AS-EFF-006) + a baseline-drift (AS-EFF-005) carrying the gained effect
_gate_blocked = {"spec": "0.25", "ok": False, "violations": [
    {"rule": "AS-EFF-006", "fn": "web.Ctl.fetch", "effects": ["Net"], "detail": "deny Net"},
    {"rule": "AS-EFF-005", "fn": "web.Ctl.fetch", "effects": ["Net"], "detail": "baseline drift"}]}
_report = {"candor": {"version": "0.8.7", "toolchain": "candor-java 0.8.7", "spec": "0.25"},
           "functions": [{"fn": "web.Ctl.fetch", "inferred": ["Net"]},
                         {"fn": "svc.Reflecty", "inferred": ["Unknown"]},
                         {"fn": "util.pure", "inferred": []}]}
_gp = _wjson(_lg, "gate.json", _gate_blocked); _rp = _wjson(_lg, "report.json", _report)
_lglog = os.path.join(_lg, ".candor", "gate-log.jsonl")
_r = _loggate(_gp, _rp, "--log", _lglog)
check("log-gate: exits 0 and appends one record", _r.returncode == 0 and os.path.exists(_lglog)
      and len([ln for ln in open(_lglog) if ln.strip()]) == 1, _r.stderr)
_rec = json.loads(open(_lglog).readline())
check("log-gate: a not-ok gate → verdict=blocked, the AS-EFF codes de-duped", _rec["verdict"] == "blocked"
      and _rec["violations"] == ["AS-EFF-005", "AS-EFF-006"], _rec)
check("log-gate: PATH-FREE — edited is null and sessionId null (a CI gate has no transcript/session)",
      _rec["edited"] is None and _rec["sessionId"] is None, _rec)
check("log-gate: gained = the effects the AS-EFF-005 baseline ratchet reports newly introduced",
      _rec["gained"] == ["Net"], _rec)
check("log-gate: unknowns COUNTED and effects PRESENT read straight from the report; engine from toolchain",
      _rec["unknowns"] == 1 and _rec["effects"] == ["Net"] and _rec["engine"] == "java", _rec)
check("log-gate: blastRadius/reviewMs the jar gate can't compute are 0/null — never fabricated",
      _rec["blastRadius"] == 0 and _rec["reviewMs"] is None, _rec)

# PARITY (TESTING.md single-source): the jar-path record must carry EXACTLY the fields the review-path
# (bash) writer produces and stats/digest read — else the two producers drift and the digest under-reads one.
_CANON = {"ts", "sessionId", "engine", "edited", "gained", "blastRadius",
          "verdict", "violations", "unknowns", "effects", "reviewMs"}
check("log-gate: record keys are byte-for-byte the review-path record shape (no producer drift)",
      set(_rec.keys()) == _CANON, sorted(_rec.keys()))

# an OK gate → verdict=clean; the digest reads a log-gate record end-to-end ("held the line in CI" is real)
_lg2 = _mkd()
_gp2 = _wjson(_lg2, "gate.json", {"spec": "0.25", "ok": True, "violations": []})
_lglog2 = os.path.join(_lg2, ".candor", "gate-log.jsonl")
_loggate(_gp2, "--log", _lglog2)   # NO report arg — optional enrichment
_rec2 = json.loads(open(_lglog2).readline())
check("log-gate: an ok gate → verdict=clean; a missing report → unknowns null, effects [] (no fabrication)",
      _rec2["verdict"] == "clean" and _rec2["unknowns"] is None and _rec2["effects"] == []
      and _rec2["engine"] == "jar", _rec2)
# digest over a jar-logged record renders the owner report (the whole point of P3's jar path)
_dgji = _digest(_lg, "--log", _lglog, "--out", "-")
check("log-gate → digest: the jar-gated CI record surfaces in the owner report ('Held the line')",
      _dgji.returncode == 0 and "Held the line" in _dgji.stdout and "AS-EFF-006" in _dgji.stdout, _dgji.stdout)

# fail-closed CLI surface (TESTING.md §2): a bad/absent verdict exits 2; --log off writes nothing
check("log-gate: an unreadable gate verdict exits 2", _loggate(os.path.join(_lg, "nope.json")).returncode == 2)
_bad = _wjson(_lg, "bad.json", [1, 2, 3])
check("log-gate: a non-object gate verdict exits 2", _loggate(_bad).returncode == 2)
_offlog = os.path.join(_mkd(), "off.jsonl")
check("log-gate: --log off is a no-op (exit 0, nothing written)",
      _loggate(_gp2, "--log", "off").returncode == 0 and not os.path.exists(_offlog))

print()

# ── ⟨0.29⟩ THE PRECONDITION UNDER WHICH `scan`'s GATE MAY OMIT `incomplete` ──────────────────────────
# `observe.py` passes `incomplete=` to the shared `run_gate`; `scan.py` does NOT, and today that is
# CORRECT rather than an oversight: `propagate`'s own contract says the scan route carries effects and
# fs kinds only, while observe carries hosts/cmds/paths too. With no literal surface in a scan-route
# report there is nothing for a benign literal to certify from, so every `allow` rule already fails
# closed with "no visible literal" — measured, including across `--link` with a code report whose entry
# declares `incomplete: ["Fs"]`.
#
# THAT MAKES IT A LATENT HOLE, NOT A LIVE ONE, and latent is exactly the shape this project keeps being
# bitten by: the day the scan route grows a literal surface (the declared half of the drift comparison
# is the obvious candidate — observe already has one), a linked report's `incomplete` will stop crossing
# and a benign declared literal will certify a masked locator. The ⟨0.29⟩ dep-join defect (PART 50) one
# join over.
#
# A COMMENT ALONE ROTS — this rung has already shipped one that contradicted the code for weeks. So the
# precondition is asserted instead: if a scan-route unit ever carries a literal surface, THIS row fails
# and names the call site that must change.
_lat = _mkd()
os.makedirs(os.path.join(_lat, ".claude", "agents"), exist_ok=True)
open(os.path.join(_lat, ".claude", "agents", "w.md"), "w").write(
    "---\nname: writer\ntools: Bash\n---\nAn agent that writes files.\n")
_lo = os.path.join(_lat, "o")
subprocess.run([sys.executable, "-m", "candor_agents.scan", _lat, "--out", _lo, "--fleet", "t"],
               capture_output=True, text=True)
_latrep = json.load(open(f"{_lo}.t.Fleet.json"))
_latkeys = set().union(*[set(f.keys()) for f in _latrep["functions"]]) if _latrep["functions"] else set()
check("⟨0.29⟩ scan-route units carry NO literal surface — the precondition letting scan's run_gate omit "
      "`incomplete`; if this fails, pass an incomplete map at scan.py's run_gate call (see the comment "
      "there) before shipping the surface",
      not (_latkeys & {"hosts", "cmds", "paths", "tables"}), f"surfaces present: {sorted(_latkeys)}")

print()


print(f"test: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
