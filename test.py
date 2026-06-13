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

# PUNCTUATION-adjacent names: a delegate referenced as `name:`, `(name)`, or `name/x` must still be
# narrowed-to. The earlier delimiter class ([`'"\s.,]) missed these, and since narrowing keeps only
# matched names, the punctuation-adjacent delegate was silently DROPPED — its effects didn't propagate.
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="Order: security-auditor: audits, then (reviewer) reviews, finally helper."),
    "security-auditor.md": agent("security-auditor", "WebFetch"),  # Net — followed by ':'
    "reviewer.md": agent("reviewer", "Bash"),                       # Exec — wrapped in '()'
    "helper.md": agent("helper", "Read"),                           # Fs — trailing '.'
    "ignored.md": agent("ignored", "WebSearch"),                    # not mentioned → excluded
})
check("punctuation-adjacent names all narrow (`name:`, `(name)`, `name.`)",
      cg["boss"] == ["helper", "reviewer", "security-auditor"], f"got {cg['boss']}")
check("boss inherits ALL three punctuation-adjacent delegates' effects",
      entry(rep, "boss")["inferred"] == ["Exec", "Fs", "Net"], f"got {entry(rep, 'boss')['inferred']}")

# a name embedded in a LONGER name is NOT a mention (boundary holds the precise direction too)
rep, cg = scan({
    "boss.md": agent("boss", "Agent", body="Only ever use code-reviewer."),
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

# ── 3. ambient authority (no tools: line) ─────────────────────────────────────────────────────────
rep, cg = scan({"legacy.md": agent("legacy", None)}, mcp=["billing"])
e = entry(rep, "legacy")
check("ambient agent is unresolved", e["unresolved"] is True)
check("ambient agent carries Unknown + the big set",
      {"Exec", "Fs", "Net", "Clock", "Ipc", "Unknown"} <= set(e["inferred"]), f"got {e['inferred']}")
check("ambient why names the cause", "ambient:tools-unrestricted" in e.get("unknownWhy", []))
check("ambient reaches the uncurated MCP server", "mcp-uncurated:billing" in e.get("unknownWhy", []))

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
          obs["candor"]["version"] == __import__("scan").VERSION, obs["candor"]["version"])
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
check("bash_cmds: a backtick substitution AND the command after it are both captured",
      bash_cmds("VERSION=`cat VERSION` ./deploy.sh") == {"cat", "deploy.sh"})
check("bash_cmds: a bare backtick substitution contributes its head",
      bash_cmds("echo `git rev-parse HEAD`") == {"echo", "git"})

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

# drift must NOT call a command/cron/session a "trim candidate agent" — they aren't agents and aren't
# recorded as distinct units in transcripts (the bug: every declared unit got the agent-trim advice).
_dd = tempfile.mkdtemp()
os.makedirs(os.path.join(_dd, ".claude", "agents")); os.makedirs(os.path.join(_dd, ".claude", "commands"))
os.makedirs(os.path.join(_dd, ".transcripts"))
open(os.path.join(_dd, ".claude", "agents", "a.md"), "w").write(agent("a", "WebFetch"))
open(os.path.join(_dd, ".claude", "commands", "c.md"), "w").write("---\nallowed-tools: Bash(curl:*)\n---\nx\n")
_j.dump([{"id": "n", "cron": "0 9 * * *", "prompt": "go", "durable": True}], open(os.path.join(_dd, ".claude", "scheduled_tasks.json"), "w"))
r = subprocess.run([sys.executable, "cli.py", "drift", _dd, "--transcripts", os.path.join(_dd, ".transcripts")], capture_output=True, text=True)
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
    # the matcher-aware per-agent reach: an agent whose tools MATCH the PostToolUse matcher edges to
    # hooks and inherits Exec; one whose tools can't trigger it does NOT.
    wr, rd = entry(rep, "writer"), entry(rep, "reader")
    check("hooks: a matching agent (Edit) edges to hooks and inherits Exec",
          wr is not None and "hooks" in wr["calls"] and "Exec" in wr["inferred"], json.dumps(wr))
    check("hooks: a non-matching agent (Read/Grep) does NOT edge to a Write|Edit hook",
          rd is not None and "hooks" not in rd["calls"] and "Exec" not in rd["inferred"], json.dumps(rd))

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

# a real agent named `hooks` or `session` must NOT be clobbered by the synthetic units
with tempfile.TemporaryDirectory() as td:
    adir = os.path.join(td, ".claude", "agents"); os.makedirs(adir)
    open(os.path.join(adir, "hooks.md"), "w").write(agent("hooks", "WebFetch"))
    open(os.path.join(adir, "session.md"), "w").write(agent("session", "Grep"))
    json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "./h.sh"}]}]}},
              open(os.path.join(td, ".claude", "settings.json"), "w"))
    out = os.path.join(td, "r")
    r = subprocess.run([sys.executable, SCAN, td, "--out", out, "--fleet", "t"], capture_output=True, text=True)
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
    r = subprocess.run([sys.executable, "cli.py", "drift", td, "--transcripts", tdir],
                       capture_output=True, text=True)
    check("drift: hooks are 'not observable', never a trim candidate",
          "hook runs are not observable" in r.stdout and "hooks: declared {Exec} but NEVER" not in r.stdout,
          r.stdout)

