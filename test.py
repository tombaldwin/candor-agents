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
      and not any(w == "tool:All tools" for w in ea.get("unknownWhy", [])), f"got {ea}")

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
check("spec envelope (candor.spec = 0.4)", rep["candor"]["spec"] == "0.4")
check("hash join keys emitted (0.4 MUST)", all("#" in f.get("hash", "") for f in rep["functions"]))
check("unitKind names every fleet unit (spec 0.5 draft: agent/session/hooks)",
      all(f.get("unitKind") in ("agent", "session", "hooks") for f in rep["functions"])
      and entry(rep, "session")["unitKind"] == "session")
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
code = {"candor": {"version": "x", "spec": "0.4"},
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


# ── real-fleet probe regressions (the VoltAgent 155-agent scan) ──────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    adir = os.path.join(td, ".claude", "agents")
    os.makedirs(adir)
    open(os.path.join(adir, "README.md"), "w").write("# A catalog readme\nNot an agent; no frontmatter.\n")
    open(os.path.join(adir, "real.md"), "w").write(agent("real", "Read"))
    out = os.path.join(td, "deep", "dir", "r")  # --out into a directory that does not exist yet
    r = subprocess.run([sys.executable, SCAN, td, "--out", out, "--fleet", "t"],
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
    r = subprocess.run([sys.executable, "observe.py", "fixture", "--transcripts", "fixture/transcripts",
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
          obs["candor"]["version"].endswith("0.4.3"), obs["candor"]["version"])
    check("observe: spec 0.4 envelope + hash + package",
          obs["candor"]["spec"] == "0.4" and by["session"]["hash"] == "fixture#session" and obs["package"] == "fixture")
    check("observe: session effects include the transitive delegate surface",
          set(by["session"]["inferred"]) >= {"Exec", "Fs", "Unknown"})
# bash_cmds: the observed-cmds extractor (first non-fixture run found it fabricating heads)
from observe import bash_cmds
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
      bash_cmds("/usr/bin/env python3 x.py") == {"env"} and bash_cmds("'jq' .") == {"jq"})
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

r = subprocess.run([sys.executable, "cli.py", "drift", "fixture", "--transcripts", "fixture/transcripts"],
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
r = subprocess.run([sys.executable, "cli.py", "drift", "fixture", "--transcripts", _an], capture_output=True, text=True)
check("drift: observed-outside-declaration is loud, advisory exit 0",
      "OBSERVED-OUTSIDE-DECLARATION {Exec}" in r.stdout and r.returncode == 0)
r = subprocess.run([sys.executable, "cli.py", "drift", "fixture", "--transcripts", _an, "--strict"], capture_output=True, text=True)
check("drift --strict: an anomaly fails the build (exit 1)", r.returncode == 1)

# ── hooks: settings.json commands are fleet capability surface ────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    adir = os.path.join(td, ".claude", "agents")
    os.makedirs(adir)
    open(os.path.join(adir, "worker.md"), "w").write(agent("worker", "Read"))
    json.dump({"hooks": {
        "PostToolUse": [{"matcher": "Write|Edit",
                         "hooks": [{"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/.claude/check.py"}]}],
        "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash hook.sh && git status"},
                                            {"type": "telepathy"}]}],
    }}, open(os.path.join(td, ".claude", "settings.json"), "w"))
    out = os.path.join(td, "r")
    r = subprocess.run([sys.executable, SCAN, td, "--out", out, "--fleet", "t"], capture_output=True, text=True)
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

# a project with hooks but NO agents still has a capability surface (the pgman shape)
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, ".claude"))
    json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "./stop-hook.sh"}]}]}},
              open(os.path.join(td, ".claude", "settings.json"), "w"))
    r = subprocess.run([sys.executable, SCAN, td, "--out", os.path.join(td, "r"), "--fleet", "t"],
                       capture_output=True, text=True)
    rep = json.load(open(os.path.join(td, "r.t.Fleet.json")))
    check("hooks: an agent-less project with hooks still scans (hooks + session)",
          r.returncode == 0 and entry(rep, "hooks") is not None
          and "stop-hook.sh" in entry(rep, "hooks").get("cmds", []), r.stderr)
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, ".claude"))
    r = subprocess.run([sys.executable, SCAN, td, "--out", os.path.join(td, "r"), "--fleet", "t"],
                       capture_output=True, text=True)
    check("hooks: no agents AND no hooks still exits 2 (nothing to analyze)", r.returncode == 2)

# drift must NOT call an unobserved hooks unit a trim candidate (hook runs aren't tool_use events)
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, ".claude"))
    json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "./h.sh"}]}]}},
              open(os.path.join(td, ".claude", "settings.json"), "w"))
    tdir = os.path.join(td, "tr"); os.makedirs(tdir)
    open(os.path.join(tdir, "s.jsonl"), "w").write(json.dumps({"message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}}]}}) + "\n")
    r = subprocess.run([sys.executable, "cli.py", "drift", td, "--transcripts", tdir],
                       capture_output=True, text=True)
    check("drift: hooks are 'not observable', never a trim candidate",
          "hook runs are not observable" in r.stdout and "hooks: declared {Exec} but NEVER" not in r.stdout,
          r.stdout)

print()


# ── --agents: the self-describing engine (the contract is an embedded module) ─────────────────────
import agentsmd
check("embedded contract matches AGENTS.md (drift gate — regen: python3 gen-agentsmd.py)",
      agentsmd.AGENTS_MD == open(os.path.join(HERE, "AGENTS.md")).read())
check("agentsmd ships in the wheel (py-modules list)",
      '"agentsmd"' in open(os.path.join(HERE, "pyproject.toml")).read())
r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "--agents"], capture_output=True, text=True)
check("--agents prints the version header + the exact installed contract",
      r.returncode == 0 and r.stdout.startswith("<!-- candor-agents-")
      and r.stdout.endswith(agentsmd.AGENTS_MD), r.stdout[:120])

print(f"test: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
