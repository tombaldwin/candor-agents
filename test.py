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
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "scan.py")
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
    r = subprocess.run([sys.executable, SCAN, d, "--out", out, "--fleet", "t"],
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
check("narrowed boss inherits only worker's Net", entry(rep, "boss")["inferred"] == ["Net"],
      f"got {entry(rep, 'boss')['inferred']}")

# name at the very START of the body (the regex-anchor case)
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="worker is your only delegate."),
    "worker.md": agent("worker", "WebFetch"),
    "other.md": agent("other", "Bash"),
})
check("start-of-body name still narrows", cg["boss"] == ["worker"], f"got {cg['boss']}")

# ── 2. CHA fallback for unnamed delegation ────────────────────────────────────────────────────────
rep, cg = scan({
    "boss.md": agent("boss", "Agent, Read", body="Delegate as needed."),
    "a.md": agent("a", "WebFetch"),
    "b.md": agent("b", "Bash"),
})
check("unnamed Agent tool CHA-edges to all", cg["boss"] == ["a", "b"], f"got {cg['boss']}")
check("CHA boss inherits the union", entry(rep, "boss")["inferred"] == ["Exec", "Fs", "Net"],
      f"got {entry(rep, 'boss')['inferred']}")

# ── 3. ambient authority (no tools: line) ─────────────────────────────────────────────────────────
rep, cg = scan({"legacy.md": agent("legacy", None)}, mcp=["billing"])
e = entry(rep, "legacy")
check("ambient agent is unresolved", e["unresolved"] is True)
check("ambient agent carries Unknown + the big set",
      {"Exec", "Fs", "Net", "Clock", "Ipc", "Unknown"} <= set(e["inferred"]), f"got {e['inferred']}")
check("ambient why names the cause", "ambient:tools-unrestricted" in e.get("unknownWhy", []))
check("ambient reaches the uncurated MCP server", "mcp:billing" in e.get("unknownWhy", []))

# ── 4. MCP: curated vs Unknown ────────────────────────────────────────────────────────────────────
rep, _ = scan({
    "m.md": agent("m", "mcp__gmail__send, Read"),
    "u.md": agent("u", "mcp__billing__charge"),
})
check("curated MCP classifies (gmail → Net+Ipc), no Unknown",
      entry(rep, "m")["inferred"] == ["Fs", "Ipc", "Net"] and not entry(rep, "m")["unresolved"],
      f"got {entry(rep, 'm')}")
eu = entry(rep, "u")
check("uncurated MCP → Unknown + unknownWhy", eu["unresolved"] and eu.get("unknownWhy") == ["mcp:billing"],
      f"got {eu}")

# an unheard-of builtin tool is Unknown, never silently pure
rep, _ = scan({"x.md": agent("x", "FrobnicateDisk")})
ex = entry(rep, "x")
check("unknown tool name → Unknown (never silent-pure)",
      ex and ex["unresolved"] and ex.get("unknownWhy") == ["tool:FrobnicateDisk"], f"got {ex}")

# ── 4b. declared MCP capabilities (the Unknown killer) ───────────────────────────────────────────
def scan_decl(files, mcp_entries):
    d = tempfile.mkdtemp()
    adir = os.path.join(d, ".claude", "agents")
    os.makedirs(adir)
    for fname, content in files.items():
        open(os.path.join(adir, fname), "w").write(content)
    json.dump({"mcpServers": mcp_entries}, open(os.path.join(d, ".mcp.json"), "w"))
    out = os.path.join(d, "r")
    r = subprocess.run([sys.executable, SCAN, d, "--out", out, "--fleet", "t"],
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
check("effect propagates up a 3-deep named chain",
      entry(rep, "a")["inferred"] == ["Exec"] and entry(rep, "b")["inferred"] == ["Exec"])

# ── 6. pure units: omitted from the report, present in the sidecar ───────────────────────────────
rep, cg = scan({
    "pure.md": agent("pure", "TodoWrite, Skill"),
    "eff.md": agent("eff", "WebFetch"),
})
check("pure agent omitted from the report", entry(rep, "pure") is None)
check("pure agent present in the callgraph sidecar", "pure" in cg)

# ── 7. envelope + main ────────────────────────────────────────────────────────────────────────────
rep, cg = scan({"a.md": agent("a", "Read")})
check("spec envelope (candor.spec = 0.3)", rep["candor"]["spec"] == "0.3")
m = entry(rep, "session")
check("the session root is the entry point and edges to every agent",
      m and m.get("entryPoint") is True and cg["session"] == ["a"])

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
    subprocess.run([sys.executable, SCAN, d, "--out", out], capture_output=True)
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
code = {"candor": {"version": "x", "spec": "0.3"},
        "functions": [{"fn": "main", "loc": "src/main.rs", "inferred": ["Db", "Exec"],
                       "direct": ["Db"], "declared": [], "undeclared": [], "overdeclared": [],
                       "unresolved": False, "calls": [], "entryPoint": True}]}
json.dump(code, open(os.path.join(d, "c.app.scan.json"), "w"))
json.dump({"main": []}, open(os.path.join(d, "c.app.scan.callgraph.json"), "w"))
out = os.path.join(d, "r")
r = subprocess.run([sys.executable, SCAN, d, "--out", out, "--fleet", "t",
                    "--link", os.path.join(d, "c")], capture_output=True, text=True)
rep = json.load(open(f"{out}.t.Fleet.json"))
cg = json.load(open(f"{out}.t.Fleet.callgraph.json"))
er = entry(rep, "runner")
check("--link: Bash agent edges to the code entryPoint", "main" in cg["runner"], f"got {cg['runner']}")
check("--link: Bash agent inherits the code's recorded effects", "Db" in er["inferred"], f"got {er['inferred']}")
check("--link: non-Bash agent does NOT inherit", "Db" not in entry(rep, "watcher")["inferred"])
check("--link: pseudo-node not re-emitted as a fleet row", entry(rep, "main") is None)

print()
print(f"test: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