# drift CLI guards: unknown flags fail, a value-less --transcripts fails (not IndexError), and the
# target isn't dropped when it equals the transcripts value.
def driftcli(*a):
    return subprocess.run([sys.executable, "cli.py", "drift", *a], capture_output=True, text=True)
check("drift: an unknown flag fails with exit 2 (not a silent non-strict run)",
      driftcli("fixture", "--strcit", "--transcripts", "fixture/transcripts").returncode == 2)
check("drift: a value-less trailing --transcripts fails cleanly (no IndexError)",
      driftcli("fixture", "--transcripts").returncode == 2)
r = driftcli("fixture", "--transcripts", "fixture")  # target == transcripts value
check("drift: a target equal to the transcripts value is not dropped to '.'",
      "fleet `fixture`" in r.stdout or "no agent definitions under fixture" in (r.stdout + r.stderr), r.stdout + r.stderr)

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
        import importlib, observe as _obs
        importlib.reload(_obs)
        check("transcript slug flattens '_' (underscore project resolves its transcripts)",
              _obs.transcript_dir_for(proj) == pdir, f"{_obs.transcript_dir_for(proj)} != {pdir}")
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        import importlib, observe as _obs
        importlib.reload(_obs)

print()


# ── --agents: the self-describing engine (the contract is an embedded module) ─────────────────────
import agentsmd
check("embedded contract matches AGENTS.md (drift gate — regen: python3 gen-agentsmd.py)",
      agentsmd.AGENTS_MD == open(os.path.join(HERE, "AGENTS.md")).read())
check("agentsmd ships in the wheel (py-modules list)",
      '"agentsmd"' in open(os.path.join(HERE, "pyproject.toml")).read())
r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "--agents"], capture_output=True, text=True)
check("--agents prints the version header + the exact installed contract",
      r.returncode == 0 and r.stdout.startswith("<!-- candor-agents 0.4")
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
    r = subprocess.run([sys.executable, SCAN, d, "--out", out, "--fleet", "t"], capture_output=True, text=True)
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
    json.dump({"candor": {"version": "x", "toolchain": "t", "spec": "0.4"}, "package": "code",
               "functions": [{"fn": "main", "inferred": code_effects, "direct": code_effects,
                              "entryPoint": True, "calls": [], "hash": "code#main"}]},
              open(os.path.join(d, "code.code.Main.json"), "w"))
    out = os.path.join(d, "r")
    rr = subprocess.run([sys.executable, SCAN, d, "--out", out, "--fleet", "t", "--link", os.path.join(d, "code")],
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
import guard
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


print(f"test: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
