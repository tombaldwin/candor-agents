#!/usr/bin/env python3
"""candor-agents scan — a candor engine over a Claude Code agent fleet.

Reads a project's STATIC fleet declarations:
  <dir>/.claude/agents/*.md      agent definitions (YAML frontmatter: name, description, tools)
  <dir>/.mcp.json                MCP servers (their tools surface as mcp__<server>__*)

and emits a candor-spec §2 report + §2.2 callgraph sidecar:
  <out>.<fleet>.Fleet.json
  <out>.<fleet>.Fleet.callgraph.json

so the EXISTING candor-query binary (candor-rust, unmodified) can answer show/where/callers/map/
whatif — and a §6.2 policy can gate the fleet. That "no candor tool changed" property is the whole
point of the exploration (see DESIGN.md).

Usage: scan.py <project-dir> [--out <prefix>] [--fleet <name>]
"""
import json
import os
import re
import sys

# ── the classifier: tool name -> effect set ──────────────────────────────────────────────────────
# The code engine's posture, ported: a small CURATED table at the boundary; never guess. `Bash` is
# `Exec` (the subprocess boundary — same trade as `Command::new`). An MCP server outside MCP_TABLE
# is an unresolvable call: Unknown, with the server named in unknownWhy.
TOOL_EFFECTS = {
    "Bash": {"Exec"},
    "Edit": {"Fs"}, "Write": {"Fs"}, "NotebookEdit": {"Fs"},
    "Read": {"Fs"}, "Glob": {"Fs"}, "Grep": {"Fs"},
    "WebFetch": {"Net"}, "WebSearch": {"Net"},
    "SendUserFile": {"Ipc"}, "PushNotification": {"Ipc"},
    "ScheduleWakeup": {"Clock"}, "CronCreate": {"Clock"}, "CronDelete": {"Clock"}, "CronList": {"Clock"},
}
FS_KIND = {"Edit": "write", "Write": "write", "NotebookEdit": "write",
           "Read": "read", "Glob": "read", "Grep": "read"}
# Pure routing / internal state: granted everywhere, effects come from the tools they route TO
# (already counted) — flooding them as effects would be the over-approximation candor avoids.
PURE_TOOLS = {
    "Agent", "Skill", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput",
    "TaskStop", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "EnterWorktree", "ExitWorktree",
    "ToolSearch", "Monitor", "LSP", "SendMessage", "Workflow", "RemoteTrigger",
    "ListMcpResourcesTool", "ReadMcpResourceTool",
}
# Curated MCP capability table (under-report-and-say-so; everything else -> Unknown).
MCP_TABLE = {
    "gmail": {"Net", "Ipc"}, "slack": {"Net", "Ipc"}, "github": {"Net"},
    "atlassian": {"Net"}, "filesystem": {"Fs"}, "postgres": {"Db"}, "sqlite": {"Db"},
}
# `tools:` absent => the agent inherits EVERYTHING (Claude Code's default): ambient authority.
# Agent is NOT in the ambient set: stock Claude Code subagents cannot nest-spawn — delegation
# exists only where `Agent` is explicitly granted. Harnesses that DO allow nested spawning can
# opt in with --nested-spawn (found on the wshobson/agents real-fleet run: with Agent ambient,
# 182 ambient agents produced a ~20k-edge all-reaches-all smear; without, the graph is honest).
AMBIENT = sorted(TOOL_EFFECTS)


def parse_frontmatter(text):
    """The agent-file YAML frontmatter subset that matters: name/description/tools (string or list)."""
    m = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:
        return {}, text
    meta, body = {}, m.group(2)
    lines = m.group(1).split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        km = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            if val == "" and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
                items = []
                while i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
                    items.append(lines[i + 1].lstrip()[2:].strip())
                    i += 1
                meta[key] = items
            else:
                meta[key] = val
        i += 1
    return meta, body


def tool_list(meta):
    """The declared tools, or None for 'inherit everything'."""
    t = meta.get("tools")
    if t is None or t == "" or t == "*":
        return None
    if isinstance(t, list):
        return [x.strip() for x in t if x.strip()]
    # Inline YAML list: `tools: []` is EXPLICITLY no tools (maximally confined — pure), and
    # `tools: [a, b]` is a list — not a single tool named "[a, b]". Real-fleet finding.
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        return [x.strip() for x in inner.split(",") if x.strip()] if inner else []
    return [x.strip() for x in t.split(",") if x.strip()]


