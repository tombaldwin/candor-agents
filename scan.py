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

SPEC = "0.4"
VERSION = "agents-0.4.3"

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
    # legacy/alias builtin names still declared by REAL fleets (the wp-calypso/MRC/Updog_restore
    # public-fleet sweep found LS and MultiEdit reading as Unknown — a curation gap, not honesty)
    "LS": {"Fs"}, "MultiEdit": {"Fs"}, "NotebookRead": {"Fs"},
}
FS_KIND = {"Edit": "write", "Write": "write", "NotebookEdit": "write", "MultiEdit": "write",
           "Read": "read", "Glob": "read", "Grep": "read", "LS": "read", "NotebookRead": "read"}
# Pure routing / internal state: granted everywhere, effects come from the tools they route TO
# (already counted) — flooding them as effects would be the over-approximation candor avoids.
# `Task` is the LEGACY name of the Agent tool — it also counts for delegation (see has_agent_tool).
PURE_TOOLS = {
    "Agent", "Task", "Skill", "TodoWrite", "TodoRead", "TaskCreate", "TaskUpdate", "TaskList",
    "TaskGet", "TaskOutput", "TaskStop", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
    "EnterWorktree", "ExitWorktree", "ToolSearch", "Monitor", "LSP", "SendMessage", "Workflow",
    "RemoteTrigger", "ListMcpResourcesTool", "ReadMcpResourceTool",
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


# keywords a command FOLLOWS (`then git push`) vs keywords followed by non-commands (`for f in …`)
_KW_SKIP = {"if", "then", "else", "elif", "do", "while", "until", "time", "exec", "!", "{", "}"}
_KW_DROP = {"for", "case", "select", "function", "in", "fi", "done", "esac"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_CMD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SUBST = re.compile(r"[$<]\(\s*([A-Za-z0-9._+/-]+)")


def bash_cmds(command):
    """Command heads from a shell string — the decidable literal subset, fabrication-averse.

    Every pipeline/sequence segment contributes its head (so `cd x && cargo build` reads BOTH),
    split quote-aware so an awk program or python -c body is never read as commands; everything
    from the first heredoc on is data, not commands (conservatively dropping what follows);
    leading VAR=… assignments are skipped; a head that isn't a plain word (comments, redirects,
    expansions) drops its segment rather than guessing. Command/process substitutions ($(git …),
    <(sort …)) contribute their own heads — those run — except inside single quotes.
    """
    s = command.split("<<", 1)[0]
    segs, cur, unsq, q = [], [], [], None
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if q == "'":
            cur.append(ch)
            unsq.append(ch if ch == "'" else " ")
            q = None if ch == "'" else q
        elif q == '"':
            if ch == "\\" and i + 1 < n:
                cur.append(s[i:i + 2])
                unsq.append("  ")
                i += 2
                continue
            cur.append(ch)
            unsq.append(ch)
            q = None if ch == '"' else q
        elif ch == "\\" and i + 1 < n:
            cur.append(s[i:i + 2])
            unsq.append("  ")
            i += 2
            continue
        elif ch in "'\"":
            q = ch
            cur.append(ch)
            unsq.append(ch)
        elif ch == "#" and (not cur or cur[-1] in " \t"):
            while i < n and s[i] != "\n":  # a comment's `;`/`|`/apostrophes are prose, not shell
                i += 1
            continue
        elif ch == "&" and ((i > 0 and s[i - 1] == ">") or (i + 1 < n and s[i + 1] == ">")):
            cur.append(ch)  # `2>&1` / `&>` are redirects, not separators
            unsq.append(ch)
        elif ch in ";|&\n":
            segs.append("".join(cur))
            cur = []
            unsq.append(ch)
        else:
            cur.append(ch)
            unsq.append(ch)
        i += 1
    segs.append("".join(cur))

    heads = set()
    for seg in segs:
        for tok in seg.split():
            if _ASSIGN.match(tok):
                if "$(" in tok or "`" in tok:
                    break  # the value opens a substitution — the _SUBST pass owns its head
                continue
            if tok.endswith(")") and not tok.startswith("("):
                continue  # a case arm (`audit)`) — the command, if any, follows it
            if tok[0] in "'\"" and not (len(tok) > 1 and tok.endswith(tok[0])):
                break  # a quoted path with spaces ("/Applications/Google Chrome…") — unsplittable
            name = tok.strip("'\"()").rsplit("/", 1)[-1]
            if name in _KW_SKIP:
                continue
            if name not in _KW_DROP and _CMD_NAME.match(name):
                heads.add(name)
            break
    for m in _SUBST.finditer("".join(unsq)):
        name = m.group(1).rsplit("/", 1)[-1]
        if name not in _KW_SKIP and name not in _KW_DROP and _CMD_NAME.match(name):
            heads.add(name)
    return heads


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
    # `tools: All tools` (and bare `all`) — the human "everything" convention, found ×12 on a real
    # public fleet reading as an unknown tool named "All tools". It MEANS ambient authority.
    if isinstance(t, str) and t.strip().lower() in ("all", "all tools"):
        return None
    if isinstance(t, list):
        return [x.strip() for x in t if x.strip()]
    # Inline YAML list: `tools: []` is EXPLICITLY no tools (maximally confined — pure), and
    # `tools: [a, b]` is a list — not a single tool named "[a, b]". Real-fleet finding.
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        return [x.strip() for x in inner.split(",") if x.strip()] if inner else []
    return [x.strip() for x in t.split(",") if x.strip()]


def classify(tools, mcp_servers, declared_mcp=None, declared_bad=None):
    """(effects, fs_kinds, unknown_why) for a concrete tool list."""
    declared_mcp = declared_mcp or {}
    declared_bad = declared_bad or {}
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
            elif server in declared_mcp:
                effs |= declared_mcp[server]  # the project's claim — declared, not verified
            elif server in declared_bad:
                effs.add("Unknown")
                why.add(f"mcp-decl-invalid:{server}:{declared_bad[server]}")
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
    # An unknown flag must FAIL, not be silently ignored or read as the project dir (a typo'd
    # flag near a gate deserves exit 2, not a confusing scan).
    known = {"--out", "--fleet", "--nested-spawn", "--link"}
    skip = set()
    for i, a in enumerate(args):
        if a in ("--out", "--fleet", "--link"):
            skip.add(i + 1)
        elif a.startswith("--") and a not in known and i not in skip:
            print(f"candor-agents: unknown flag {a} (usage: scan <dir> [--out <prefix>] [--fleet <name>] [--link <prefix>] [--nested-spawn])", file=sys.stderr)
            return 2
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

    # MCP servers configured for the project — plus any DECLARED capabilities: a `candorEffects`
    # array on a server's entry ("candorEffects": ["Net","Ipc"]) classifies that server exactly like
    # a curated-table entry, killing its Unknown. Two-tier trust, mirroring the code engines:
    # the curated MCP_TABLE is candor's own claim; a declaration is the PROJECT's claim (the
    # classify_extra / CANDOR_DEPS analog) — accepted as stated, so the report is only as true as
    # the declaration (declared, not verified). An effect name outside the vocabulary is NEVER
    # silently accepted: the server stays Unknown with `mcp-decl-invalid:<server>:<name>` so a typo
    # ("net") can't silently narrow the surface. `"candorEffects": []` declares a PURE server.
    mcp_servers = []
    declared_mcp = {}  # server -> declared effect set (validated)
    declared_bad = {}  # server -> the invalid name that voided its declaration
    VOCAB = {"Net", "Fs", "Db", "Exec", "Env", "Clock", "Ipc", "Log", "Rand", "Clipboard"}
    mcp_path = os.path.join(root, ".mcp.json")
    if os.path.exists(mcp_path):
        try:
            entries = json.load(open(mcp_path)).get("mcpServers", {})
            mcp_servers = sorted(entries.keys())
            for name, cfg in entries.items():
                decl = cfg.get("candorEffects") if isinstance(cfg, dict) else None
                if decl is None:
                    continue
                bad = [e for e in decl if e not in VOCAB]
                if bad:
                    declared_bad[name] = bad[0]
                else:
                    declared_mcp[name] = set(decl)  # [] = declared PURE (maximally confined)
        except Exception as e:
            print(f"candor-agents: unreadable .mcp.json ({e}) — servers unknown", file=sys.stderr)

    # Agent definitions.
    agents = {}  # name -> {tools: list|None, body, desc}
    adir = os.path.join(root, ".claude", "agents")
    if os.path.isdir(adir):
        skipped = []
        for f in sorted(os.listdir(adir)):
            if not f.endswith(".md"):
                continue
            meta, body = parse_frontmatter(open(os.path.join(adir, f)).read())
            if not meta:
                # no frontmatter at all (a README, notes…): Claude Code won't load it — counting
                # it would FABRICATE an ambient-authority unit the runtime doesn't have
                skipped.append(f)
                continue
            name = meta.get("name") or f[:-3]
            agents[name] = {"tools": tool_list(meta), "body": body, "desc": str(meta.get("description", "")), "file": f}
        if skipped:
            print(f"candor-agents: skipped {len(skipped)} .md file(s) with no frontmatter "
                  f"(not agent definitions): {', '.join(skipped[:5])}{'…' if len(skipped) > 5 else ''}",
                  file=sys.stderr)
    # ── hooks: commands the harness runs AUTOMATICALLY on tool events ─────────────────────────
    # `.claude/settings.json` / `settings.local.json` hook entries are fleet capability surface:
    # a PreToolUse/PostToolUse/Stop hook executes a shell command on every matching event with no
    # agent deciding anything. One `hooks` unit carries them — Exec (the Bash cliff applies) plus
    # the command heads as the literal surface; the session root edges to it. A hook type this
    # scanner doesn't know reads Unknown, never silence. User-level (~/.claude) hooks are out of
    # scope: the scan describes the PROJECT.
    hook_cmds, hook_events, hook_why = set(), [], set()
    for sf in ("settings.json", "settings.local.json"):
        sp = os.path.join(root, ".claude", sf)
        if not os.path.exists(sp):
            continue
        try:
            hooks_cfg = json.load(open(sp)).get("hooks") or {}
        except Exception as e:
            print(f"candor-agents: unreadable .claude/{sf} ({e}) — its hooks are UNKNOWN", file=sys.stderr)
            hook_why.add(f"hooks-unreadable:{sf}")
            continue
        for event in sorted(hooks_cfg):
            entries = hooks_cfg[event]
            if not isinstance(entries, list):
                continue
            n_cmds = 0
            for ent in entries:
                for h in (ent.get("hooks") or []) if isinstance(ent, dict) else []:
                    if not isinstance(h, dict):
                        continue
                    if h.get("type") == "command" and isinstance(h.get("command"), str):
                        hook_cmds |= bash_cmds(h["command"])
                        n_cmds += 1
                    else:
                        hook_why.add(f"hook-type:{h.get('type', '?')}")
            if n_cmds:
                hook_events.append(f"{event}({n_cmds})")
    has_hooks = bool(hook_events or hook_why)

    if not agents and not has_hooks:
        print(f"candor-agents: no agent definitions under {adir} and no hooks — nothing to analyze.", file=sys.stderr)
        return 2

    # ── edges (delegation) ────────────────────────────────────────────────────────────────────
    # Ladder mirrors the code engine: named-delegation narrowing (devirt) > CHA over all agents.
    names = sorted(agents)
    calls = {}
    for name, a in agents.items():
        has_agent_tool = ("Agent" in (a["tools"] or []) or "Task" in (a["tools"] or [])
                          or (nested and a["tools"] is None))
        edges = []
        if has_agent_tool:
            mentioned = [n for n in names if n != name and re.search(rf"(?:^|[`'\"\s]){re.escape(n)}[`'\"\s.,]", a["body"] + " " + a["desc"] + " ")]
            edges = mentioned if mentioned else [n for n in names if n != name]  # CHA fallback
        calls[name] = sorted(edges)
    # The session root: an entry point holding every tool + every configured MCP server, able to
    # spawn every agent. Named `session` (not `main`): in combined mode (fleet + code reports under
    # ONE prefix) the crate's `fn main` would collide with it.
    calls["session"] = sorted(names + (["hooks"] if has_hooks else []))
    if has_hooks:
        calls["hooks"] = []

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
                elif s in declared_mcp:
                    effs |= declared_mcp[s]
                elif s in declared_bad:
                    effs.add("Unknown")
                    why.add(f"mcp-decl-invalid:{s}:{declared_bad[s]}")
                else:
                    effs.add("Unknown")
                    why.add(f"mcp:{s}")
            why.add("ambient:tools-unrestricted")
            effs.add("Unknown")
        else:
            effs, fs, why = classify(tools, mcp_servers, declared_mcp, declared_bad)
        direct[name], fs_detail[name], why_map[name] = effs, fs, why
        unresolved_direct[name] = "Unknown" in effs
    ROOT = "session"
    me, mf, mw = classify(AMBIENT, mcp_servers)
    for s in mcp_servers:
        if s in MCP_TABLE:
            me |= MCP_TABLE[s]
        elif s in declared_mcp:
            me |= declared_mcp[s]
        elif s in declared_bad:
            me.add("Unknown")
            mw.add(f"mcp-decl-invalid:{s}:{declared_bad[s]}")
        else:
            me.add("Unknown")
            mw.add(f"mcp:{s}")
    direct[ROOT], fs_detail[ROOT], why_map[ROOT] = me, mf, mw
    unresolved_direct[ROOT] = "Unknown" in me
    if has_hooks:
        direct["hooks"] = {"Exec"} | ({"Unknown"} if hook_why else set())
        fs_detail["hooks"], why_map["hooks"] = set(), set(hook_why)
        unresolved_direct["hooks"] = bool(hook_why)

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
            "loc": agents[n]["file"] if n in agents
                   else f".claude/settings*.json hooks: {', '.join(hook_events) or '(unreadable)'}" if n == "hooks"
                   else "(session root)",
            "inferred": sorted(effs),
            "direct": sorted(direct.get(n, set())),
            "declared": [], "undeclared": sorted(effs - {"Unknown"}), "overdeclared": [],
            "unresolved": "Unknown" in effs,
            # spec ⟨0.5⟩ unitKind: a fleet's units are not functions — name what each one is, so a
            # merged prefix (fleet + code reports) renders sensibly. Informative, never semantic.
            "unitKind": "session" if n == "session" else "hooks" if n == "hooks" else "agent",
            "calls": calls[n],
        }
        if fs_tr.get(n):
            entry["fs"] = sorted(fs_tr[n])
        if why_map.get(n):
            entry["unknownWhy"] = sorted(why_map[n])
        if n == "hooks" and hook_cmds:
            entry["cmds"] = sorted(hook_cmds)
        if n == "session":
            entry["entryPoint"] = True
        # spec-0.4 MUST: every producer emits the cross-boundary join key — a fleet report is
        # chainable like any sibling (`<fleet>#<agent>`, the pkg#LocalName shape).
        entry["hash"] = f"{fleet}#{n}"
        functions.append(entry)

    report = {"candor": {"version": VERSION, "toolchain": "claude-code", "spec": SPEC},
              "package": fleet,
              "functions": functions}
    rp = f"{out}.{fleet}.Fleet.json"
    cp = f"{out}.{fleet}.Fleet.callgraph.json"
    os.makedirs(os.path.dirname(os.path.abspath(rp)), exist_ok=True)
    json.dump(report, open(rp, "w"), indent=1)
    json.dump({n: calls[n] for n in sorted(calls)}, open(cp, "w"), indent=1)
    print(f"candor-agents: {len(functions)} effectful unit(s) of {len(calls)} → {rp} (+ callgraph sidecar)")
    if has_hooks:
        print(f"candor-agents: hooks run AUTOMATICALLY on tool events — {', '.join(hook_events) or 'unreadable settings'}"
              f"{'; cmds: ' + ', '.join(sorted(hook_cmds)) if hook_cmds else ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