def classify(tools, mcp_servers):
    """(effects, fs_kinds, unknown_why) for a concrete tool list."""
    effs, fs, why = set(), set(), set()
    for t in tools:
        if t in TOOL_EFFECTS:
            effs |= TOOL_EFFECTS[t]
            if t in FS_KIND:
                fs.add(FS_KIND[t])
        elif t.startswith("mcp__"):
            server = t.split("__")[1]
            if server in MCP_TABLE:
                effs |= MCP_TABLE[server]
            else:
                effs.add("Unknown")
                why.add(f"mcp:{server}")
        elif t in PURE_TOOLS:
            pass
        else:
            # A tool we've never heard of is an unresolvable call, not silently pure.
            effs.add("Unknown")
            why.add(f"tool:{t}")
    # Ambient MCP reach: an agent that inherits everything reaches every configured server too.
    return effs, fs, why


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = args[0]
    out = "report"
    fleet = os.path.basename(os.path.abspath(root)) or "fleet"
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]
        if a == "--fleet" and i + 1 < len(args):
            fleet = args[i + 1]
    nested = "--nested-spawn" in args
    link = None
    for i, a in enumerate(args):
        if a == "--link" and i + 1 < len(args):
            link = args[i + 1]

    # MCP servers configured for the project.
    mcp_servers = []
    mcp_path = os.path.join(root, ".mcp.json")
    if os.path.exists(mcp_path):
        try:
            mcp_servers = sorted(json.load(open(mcp_path)).get("mcpServers", {}).keys())
        except Exception as e:
            print(f"candor-agents: unreadable .mcp.json ({e}) — servers unknown", file=sys.stderr)

    # Agent definitions.
    agents = {}  # name -> {tools: list|None, body, desc}
    adir = os.path.join(root, ".claude", "agents")
    if os.path.isdir(adir):
        for f in sorted(os.listdir(adir)):
            if not f.endswith(".md"):
                continue
            meta, body = parse_frontmatter(open(os.path.join(adir, f)).read())
            name = meta.get("name") or f[:-3]
            agents[name] = {"tools": tool_list(meta), "body": body, "desc": str(meta.get("description", "")), "file": f}
    if not agents:
        print(f"candor-agents: no agent definitions under {adir} — nothing to analyze.", file=sys.stderr)
        return 2

    # ── edges (delegation) ────────────────────────────────────────────────────────────────────
    # Ladder mirrors the code engine: named-delegation narrowing (devirt) > CHA over all agents.
    names = sorted(agents)
    calls = {}
    for name, a in agents.items():
        has_agent_tool = "Agent" in (a["tools"] or []) or (nested and a["tools"] is None)
        edges = []
        if has_agent_tool:
            mentioned = [n for n in names if n != name and re.search(rf"(?:^|[`'\"\s]){re.escape(n)}[`'\"\s.,]", a["body"] + " " + a["desc"] + " ")]
            edges = mentioned if mentioned else [n for n in names if n != name]  # CHA fallback
        calls[name] = sorted(edges)
    # The session root: an entry point holding every tool + every configured MCP server, able to
    # spawn every agent. Named `session` (not `main`): in combined mode (fleet + code reports under
    # ONE prefix) the crate's `fn main` would collide with it.
    calls["session"] = names

    # ── per-agent direct effects ──────────────────────────────────────────────────────────────
    direct, fs_detail, why_map, unresolved_direct = {}, {}, {}, {}
    for name, a in agents.items():
        tools = a["tools"]
        if tools is None:
            # Ambient authority: every built-in + every configured MCP server's tools.
            effs, fs, why = classify(AMBIENT, mcp_servers)
            for s in mcp_servers:
                if s in MCP_TABLE:
                    effs |= MCP_TABLE[s]
                else:
                    effs.add("Unknown")
                    why.add(f"mcp:{s}")
            why.add("ambient:tools-unrestricted")
            effs.add("Unknown")
        else:
            effs, fs, why = classify(tools, mcp_servers)
        direct[name], fs_detail[name], why_map[name] = effs, fs, why
        unresolved_direct[name] = "Unknown" in effs
    ROOT = "session"
    me, mf, mw = classify(AMBIENT, mcp_servers)
    for s in mcp_servers:
        if s in MCP_TABLE:
            me |= MCP_TABLE[s]
        else:
            me.add("Unknown")
            mw.add(f"mcp:{s}")
    direct[ROOT], fs_detail[ROOT], why_map[ROOT] = me, mf, mw
    unresolved_direct[ROOT] = "Unknown" in me

    # ── --link: the Exec-boundary refinement ─────────────────────────────────────────────────
    # Edge every Bash-holding (or ambient) agent to each entryPoint of the linked CODE report, and
    # seed the entry as a pseudo-node carrying its recorded transitive effects. The pseudo-node is
    # NOT re-emitted (it lives in the code report); under a merged prefix the cross edge makes
    # callers/whatif walk from a code function up into the FLEET.
    linked = {}  # entry fn -> its inferred effects (from the code report)
    if link:
        import glob as _glob
        for rp in sorted(_glob.glob(f"{link}.*.json")):
            if rp.endswith(".callgraph.json") or ".encountered-" in rp or rp.endswith(".calibrated.json"):
                continue
            try:
                cr = json.load(open(rp))
            except Exception:
                continue
            for f in cr.get("functions", []):
                if f.get("entryPoint"):
                    linked[f["fn"]] = set(f.get("inferred", []))
        if not linked:
            print(f"candor-agents: --link {link}: no entryPoint functions found — nothing linked", file=sys.stderr)
        for name, a in agents.items():
            runs_code = a["tools"] is None or "Bash" in a["tools"]
            if runs_code:
                calls[name] = sorted(set(calls[name]) | set(linked))
        calls[ROOT] = sorted(set(calls[ROOT]) | set(linked))

    # ── transitive fixpoint (spec §5a) ────────────────────────────────────────────────────────
    inferred = {n: set(direct[n]) for n in calls}
    for fn_, effs_ in linked.items():
        inferred.setdefault(fn_, set(effs_))
    changed = True
    while changed:
        changed = False
        for n, callees in calls.items():
            for c in callees:
                add = inferred.get(c, set()) - inferred[n]
                if add:
                    inferred[n] |= add
                    changed = True
    fs_tr = {n: set(fs_detail.get(n, set())) for n in calls}
    changed = True
    while changed:
        changed = False
        for n, callees in calls.items():
            for c in callees:
                add = fs_tr.get(c, set()) - fs_tr[n]
                if add:
                    fs_tr[n] |= add
                    changed = True

    # ── emit the spec §2 envelope + §2.2 sidecar ─────────────────────────────────────────────
    functions = []
    for n in sorted(calls):
        if n in linked and n not in agents:
            continue  # pseudo-node: lives in the linked code report
        effs = inferred[n]
        if not effs:
            continue  # pure units are omitted from the report (present in the sidecar)
        entry = {
            "fn": n,
            "loc": agents[n]["file"] if n in agents else "(session root)",
            "inferred": sorted(effs),
            "direct": sorted(direct.get(n, set())),
            "declared": [], "undeclared": sorted(effs - {"Unknown"}), "overdeclared": [],
            "unresolved": "Unknown" in effs,
            "calls": calls[n],
        }
        if fs_tr.get(n):
            entry["fs"] = sorted(fs_tr[n])
        if why_map.get(n):
            entry["unknownWhy"] = sorted(why_map[n])
        if n == "session":
            entry["entryPoint"] = True
        functions.append(entry)

    report = {"candor": {"version": "agents-poc", "toolchain": "claude-code", "spec": "0.3"},
              "functions": functions}
    rp = f"{out}.{fleet}.Fleet.json"
    cp = f"{out}.{fleet}.Fleet.callgraph.json"
    json.dump(report, open(rp, "w"), indent=1)
    json.dump({n: calls[n] for n in sorted(calls)}, open(cp, "w"), indent=1)
    print(f"candor-agents: {len(functions)} effectful unit(s) of {len(calls)} → {rp} (+ callgraph sidecar)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
