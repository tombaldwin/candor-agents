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

SPEC = "0.8"
VERSION = "agents-0.8.0"

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
# Curated MCP capability table (under-report-and-say-so; everything else -> Unknown). Keyed by the
# CONVENTIONAL server name (the official / widely-used name); a project that names its server
# differently keeps Unknown until it declares `candorEffects` (DECLARING.md) — the table never guesses
# from a non-standard name. Only servers with an UNAMBIGUOUS effect are listed.
MCP_TABLE = {
    # messaging that also crosses to the user's device/account (Net + Ipc)
    "gmail": {"Net", "Ipc"}, "slack": {"Net", "Ipc"},
    # web / SaaS APIs (Net)
    "github": {"Net"}, "gitlab": {"Net"}, "atlassian": {"Net"}, "notion": {"Net"}, "linear": {"Net"},
    "sentry": {"Net"}, "stripe": {"Net"}, "cloudflare": {"Net"}, "gdrive": {"Net"}, "google-maps": {"Net"},
    "brave-search": {"Net"}, "fetch": {"Net"}, "puppeteer": {"Net"}, "playwright": {"Net"},
    # databases (Db)
    "postgres": {"Db"}, "sqlite": {"Db"}, "mysql": {"Db"}, "mongodb": {"Db"}, "redis": {"Db"},
    # local stores (Fs) and time (Clock) — the official servers of these names
    "filesystem": {"Fs"}, "memory": {"Fs"}, "time": {"Clock"},
}
# Refining the Exec cliff (spec §4 ⟨0.5⟩): a literal, statically-known sub-command head MAY be
# classified — its effects are ADDED to the caller (a subprocess still spawned, so `Exec` stays),
# and an unrecognised head keeps the bare cliff. A **candor engine** reads Fs/Env only — spec §7
# item 12 (the analyzer self-boundary) GUARANTEES that, so that case is spec-supplied, not curation.
# The rest is a small curated table under §1's under-report rule. INVARIANT: every head here is an
# external tool that does NOT execute the analysed project's own code — so a unit whose shell heads
# are all in this table can't, via the cliff, reach the project's binaries' effects (the §4
# transitive bound; --link relies on it). Heads that orchestrate project code (make/npm/cargo/bash)
# are deliberately ABSENT — they stay the cliff and keep their --link edge.
CANDOR_HEADS = ("candor", "candor-run.sh", "candor-scan", "candor-query", "candor-java",
                "candor-classify", "candor-report", "cargo-candor")
# Only UNAMBIGUOUS single-effect tools belong here. A multi-modal head (`git status` is local vs
# `git push` Net; `rsync` local vs remote) would FABRICATE the effect for its common case — the
# under-report rule forbids it, so those heads keep the bare cliff.
COMMAND_HEAD = {
    "curl": {"Net"}, "wget": {"Net"}, "http": {"Net"}, "ssh": {"Net"}, "scp": {"Net"},
    "sftp": {"Net"}, "ftp": {"Net"}, "telnet": {"Net"},
    "psql": {"Db"}, "mysql": {"Db"}, "sqlite3": {"Db"}, "mongosh": {"Db"}, "mongo": {"Db"},
    "redis-cli": {"Db"}, "cqlsh": {"Db"}, "influx": {"Db"},
}
COMMAND_HEAD.update({h: {"Fs", "Env"} for h in CANDOR_HEADS})  # §7-item-12: analyzers do Fs/Env only
# `tools:` absent => the agent inherits EVERYTHING (Claude Code's default): ambient authority.
# Agent is NOT in the ambient set: stock Claude Code subagents cannot nest-spawn — delegation
# exists only where `Agent` is explicitly granted. Harnesses that DO allow nested spawning can
# opt in with --nested-spawn (found on the wshobson/agents real-fleet run: with Agent ambient,
# 182 ambient agents produced a ~20k-edge all-reaches-all smear; without, the graph is honest).
AMBIENT = sorted(TOOL_EFFECTS)

# Hook events that fire on a TOOL USE (so they reach back into the agent that triggered them) vs
# session-lifecycle events (Stop/SessionStart/…) which fire at the session level only.
TOOL_HOOK_EVENTS = {"PreToolUse", "PostToolUse"}

# ── .candor/config (spec §3.4, amended within 0.8): the checked-in alternative to CANDOR_* env ────
# The shared cross-engine key vocabulary. candor-agents implements `policy` only; the other keys
# drive modes this engine does not have (baseline ratchet, JVM conformance, …) — a config carrying
# one is warned LOUDLY (below), because a reader of the checked-in file could otherwise believe a
# gate is active here that is not.
CONFIG_KEYS = {"policy", "baseline", "strict", "no-ambient", "closed-world", "taint", "deps"}
CONFIG_IMPLEMENTED = {"policy"}


def load_candor_config(target):
    """Locate + parse `.candor/config` for a scan of `target` (spec §3.4). Returns (cfg, base_dir).

    Discovery is anchored to the SCAN TARGET, never the CWD: walk UP from the target to the nearest
    `.candor/config`, so the config that travels with the scanned fleet applies regardless of where
    the process was launched. A set `$CANDOR_CONFIG` overrides discovery entirely. FAIL-CLOSED: a
    configured-but-unusable file (a set CANDOR_CONFIG naming a missing path; a discovered file that
    exists but cannot be read) exits 2 — the file may carry the `policy` gate, and a silently-dropped
    config is a silently-dropped gate (the §6.2 unreadable-policy posture). Only genuine absence is
    an empty config. A key outside the vocabulary warns (a misspelt `policy` must never silently
    drop the gate); a family key this engine does not implement warns that its gate is NOT active
    here. `base_dir` is what RELATIVE path values resolve against (the family decision — the config
    file's location, not the CWD): the directory holding `.candor/` for the canonical layout, the
    override file's own directory otherwise."""
    file = os.environ.get("CANDOR_CONFIG")
    if file is not None:
        if not os.path.isfile(file):
            print(f"candor-agents: CANDOR_CONFIG set but {file or '(empty)'} is not a readable file "
                  f"— failing (exit 2)", file=sys.stderr)
            raise SystemExit(2)
    else:
        d = os.path.abspath(target)
        if not os.path.isdir(d):
            d = os.path.dirname(d)
        while True:
            cand = os.path.join(d, ".candor", "config")
            if os.path.isfile(cand):
                file = cand
                break
            parent = os.path.dirname(d)
            if parent == d:
                break  # filesystem root — genuine absence (no CWD fallback: target-anchored only)
            d = parent
        if file is None:
            return {}, None
    try:
        text = open(file, encoding="utf-8").read()
    except OSError as e:
        print(f"candor-agents: config {file} exists but could not be read ({e}) — failing (exit 2), "
              f"a configured gate source must not vanish silently", file=sys.stderr)
        raise SystemExit(2)
    cfg = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()  # `#` begins a comment, inline too (§6.2 lexical)
        if not line:
            continue
        parts = line.split(None, 1)
        key = parts[0].lower()
        val = parts[1].strip() if len(parts) > 1 else ""  # a bare key = enabled with the empty value
        if key not in CONFIG_KEYS:
            print(f"candor-agents: ignoring unknown config key '{key}' in {file}", file=sys.stderr)
            continue
        if key not in CONFIG_IMPLEMENTED:
            print(f"candor-agents: config key '{key}' is recognized by the candor family but not "
                  f"implemented by candor-agents — this gate is NOT active here ({file})", file=sys.stderr)
        cfg[key] = val
    base = os.path.dirname(os.path.abspath(file))
    if os.path.basename(base) == ".candor":
        base = os.path.dirname(base)  # the canonical <root>/.candor/config: values are root-relative
    return cfg, base


def config_policy(policy_path, target):
    """Resolve the policy source through the config layer (spec §3.4 precedence: the --policy flag →
    $CANDOR_POLICY → `.candor/config` `policy` → none). `policy_path` is the flag/env resolution so
    far (None = unset; '' = set-but-empty, which stays and fails loud on open). The config is always
    LOADED (fail-closed + key warnings apply even when the policy came from the flag/env); its
    `policy` value applies only as the floor, a relative value resolved against the config's own
    directory — never the CWD."""
    cfg, base = load_candor_config(target)
    if policy_path is None and "policy" in cfg:
        policy_path = cfg["policy"]
        if policy_path and not os.path.isabs(policy_path):
            policy_path = os.path.join(base, policy_path)
    return policy_path


def tools_match_matcher(tools, matcher):
    """Whether an agent holding `tools` (None = ambient = every tool) can trigger a tool-event hook
    with this Claude Code `matcher`. Mirrors Claude Code's THREE-TIER matcher semantics (per the hooks
    reference) so candor's edge model matches what the harness actually fires:
      - empty / `*`           → all tools
      - `[A-Za-z0-9_|]+`      → an EXACT tool-name list (`Edit|Write` fires ONLY on Edit and Write,
                                never on `MultiEdit` — that's why the docs list variants explicitly)
      - anything else         → a full regex with SEARCH (partial) semantics, JS `.test()`-style
                                (the docs' own `^Notebook` example is a prefix match → `NotebookEdit`).
    The earlier form force-wrapped every matcher in `^(?:alt)$` (fullmatch) and split on `|`
    unconditionally, so a tier-2 partial regex (`^Notebook`, `Edit$`) UNDER-matched — it failed to
    edge an agent whose tool the hook really fires on, silently dropping that hook's Exec reach.
    Under-report on an unparseable regex rather than fabricate an edge (the family posture)."""
    m = (matcher or "").strip()
    if m in ("", "*"):
        return True
    if tools is None:  # ambient authority holds every tool — matches any non-empty matcher
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", m):  # tier 1: exact `|`-separated tool-name list
        names = {a for a in m.split("|") if a}
        return any(t in names for t in tools)
    try:
        pat = re.compile(m)  # tier 2: a full regex, matched by SEARCH (Claude's JS `.test()`)
    except re.error:
        return False  # unparseable — don't fabricate an edge
    return any(pat.search(t) for t in tools)


# keywords a command FOLLOWS (`then git push`) vs keywords followed by non-commands (`for f in …`).
# The second group also holds TRANSPARENT COMMAND-PREFIX wrappers (`sudo curl`, `env X=1 node`,
# `command git`, `nohup wget`) — like `time`/`exec`, the REAL command follows them, so the scanner
# skips the wrapper to read the wrapped head. Without this, `sudo curl evil.com` in a hook/command
# `!`-line reported only the bare Exec cliff, NOT Net — a `deny Net` fleet gate would miss it
# (the head's §4 refinement was dropped). Only no-arg-before-command wrappers belong here: `timeout`
# (`timeout 5 cmd`) and `nice`/`xargs` (flag/duration before the command) are deliberately ABSENT —
# skipping them would mint a bogus head from the duration or drop nothing useful.
_KW_SKIP = {"if", "then", "else", "elif", "do", "while", "until", "time", "exec", "!", "{", "}",
            "sudo", "doas", "command", "env", "nohup", "setsid"}
            # NOTE: `builtin` is NOT a transparent wrapper — it runs ONLY shell builtins, so `builtin curl`
            # never reaches the network; skipping it would FABRICATE curl's Net (review find). `command`
            # IS correct (it runs external binaries, bypassing functions/aliases).
_KW_DROP = {"for", "case", "select", "function", "in", "fi", "done", "esac"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_CMD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SUBST = re.compile(r"[$<]\(\s*([A-Za-z0-9._+/-]+)")


def _strip_comment(s):
    """Strip a trailing YAML inline comment (` #…`): a `#` preceded by whitespace (or at the start) and
    NOT inside quotes begins a comment, which the real YAML parser Claude uses drops. Without this a
    `tools: Read, Bash # only safe` kept `Bash # only safe` as one token → Unknown, evading a deny gate.
    Quote-aware so a `#` inside a quoted scalar stays literal; `C#`/`a#b` (no leading space) stay."""
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s


def _unquote(s):
    """Strip ONE layer of matching surrounding quotes from a YAML scalar tool token. A user who quotes
    a tool entry to protect its specifier's special chars — `"Bash(git:*)"`, `'mcp__x__y'` (the parens,
    colon, and star invite YAML quoting) — must NOT have it become an unrecognized tool that classifies
    as `Unknown` (its base `"Bash` no longer matches), which would silently turn a definite Exec/Net into
    Unknown and evade an effect-specific `deny Exec`/`deny Net` gate. Quoting is presentation, not meaning."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].strip()
    return s


def split_tools(s):
    """Comma-split a tool list, RESPECTING parens so a specifier with an inner comma stays whole:
    `Agent(worker, researcher), Read` → ['Agent(worker, researcher)', 'Read'] (a naive `.split(',')`
    shattered it). `Agent(...)` is documented agent-frontmatter syntax (the spawn allowlist)."""
    out, buf, depth = [], "", 0
    for ch in s:
        if ch == "(":
            depth += 1; buf += ch
        elif ch == ")":
            depth = max(0, depth - 1); buf += ch
        elif ch == "," and depth == 0:
            out.append(buf); buf = ""
        else:
            buf += ch
    out.append(buf)
    return [u for x in out if (u := _unquote(x))]


def base_tool(t):
    """The base tool NAME of a token, dropping any `(...)` specifier: `Bash(git:*)` → `Bash`,
    `Agent(worker)` → `Agent`, `mcp__github__create(x)` → `mcp__github__create`, `Read` → `Read`. The
    agent tool path must base-strip like the command path / the `live()` deny path already do — else
    `Agent(worker)` fails the `"Agent" in tools` delegation check (silently disabling a real spawn
    reach) and `Bash(git:*)` classifies as Unknown not Exec (evading a `deny Exec` gate)."""
    return t.split("(", 1)[0].strip()


def agent_spawn_allowlist(tools):
    """The agent names in any `Agent(x, y)` / `Task(x)` spawn-allowlist specifier — the HARNESS-ENFORCED
    delegation set, candor's sound devirt analog (the runtime cannot spawn an agent outside it). Returns
    an empty set when no allowlist is declared: a BARE `Agent` grant, whose runtime spawn target is a
    string chosen at runtime and therefore unprovable. `tools` is a (deny-filtered) grant list."""
    out = set()
    for t in (tools or []):
        s = t.strip()
        if base_tool(s) in ("Agent", "Task") and "(" in s and s.endswith(")"):
            inner = s[s.index("(") + 1 : -1]
            out |= {n.strip() for n in inner.split(",") if n.strip()}
    return out


def propagate(seed, edges):
    """Transitive fixpoint over a delegation graph: each unit accumulates its callees' values to a
    least fixpoint (spec §5a). One implementation, used by scan.py (effects, fs kinds) and observe.py
    (effects, fs, hosts, cmds, paths) — so the declared and observed halves of a drift comparison can
    never propagate differently. `seed`: name -> set; `edges`: name -> iterable of callee names."""
    acc = {k: set(v) for k, v in seed.items()}
    changed = True
    while changed:
        changed = False
        for caller, callees in edges.items():
            for callee in callees:
                add = acc.get(callee) or set()
                if add - acc.setdefault(caller, set()):
                    acc[caller] |= add
                    changed = True
    return acc


def bash_cmds(command):
    """Command heads from a shell string — the decidable literal subset, fabrication-averse.

    Every pipeline/sequence segment contributes its head (so `cd x && cargo build` reads BOTH),
    split quote-aware so an awk program or python -c body is never read as commands; everything
    from the first heredoc on is data, not commands (conservatively dropping what follows);
    leading VAR=… assignments are skipped; a head that isn't a plain word (comments, redirects,
    expansions) drops its segment rather than guessing. Command/process substitutions ($(git …),
    <(sort …)) AND legacy backtick substitutions (`cat x`) contribute their own heads — those run.
    """
    heads = set()
    # Backtick substitutions are legacy `$()`: capture their head, then BLANK the span so the
    # quote-aware scanner below (which handles ' and " only) doesn't mis-tokenize a backtick'd
    # command and then drop the REAL command after the assignment (`VERSION=`cat V` ./deploy.sh`
    # previously yielded nothing — both cat and deploy.sh lost).
    for m in re.finditer(r"`\s*([A-Za-z0-9._+/-]+)", command):
        name = m.group(1).rsplit("/", 1)[-1]
        if name not in _KW_SKIP and name not in _KW_DROP and _CMD_NAME.match(name):
            heads.add(name)
    command = re.sub(r"`[^`]*`?", " ", command)
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

    for seg in segs:
        for tok in seg.split():
            if _ASSIGN.match(tok):
                if "$(" in tok:
                    break  # a $() substitution opens in the value — its head comes via _SUBST and
                           # the `;`-split path; descending into it would mint loop-var heads
                continue   # a plain VAR= prefix (backtick values were blanked above) — keep scanning
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


def read_md(path, unreadable):
    """Read a fleet `.md` file, returning its text or None on a permission/encoding error — the error
    is COLLECTED in `unreadable` and disclosed, never raised. A bare `open().read()` let one poison
    file (chmod 000, non-UTF-8) crash the whole scan with a traceback → no report written → the gate
    silently does not run. The JSON readers (.mcp.json/settings.json) already degrade-and-warn; the
    markdown readers must too (found by adversarial review)."""
    try:
        return open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        unreadable.append(f"{os.path.basename(path)} ({type(e).__name__})")
        return None


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
            key, val = km.group(1), _strip_comment(km.group(2)).strip()
            if val == "" and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
                items = []
                while i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
                    items.append(_strip_comment(lines[i + 1].lstrip()[2:]).strip())
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
    if isinstance(t, str):
        t = _unquote(t)  # a whole-value quote (`tools: "Read, Bash"`) — strip before splitting
    # `tools: All tools` (and bare `all`) — the human "everything" convention, found ×12 on a real
    # public fleet reading as an unknown tool named "All tools". It MEANS ambient authority.
    if isinstance(t, str) and t.strip().lower() in ("all", "all tools"):
        return None
    if isinstance(t, list):
        return [u for x in t if (u := _unquote(str(x)))]
    # Inline YAML list: `tools: []` is EXPLICITLY no tools (maximally confined — pure), and
    # `tools: [a, b]` is a list — not a single tool named "[a, b]". Real-fleet finding.
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        return split_tools(inner) if inner else []   # paren-aware (keeps `Agent(a, b)` whole)
    return split_tools(t)


def classify(tools, mcp_servers, declared_mcp=None, declared_bad=None):
    """(effects, fs_kinds, unknown_why) for a concrete tool list."""
    declared_mcp = declared_mcp or {}
    declared_bad = declared_bad or {}
    effs, fs, why = set(), set(), set()
    for t in tools:
        b = base_tool(t)  # strip a `(...)` specifier: `Bash(git:*)`→Bash (Exec, not Unknown — gate-safe)
        if b in TOOL_EFFECTS:
            effs |= TOOL_EFFECTS[b]
            if b in FS_KIND:
                fs.add(FS_KIND[b])
        elif b.startswith("mcp__"):
            server = b.split("__")[1]
            if server in MCP_TABLE:
                effs |= MCP_TABLE[server]
            elif server in declared_mcp:
                effs |= declared_mcp[server]  # the project's claim — declared, not verified
            elif server in declared_bad:
                effs.add("Unknown")
                why.add(f"mcp-decl-invalid:{server}:{declared_bad[server]}")
            else:
                effs.add("Unknown")
                why.add(f"mcp-uncurated:{server}")
        elif b in PURE_TOOLS:
            pass
        else:
            # A tool we've never heard of is an unresolvable call, not silently pure.
            effs.add("Unknown")
            why.add(f"tool-unknown:{t}")
    # Ambient MCP reach: an agent that inherits everything reaches every configured server too.
    return effs, fs, why


def kappa_ledger(why_map, unit_heads, pure_used):
    """The κ-coverage ledger (spec §7 item 14, a conformance MUST) — the fleet analog of the code
    engines' per-scan receipt, canonical marker `κ doesn't know`. What the fleet's curated classifier
    (κ = TOOL_EFFECTS/MCP_TABLE/COMMAND_HEAD/PURE_TOOLS) does NOT cover, named with unit counts:
      - `mcp:<server>`  an uncurated / voided-declaration MCP server (reads Unknown per unit — the
                        fleet domain DISCLOSES these already; the ledger is the per-scan aggregate)
      - `tool:<name>`   a tool name outside every table (reads Unknown per unit)
      - `head:<name>`   a literal sub-command head COMMAND_HEAD doesn't list — the unit keeps only
                        the bare Exec cliff, so the head's own effects are INVISIBLE (not Unknown):
                        the sharpest edge, exactly item 14's silent-purity concern
    plus the curated PURE claims the verdict RELIES on: a PURE_TOOLS grant contributes nothing by
    candor's own review — a claim, not a measurement — so it is disclosed rather than silent.
    Exempt (item 14): tools TOOL_EFFECTS/MCP_TABLE/COMMAND_HEAD cover verb-precisely, and the
    structural origins (`ambient:`, `agent-spawn:`, `hooks-*`) — indeterminacy, not curation gaps."""
    counts = {}
    for whys in why_map.values():
        for w in whys:
            if w.startswith("mcp-uncurated:") or w.startswith("mcp-decl-invalid:"):
                key = "mcp:" + w.split(":")[1]
            elif w.startswith("tool-unknown:"):
                key = "tool:" + w.split(":", 1)[1]
            else:
                continue
            counts[key] = counts.get(key, 0) + 1
    for heads in unit_heads.values():
        for h in heads:
            if h not in COMMAND_HEAD:
                counts["head:" + h] = counts.get("head:" + h, 0) + 1
    lines = []
    if counts:
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{k} ({n} unit{'s' if n != 1 else ''})" for k, n in top[:8])
        more = f" + {len(top) - 8} more" if len(top) > 8 else ""
        lines.append(f"candor-agents: κ doesn't know {len(top)} "
                     f"capabilit{'y' if len(top) == 1 else 'ies'} this fleet declares — an uncurated "
                     f"MCP server / unknown tool reads Unknown (disclosed per unit); an unlisted "
                     f"command head keeps only the bare Exec cliff (its own effects INVISIBLE): "
                     f"{shown}{more}")
    if pure_used:
        lines.append(f"candor-agents: κ relies on {len(pure_used)} reviewed-pure tool grant(s) — a "
                     f"curated claim, not a measurement: {', '.join(sorted(pure_used))}")
    return lines


def print_version():
    """`--version`/`-V` (spec §3.3): the installed build + the candor-spec version it speaks, then an
    upgrade line — fully OFFLINE (candor never phones home; staying current is the agent's job)."""
    print(f"candor-agents {VERSION} (candor-spec {SPEC})")
    print("upgrade: pipx upgrade candor-agents  (or: pip install -U candor-agents)")


_HELP = f"""candor-agents {VERSION} — effect analysis for Claude Code agent fleets (candor-spec {SPEC})

USAGE: candor-agents scan <project-dir> [--out <prefix>] [--json] [--policy <file>]
                          [--gate-json <file>] [--fleet <name>] [--link <prefix>] [--nested-spawn]

  <project-dir>      the fleet root (.claude/agents, .mcp.json, settings, commands, skills, cron)
  --out <prefix>     write <prefix>.<fleet>.Fleet.json + a .callgraph.json sidecar (default: report)
  --json             emit the §2 report envelope as JSON to STDOUT (human/progress goes to stderr)
  --policy <file>    enforce a §6.2 policy file: exit 1 on a violation, 2 if unreadable; honours
                     $CANDOR_POLICY, then a discovered .candor/config `policy`, when absent
  --gate-json <file> write the structured gate verdict {{spec, ok, violations}} as JSON (§3.3 ⟨0.8⟩;
                     `-` = stdout); written whenever the flag is given, exit code unchanged
  --fleet <name>     name the fleet (default: the project dir's basename)
  --link <prefix>    edge Bash-holding units into a linked CODE report's entry points (§4 cliff)
  --nested-spawn     allow ambient agents to nest-spawn (harnesses that permit it)
  -V, --version      print the build + candor-spec version (offline)
  -h, --help         show this help

See https://github.com/tombaldwin/candor-agents"""


def main():
    args = sys.argv[1:]
    # `-V`/`--version` and `-h`/`--help` are print-and-exit MODES, handled BEFORE the arg walk so a
    # single-dash `-h`/`-V` is never mistaken for a flag-value or swallowed by the positional capture.
    if "--version" in args or "-V" in args:
        print_version()
        return 0
    if not args or "--help" in args or "-h" in args:
        print(_HELP, file=sys.stderr)
        return 0 if args else 2
    # ONE flag pass (was four, with a skip-set the validator both built and read). An unknown flag,
    # a flag-shaped or missing value, or a second positional all FAIL with exit 2 — never silently
    # ignored or read as the project dir. The first positional is the scan root.
    usage = ("usage: scan <dir> [--out <prefix>] [--json] [--policy <file>] [--gate-json <file>] "
             "[--fleet <name>] [--link <prefix>] [--nested-spawn]")
    out, fleet, link, nested, root = "report", None, None, False, None
    as_json = False
    gate_json = None
    policy_path = os.environ.get("CANDOR_POLICY")  # the flag (below) overrides this; config is the floor
    i = 0

    def value(flag):
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            print(f"candor-agents: {flag} requires a value ({usage})", file=sys.stderr)
            return None
        return args[i + 1]

    while i < len(args):
        a = args[i]
        if a == "--out":
            v = value("--out")
            if v is None:
                return 2
            out = v; i += 2
        elif a == "--json":
            as_json = True; i += 1
        elif a == "--policy":
            v = value("--policy")
            if v is None:
                return 2
            policy_path = v; i += 2
        elif a == "--gate-json":
            v = value("--gate-json")
            if v is None:
                return 2
            gate_json = v; i += 2
        elif a == "--fleet":
            v = value("--fleet")
            if v is None:
                return 2
            fleet = v; i += 2
        elif a == "--link":
            v = value("--link")
            if v is None:
                return 2
            link = v; i += 2
        elif a == "--nested-spawn":
            nested = True; i += 1
        elif a.startswith("--"):
            print(f"candor-agents: unknown flag {a} ({usage})", file=sys.stderr)
            return 2
        elif root is None:
            root = a; i += 1
        else:
            print(f"candor-agents: unexpected extra argument {a} ({usage})", file=sys.stderr)
            return 2
    if root is None:
        print(f"candor-agents: a project dir is required ({usage})", file=sys.stderr)
        return 2
    if fleet is None:
        fleet = os.path.basename(os.path.abspath(root)) or "fleet"
    # `.candor/config` (spec §3.4): loaded target-anchored BEFORE any scanning, so a configured-but-
    # unusable config fails the run up front (exit 2) and a checked-in `policy` becomes the gate floor.
    policy_path = config_policy(policy_path, root)

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
    dup_names = set()  # agent `name:`s declared by >1 file — disambiguated below, NEVER silently clobbered
    unreadable = []  # .md files that couldn't be read (permission/encoding) — disclosed, never fatal
    adir = os.path.join(root, ".claude", "agents")
    if os.path.isdir(adir):
        skipped = []
        for f in sorted(os.listdir(adir)):
            if not f.endswith(".md"):
                continue
            text = read_md(os.path.join(adir, f), unreadable)
            if text is None:  # permission/encoding error — DISCLOSED, not a crash (it would kill the gate)
                continue
            meta, body = parse_frontmatter(text)
            if not meta:
                # no frontmatter at all (a README, notes…): Claude Code won't load it — counting
                # it would FABRICATE an ambient-authority unit the runtime doesn't have
                skipped.append(f)
                continue
            name = meta.get("name") or f[:-3]
            key = name
            if name in dup_names or name in agents:
                # Two+ agent files declaring the SAME `name:`. Silently overwriting (the old
                # `agents[name] = …`) dropped the earlier agent's grants/edges, so drift compared an
                # observed `name` against only the SURVIVOR — LAUNDERING a real violation (the dropped
                # agent's stricter contract vanished → clean exit, the cardinal silent miss) and able to
                # mis-flag the innocent one. Keep ALL of them, disambiguated by file so NEITHER holds the
                # bare `name`: an observed bare `name` then matches no declaration and routes to the
                # OBSERVED-OUTSIDE-DECLARATION anomaly (the safe flag), and the collision is surfaced.
                # (Distinct from the session/hooks guard, where one USER agent vs a SYNTHETIC unit keeps
                # the user's bare name; here two USER agents collide, so neither is authoritative — the
                # runtime can't disambiguate same-named agents either.)
                if name not in dup_names:
                    dup_names.add(name)
                    orig = agents.pop(name)  # the first file held the bare name — rename it too
                    agents[f"{name}#{orig['file']}"] = orig
                    print(f"candor-agents: duplicate agent name `{name}` — `{orig['file']}` is now unit "
                          f"`{name}#{orig['file']}`; rename one (the runtime can't tell them apart either)",
                          file=sys.stderr)
                key = f"{name}#{f}"
                print(f"candor-agents: duplicate agent name `{name}` — `{f}` is unit `{key}`", file=sys.stderr)
            agents[key] = {"tools": tool_list(meta), "body": body, "desc": str(meta.get("description", "")), "file": f}
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
    tool_hook_matchers = []  # matchers of TOOL-event hooks that run a command — the per-agent reach
    # `permissions.deny` (settings.json) is HARD-ENFORCED by the harness (precedence deny > ask >
    # allow), so a WHOLLY-denied tool/server is genuinely unreachable — candor SUBTRACTS it. This is
    # the one place the may-analysis tightens on sound data. A SCOPED deny (`Bash(curl:*)`, one mcp
    # tool `mcp__s__t`, a path glob) removes only a SUBSET of a tool's uses — the tool stays usable,
    # so it is recorded as informative but NOT subtracted (the Bash cliff: Exec survives one denied
    # command). `allow`/`ask` don't expand capability and are ignored (candor stays an upper bound).
    # Hooks BYPASS the permission system (the harness runs them directly), so a `deny Bash` never
    # strips the hooks unit's Exec.
    denied_tools, denied_servers, scoped_denies = set(), set(), set()
    for sf in ("settings.json", "settings.local.json"):
        sp = os.path.join(root, ".claude", sf)
        if not os.path.exists(sp):
            continue
        try:
            cfg = json.load(open(sp))
        except Exception as e:
            print(f"candor-agents: unreadable .claude/{sf} ({e}) — its hooks/permissions are UNKNOWN", file=sys.stderr)
            hook_why.add(f"hooks-unreadable:{sf}")
            continue
        # Valid JSON of the WRONG shape (a top-level list/scalar, or a non-dict permissions/hooks) must
        # degrade and DISCLOSE, never crash: a traceback here aborts the whole scan, no report is
        # written, and the gate then silently does not run. Mirror the .mcp.json / scheduled-tasks
        # readers' shape-tolerance — this was the one config reader that trusted the post-load shape.
        if not isinstance(cfg, dict):
            print(f"candor-agents: .claude/{sf} is not a JSON object (got {type(cfg).__name__}) — "
                  f"its hooks/permissions are UNKNOWN", file=sys.stderr)
            hook_why.add(f"hooks-malformed:{sf}")
            continue
        perms = cfg.get("permissions")
        deny_rules = perms.get("deny") if isinstance(perms, dict) else None
        for rule in (deny_rules if isinstance(deny_rules, list) else []):
            if not isinstance(rule, str):
                continue
            r = rule.strip()
            if r.startswith("mcp__"):
                parts = r.split("__")
                if len(parts) == 2 and parts[1]:
                    denied_servers.add(parts[1])  # `mcp__server` = the whole server
                else:
                    scoped_denies.add(r)          # `mcp__server__tool` = one tool, not the server
            elif "(" in r:
                scoped_denies.add(r)              # `Bash(curl:*)` / `Read(./secret/**)` — scoped
            elif r:
                denied_tools.add(r)               # a bare tool name = the whole tool
        hooks_cfg = cfg.get("hooks")
        if not isinstance(hooks_cfg, dict):
            hooks_cfg = {}
        for event in sorted(hooks_cfg):
            entries = hooks_cfg[event]
            if not isinstance(entries, list):
                continue
            n_cmds = 0
            for ent in entries:
                ent_cmds = 0
                for h in (ent.get("hooks") or []) if isinstance(ent, dict) else []:
                    if not isinstance(h, dict):
                        continue
                    if h.get("type") == "command" and isinstance(h.get("command"), str):
                        hook_cmds |= bash_cmds(h["command"])
                        n_cmds += 1
                        ent_cmds += 1
                    else:
                        hook_why.add(f"hook-type:{h.get('type', '?')}")
                # A TOOL-event hook with a command reaches back into whichever agent's tool use
                # matches `matcher` — record the matcher so those agents edge to the hooks unit.
                if ent_cmds and event in TOOL_HOOK_EVENTS and isinstance(ent, dict):
                    tool_hook_matchers.append(ent.get("matcher", "") if isinstance(ent.get("matcher"), str) else "")
            if n_cmds:
                hook_events.append(f"{event}({n_cmds})")
    has_hooks = bool(hook_events or hook_why)

    # Apply the harness-enforced denials: drop a wholly-denied tool/server from any grant list. A
    # specifier (`Bash(curl:*)`) is left in place — base tool stays usable, so its effect stays.
    def live(tools):
        """A grant list with the wholly-denied tools/servers removed (None stays ambient)."""
        if tools is None:
            return None
        out = []
        for t in tools:
            b = t.split("(", 1)[0].strip()
            if b in denied_tools:
                continue
            if b.startswith("mcp__"):
                seg = b.split("__")
                if len(seg) >= 2 and seg[1] in denied_servers:
                    continue
            out.append(t)
        return out
    ambient_live = [t for t in AMBIENT if t not in denied_tools]
    live_servers = [s for s in mcp_servers if s not in denied_servers]
    for a in agents.values():
        a["lt"] = live(a["tools"])  # deny-filtered grants, used for effects/edges/link

    # ── slash commands + skills: in-session capability/entry units ────────────────────────────────
    # `.claude/commands/**/*.md` (a `/command`) and `.claude/skills/*/SKILL.md` (a model-invoked
    # skill) each carry their OWN `allowed-tools` frontmatter and a command may run `!`-prefixed
    # shell — a capability surface distinct from the agents. The session root invokes them; they hold
    # tools (so a tool-event hook can fire on their use) but do not themselves delegate. Effects come
    # from `allowed-tools` (specifiers stripped to the base tool — `Bash(git:*)` is still Exec, the
    # cliff) plus, for a command, the heads of any `!` shell line. An ABSENT `allowed-tools` is PURE
    # (a prompt-only command), NOT ambient — the opposite of an agent's absent `tools:`.
    def _split_tools(s):
        """Comma-split a tool list, respecting parens so `Bash(a, b), Read` keeps the specifier whole."""
        out, buf, depth = [], "", 0
        for ch in s:
            if ch == "(":
                depth += 1; buf += ch
            elif ch == ")":
                depth = max(0, depth - 1); buf += ch
            elif ch == "," and depth == 0:
                out.append(buf); buf = ""
            else:
                buf += ch
        out.append(buf)
        # _unquote each item: a quoted `"Bash(git:*)"` specifier must keep its meaning, not become a
        # `"Bash` base that classifies as Unknown and slips an effect-specific deny gate (the parens/
        # colon/star in a Bash specifier are exactly what invites YAML quoting).
        return [u for x in out if (u := _unquote(x))]

    def _raw_tools(meta):
        """The raw `allowed-tools` items (specifiers intact), or [] if absent."""
        v = meta.get("allowed-tools")
        if v is None:
            return []
        if isinstance(v, list):
            return [u for x in v if (u := _unquote(str(x)))]
        s = _unquote(str(v).strip())  # a whole-value quote around an inline list / single specifier
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        return _split_tools(s)

    def _bash_spec_head(spec):
        """The command word a `Bash(...)` specifier scopes to (`git diff:*`→git, `*candor-run.sh*`
        →candor-run.sh) — a literal subprocess surface like a `!`-shell line. '' if not a plain word."""
        s = spec.split(":", 1)[0].strip().strip("*")  # drop the :args tail, surrounding globs
        s = (s.split() or [""])[0].rsplit("/", 1)[-1]  # the command word, basenamed
        return s if _CMD_NAME.match(s) else ""

    def _heads(raw, body):
        """Literal subprocess heads of a command: `!`-shell lines + `Bash(...)` allowed-tools specifiers."""
        heads = set()
        for line in body.splitlines():
            if line.lstrip().startswith("!"):
                heads |= bash_cmds(line.lstrip()[1:])
        for item in raw:
            if item.split("(", 1)[0].strip() == "Bash" and "(" in item and item.endswith(")"):
                h = _bash_spec_head(item[item.index("(") + 1:-1])
                if h:
                    heads.add(h)
        return heads

    commands, skills = {}, {}
    cdir = os.path.join(root, ".claude", "commands")
    if os.path.isdir(cdir):
        for dp, _ds, fs_ in os.walk(cdir):
            for f in sorted(fs_):
                if not f.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(dp, f), cdir)[:-3].replace(os.sep, ":")
                text = read_md(os.path.join(dp, f), unreadable)
                if text is None:
                    continue
                meta, body = parse_frontmatter(text)
                raw = _raw_tools(meta)
                commands[f"command:{rel}"] = {"tools": [t.split("(", 1)[0].strip() for t in raw],
                                              "file": os.path.relpath(os.path.join(dp, f), root),
                                              "heads": _heads(raw, body)}
    sdir = os.path.join(root, ".claude", "skills")
    if os.path.isdir(sdir):
        for d in sorted(os.listdir(sdir)):
            sm = os.path.join(sdir, d, "SKILL.md")
            if not os.path.isfile(sm):
                continue
            text = read_md(sm, unreadable)
            if text is None:
                continue
            meta, body = parse_frontmatter(text)
            raw = _raw_tools(meta)
            skills[f"skill:{d}"] = {"tools": [t.split("(", 1)[0].strip() for t in raw],
                                    "file": os.path.relpath(sm, root), "heads": _heads(raw, body)}

    # ── scheduled tasks: autonomous entry points ──────────────────────────────────────────────────
    # A DURABLE cron job (CronCreate `durable: true`) persists to .claude/scheduled_tasks.json and
    # fires on its own wall-clock schedule — no human, no caller. Each enqueues a prompt into a fresh
    # session, so its reach is the WHOLE session's capability (it can drive any agent/command/skill):
    # a `cron:<id>` entry-point unit that edges to the session root. Non-durable cron is in-memory only
    # (never on disk) and correctly invisible to a static scan — the report describes what's DECLARED
    # to persist. This is the fleet's autonomous-trigger surface: `deny Net cron:x` gates it.
    crons = {}  # unit name -> {cron, prompt}
    spath = os.path.join(root, ".claude", "scheduled_tasks.json")
    if os.path.exists(spath):
        try:
            sdata = json.load(open(spath))
        except Exception as e:
            print(f"candor-agents: unreadable .claude/scheduled_tasks.json ({e}) — scheduled tasks UNKNOWN", file=sys.stderr)
            sdata = None
        # Tolerate a top-level list OR an object wrapping the list under a conventional key.
        tasks = (sdata if isinstance(sdata, list)
                 else next((v for v in sdata.values() if isinstance(v, list)), []) if isinstance(sdata, dict)
                 else [])
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or t.get("name") or i)
            ckey = f"cron:{tid}"
            if ckey in crons:  # two scheduled tasks sharing an explicit id/name — keep both (the same
                ckey = f"cron:{tid}#{i}"  # silent-clobber class as duplicate agent names above)
            crons[ckey] = {"cron": str(t.get("cron", "")), "prompt": str(t.get("prompt", ""))}

    if not agents and not has_hooks and not commands and not skills and not crons:
        print(f"candor-agents: no agent definitions under {adir}, no commands/skills, no hooks, and no "
              f"scheduled tasks — nothing to analyze.", file=sys.stderr)
        return 2
    if denied_tools or denied_servers or scoped_denies:
        sub = sorted(denied_tools | {f"mcp:{s}" for s in denied_servers})
        msg = f"candor-agents: permissions.deny removed {{{', '.join(sub) or '—'}}} from the fleet surface"
        if scoped_denies:
            msg += f"; scoped (not subtracted — tool stays usable): {', '.join(sorted(scoped_denies))}"
        print(msg, file=sys.stderr)

    # ── edges (delegation) ────────────────────────────────────────────────────────────────────
    # Ladder mirrors the code engine: named-delegation narrowing (devirt) > CHA over all agents.
    names = sorted(agents)
    # Reserved synthetic-unit names. If a real agent claims one, DISAMBIGUATE the synthetic unit —
    # never clobber the user's declared agent (that silently dropped its grants/edges and made drift
    # mis-flag it). Warn so the rare collision is visible. The unitKind stays "session"/"hooks".
    ROOT, HOOKS = "session", "hooks"
    if ROOT in agents:
        ROOT = "session-root"
        print(f"candor-agents: an agent is named `session` — the session ROOT unit is `{ROOT}` "
              f"to avoid clobbering it", file=sys.stderr)
    if HOOKS in agents:
        HOOKS = "hooks-unit"
        print(f"candor-agents: an agent is named `hooks` — the settings-hooks unit is `{HOOKS}` "
              f"to avoid clobbering it", file=sys.stderr)
    calls = {}
    # Precompile ONE name-boundary regex per agent name (was a fresh compile per agent×name pair — a
    # quadratic-with-compile blow-up: 600 agents took 11s; precompiled, the compile cost is O(n) and
    # the per-pair search is cheap). The boundary is "not flanked by a name-CONTINUATION char". An
    # earlier explicit-delimiter class ([`'"\s.,]) MISSED a name followed by `:`, wrapped in `()` — a
    # real delegate silently DROPPED. But excluding `.`/`/` from the continuation set then FABRICATED
    # edges: a common-word agent name in a PATH or identifier (`src/build.rs`, `build.gradle`) matched
    # agent `build` → a false delegation edge → fabricated inherited effects (adversarial review find).
    # So `.` and `/` ARE name-continuation chars: a name flanked by them is a path/identifier fragment,
    # not a mention. Cost: a name at a SENTENCE END (`use helper.`) no longer matches — an UNDER-report
    # (the safe direction: it falls to CHA or is dropped, never fabricates a path edge). `:`/`(`/`)` etc.
    # still delimit, so the punctuation-adjacent delegates the earlier fix recovered still match.
    _NC = r"[A-Za-z0-9_./+-]"  # name-continuation chars (incl. . and / — path/identifier fragments aren't mentions)
    name_re = {n: re.compile(rf"(?<!{_NC}){re.escape(n)}(?!{_NC})") for n in names}
    # Bare-`Agent` agents narrowed by a PROMPT MENTION: the mention is advisory text, not proof of the
    # spawn set, so the narrowed reach is a lower bound — the residual must be disclosed (Unknown), not
    # silently dropped. Collected here, applied to `direct` after effects are classified below.
    unresolved_spawn = set()
    for name, a in agents.items():
        lt = a["lt"]  # deny-filtered: a denied `Agent`/`Task` grant can no longer delegate
        # base-strip the specifier: `Agent(worker)` (documented spawn-allowlist syntax) still grants the
        # Agent tool — a literal `"Agent" in lt` missed it and silently DISABLED delegation (review find).
        lt_bases = {base_tool(t) for t in (lt or [])}
        has_agent_tool = ("Agent" in lt_bases or "Task" in lt_bases
                          or (nested and a["tools"] is None and "Agent" not in denied_tools))
        edges = []
        if has_agent_tool:
            # Resolution ladder, mirroring the code engine's dyn dispatch:
            #  1. A declared `Agent(x,y)` spawn-allowlist is HARNESS-ENFORCED — the sound devirt analog.
            #     Narrow to it with NO residual (the runtime cannot spawn outside the allowlist).
            #  2. Bare `Agent` + a PROMPT MENTION: the mention narrows for precision but does NOT prove
            #     the agent won't spawn an unmentioned (possibly effectful) one — so keep the precise
            #     edges AND disclose the residual as Unknown (the code engine's unresolvable-dispatch
            #     posture). A narrowed-pure reading here was a silent under-report (adversarial find).
            #  3. Bare `Agent`, no mention: CHA over the whole fleet — a sound over-approximation with no
            #     residual beyond the fleet, so no Unknown.
            allowlist = agent_spawn_allowlist(lt)
            if allowlist:
                edges = [n for n in names if n != name and n in allowlist]
            else:
                hay = a["body"] + " " + a["desc"] + " "
                mentioned = [n for n in names if n != name and name_re[n].search(hay)]
                if mentioned:
                    edges = mentioned
                    unresolved_spawn.add(name)
                else:
                    edges = [n for n in names if n != name]  # CHA fallback (sound; no residual)
        calls[name] = sorted(edges)
    # Commands and skills are leaf units (they hold tools but don't spawn subagents) — init their
    # edge lists so the hook-matcher loop and the sidecar see them.
    for u in list(commands) + list(skills):
        calls[u] = []
    # The session root: an entry point holding every tool + every configured MCP server, able to
    # spawn every agent and invoke every command/skill. Named `session` (not `main`): in combined
    # mode (fleet + code reports under ONE prefix) the crate's `fn main` would collide with it.
    calls[ROOT] = sorted(names + list(commands) + list(skills) + ([HOOKS] if has_hooks else []))
    # A scheduled task drives a full session — it edges to the root and inherits the whole fleet's reach.
    for u in crons:
        calls[u] = [ROOT]
    if has_hooks:
        calls[HOOKS] = []
        # A TOOL-event hook (PreToolUse/PostToolUse) runs its command on the matching tool use of
        # ANY unit, not just the session root — so each agent/command/skill whose tools match a
        # hook's matcher EDGES to the hooks unit and inherits its Exec. Without this, `forbid
        # reviewer -> Exec` passed green while a PostToolUse hook exec'd on the reviewer's every edit.
        # Lifecycle hooks (Stop/SessionStart/…) stay session-only. Conservative: an unparseable
        # matcher edges no unit (under-report, never fabricate). Holders carry deny-filtered tools.
        holders = {n: a["lt"] for n, a in agents.items()}
        holders.update({n: live(c["tools"]) for n, c in commands.items()})
        holders.update({n: live(s["tools"]) for n, s in skills.items()})
        for name, tools in holders.items():
            if any(tools_match_matcher(tools, m) for m in tool_hook_matchers):
                calls[name] = sorted(set(calls[name]) | {HOOKS})

    # ── per-agent direct effects ──────────────────────────────────────────────────────────────
    # The MCP reach of an ambient unit (every CONFIGURED, non-denied server): one helper, used by
    # the ambient agents and the session root (was copy-pasted three ways).
    def mcp_effects(servers):
        e, w = set(), set()
        for s in servers:
            if s in MCP_TABLE:
                e |= MCP_TABLE[s]
            elif s in declared_mcp:
                e |= declared_mcp[s]
            elif s in declared_bad:
                e.add("Unknown"); w.add(f"mcp-decl-invalid:{s}:{declared_bad[s]}")
            else:
                e.add("Unknown"); w.add(f"mcp-uncurated:{s}")
        return e, w

    direct, fs_detail, why_map, unresolved_direct = {}, {}, {}, {}
    for name, a in agents.items():
        if a["tools"] is None:
            # Ambient authority: every built-in + every configured MCP server's tools (minus denials).
            effs, fs, why = classify(ambient_live, live_servers, declared_mcp, declared_bad)
            me_, mw_ = mcp_effects(live_servers)
            effs |= me_; why |= mw_
            why.add("ambient:tools-unrestricted")
            effs.add("Unknown")
        else:
            effs, fs, why = classify(a["lt"], live_servers, declared_mcp, declared_bad)
        direct[name], fs_detail[name], why_map[name] = effs, fs, why
        unresolved_direct[name] = "Unknown" in effs
    # Disclose the unprovable spawn residual on bare-`Agent` agents narrowed by a prompt mention (ladder
    # rung 2 above): they CAN spawn an unmentioned, possibly effectful agent at runtime, so a narrowed
    # reach that omits it would be a silent under-report — Unknown blocks a false `deny` certification
    # while the precise mentioned edges stay for map / blast-radius. (Allowlisted `Agent(x,y)` and the
    # CHA fallback are sound, so they're not flagged.)
    for name in unresolved_spawn:
        direct[name].add("Unknown")
        why_map[name].add("agent-spawn:bare `Agent` narrowed by prompt mention (can spawn an unmentioned agent)")
        unresolved_direct[name] = True
    # Commands / skills: effects from their (deny-filtered) allowed-tools; a command's shell heads add
    # Exec + the heads' REFINED effects (spec §4 ⟨0.5⟩: a known head classifies — `curl`→Net,
    # `candor*`→Fs/Env; an unknown head keeps the bare Exec cliff), unless Bash is denied (the shell
    # line could not run). Exec is never dropped — a subprocess was still spawned.
    def with_heads(unit, effs):  # commands AND skills refine the Exec cliff by their shell heads alike
        if unit["heads"] and "Bash" not in denied_tools:
            effs.add("Exec")
            for h in unit["heads"]:
                effs |= COMMAND_HEAD.get(h, set())
        return effs
    for u, c in commands.items():
        effs, fs, why = classify(live(c["tools"]), live_servers, declared_mcp, declared_bad)
        direct[u], fs_detail[u], why_map[u] = with_heads(c, effs), fs, why
        unresolved_direct[u] = "Unknown" in direct[u]
    for u, sk in skills.items():
        effs, fs, why = classify(live(sk["tools"]), live_servers, declared_mcp, declared_bad)
        direct[u], fs_detail[u], why_map[u] = with_heads(sk, effs), fs, why
        unresolved_direct[u] = "Unknown" in direct[u]
    me, mf, mw = classify(ambient_live, live_servers, declared_mcp, declared_bad)
    me_, mw_ = mcp_effects(live_servers)
    me |= me_; mw |= mw_
    direct[ROOT], fs_detail[ROOT], why_map[ROOT] = me, mf, mw
    unresolved_direct[ROOT] = "Unknown" in me
    if has_hooks:
        # The hook commands' heads refine the Exec the same way (a Stop hook running `curl` reaches Net).
        head_effs = set().union(*(COMMAND_HEAD.get(h, set()) for h in hook_cmds)) if hook_cmds else set()
        direct[HOOKS] = {"Exec"} | head_effs | ({"Unknown"} if hook_why else set())
        fs_detail[HOOKS], why_map[HOOKS] = set(), set(hook_why)
        unresolved_direct[HOOKS] = bool(hook_why)
    # A scheduled task has no DIRECT effect of its own — it just triggers; its reach is the session's,
    # inherited via the cron→root edge in the fixpoint below.
    for u in crons:
        direct[u], fs_detail[u], why_map[u], unresolved_direct[u] = set(), set(), set(), False

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
            runs_code = (a["tools"] is None and "Bash" not in denied_tools) or "Bash" in {base_tool(t) for t in (a["lt"] or [])}
            if runs_code:
                calls[name] = sorted(set(calls[name]) | set(linked))
        # A command/skill that shells out may run the project's own binaries — UNLESS its heads are all
        # known external tools (spec §4 transitive bound: e.g. one that only runs candor *over* the code
        # reads Fs, it doesn't perform the code's Net/Db). Bare-Bash or any unknown head keeps the link.
        for u, unit in {**commands, **skills}.items():
            if "Bash" in live(unit["tools"]) and not (unit["heads"] and all(h in COMMAND_HEAD for h in unit["heads"])):
                calls[u] = sorted(set(calls[u]) | set(linked))
        calls[ROOT] = sorted(set(calls[ROOT]) | set(linked))

    # ── transitive fixpoint (spec §5a) — one shared propagate(), used by observe.py too ─────────
    seed = {n: set(direct[n]) for n in calls}
    for fn_, effs_ in linked.items():
        seed.setdefault(fn_, set(effs_))
    inferred = propagate(seed, calls)
    fs_tr = propagate({n: set(fs_detail.get(n, set())) for n in calls}, calls)

    # ── emit the spec §2 envelope + §2.2 sidecar ─────────────────────────────────────────────
    functions = []
    for n in sorted(calls):
        if n in linked and n not in agents:
            continue  # pseudo-node: lives in the linked code report
        effs = inferred[n]
        if not effs:
            continue  # pure units are omitted from the report (present in the sidecar)
        # ONE discriminant for the unit's kind + location (was three scattered ternaries/conditions).
        # spec ⟨0.5⟩ unitKind: a fleet's units are not functions — informative, never semantic.
        if n in agents:
            kind, loc = "agent", agents[n]["file"]
        elif n in commands:
            kind, loc = "command", commands[n]["file"]
        elif n in skills:
            kind, loc = "skill", skills[n]["file"]
        elif n in crons:
            kind, loc = "cron", f".claude/scheduled_tasks.json: {crons[n]['cron'] or '(no schedule)'}"
        elif n == HOOKS:
            kind, loc = "hooks", f".claude/settings*.json hooks: {', '.join(hook_events) or '(unreadable)'}"
        else:
            kind, loc = "session", "(session root)"
        entry = {
            "fn": n,
            "loc": loc,
            "inferred": sorted(effs),
            "direct": sorted(direct.get(n, set())),
            "declared": [], "undeclared": sorted(effs - {"Unknown"}), "overdeclared": [],
            "unresolved": "Unknown" in effs,
            "unitKind": kind,
            "calls": calls[n],
        }
        if fs_tr.get(n):
            entry["fs"] = sorted(fs_tr[n])
        if why_map.get(n):
            entry["unknownWhy"] = sorted(why_map[n])
        if kind == "hooks" and hook_cmds:
            entry["cmds"] = sorted(hook_cmds)
        elif kind in ("command", "skill") and "Bash" not in denied_tools:
            heads = (commands if kind == "command" else skills)[n]["heads"]
            if heads:
                entry["cmds"] = sorted(heads)
        if kind in ("session", "cron"):
            entry["entryPoint"] = True  # autonomous roots: the session, and each scheduled task
        # spec §2 MUST: every producer emits the cross-boundary join key — a fleet report is
        # chainable like any sibling (`<fleet>#<agent>`, the pkg#LocalName shape).
        entry["hash"] = f"{fleet}#{n}"
        functions.append(entry)

    report = {"candor": {"version": VERSION, "toolchain": "claude-code", "spec": SPEC},
              "package": fleet,
              "functions": functions}
    callgraph = {n: calls[n] for n in sorted(calls)}
    rp = f"{out}.{fleet}.Fleet.json"
    cp = f"{out}.{fleet}.Fleet.callgraph.json"
    if as_json:
        # --json: stdout MUST be pure JSON (the §2 envelope) so it pipes cleanly — every human/progress
        # line goes to stderr (the sidecar need not go to stdout, per §3.3). No files are written.
        print(json.dumps(report, indent=1))
        print(f"candor-agents: {len(functions)} effectful unit(s) of {len(calls)} → stdout (--json)", file=sys.stderr)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(rp)), exist_ok=True)
        json.dump(report, open(rp, "w"), indent=1)
        json.dump(callgraph, open(cp, "w"), indent=1)
        # `--gate-json -` puts the VERDICT on stdout — the receipt then moves to stderr so stdout
        # stays one pure JSON document (the same rule --json already applies).
        print(f"candor-agents: {len(functions)} effectful unit(s) of {len(calls)} → {rp} (+ callgraph sidecar)",
              file=sys.stderr if gate_json == "-" else sys.stdout)
    if unreadable:
        print(f"candor-agents: {len(unreadable)} .md file(s) could not be read — NOT analyzed (permission/"
              f"encoding): {', '.join(unreadable[:5])}{'…' if len(unreadable) > 5 else ''}", file=sys.stderr)
    if has_hooks:
        print(f"candor-agents: hooks run AUTOMATICALLY on tool events — {', '.join(hook_events) or 'unreadable settings'}"
              f"{'; cmds: ' + ', '.join(sorted(hook_cmds)) if hook_cmds else ''}", file=sys.stderr)

    # ── the κ-coverage ledger (spec §7 item 14) — per-scan evidence, not a doc footnote ─────────
    pure_used = set()
    for a in agents.values():
        for t in (a["lt"] or []):
            if base_tool(t) in PURE_TOOLS:
                pure_used.add(base_tool(t))
    for unit in list(commands.values()) + list(skills.values()):
        for t in (unit["tools"] or []):  # already base-stripped at parse
            if t in PURE_TOOLS:
                pure_used.add(t)
    unit_heads = {u: c["heads"] for u, c in {**commands, **skills}.items()}
    if hook_cmds:
        unit_heads[HOOKS] = hook_cmds
    for line in kappa_ledger(why_map, unit_heads, pure_used):
        print(line, file=sys.stderr)

    # ── the standing §6.2 gate (--policy / $CANDOR_POLICY / config `policy`, spec §3.3) ───────────
    # A set-but-unreadable policy FAILS the run (exit 2) — never silently gate-passes (that includes
    # a set-but-EMPTY $CANDOR_POLICY / a bare config `policy` line: enabled-with-empty fails loud on
    # the open, never a silent skip); a violation exits 1. The gate runs IN-PROCESS over this report
    # (see policy.py for why not candor-query). --gate-json (spec §3.3 ⟨0.8⟩) re-emits the SAME
    # violation records as the machine verdict — written whenever the flag is given (ok:true, []
    # with no gate configured), and an unwritable verdict path exits 2, never a silent drop.
    from candor_agents import policy as _policy
    violations = []
    if policy_path is not None:
        try:
            ptext = open(policy_path, encoding="utf-8").read()
        except OSError as e:
            print(f"candor-agents: policy {policy_path} could not be read ({e}) — gate NOT enforced "
                  f"(exit 2)", file=sys.stderr)
            return 2
        violations = _policy.evaluate_policy(_policy.parse_policy(ptext), functions, callgraph)
        for v in violations:
            print(_policy.render(v), file=sys.stderr)  # keep stdout pure JSON in --json mode
    if gate_json is not None:
        if not _policy.write_gate_json(gate_json, violations, SPEC, stdout_is_json=as_json):
            return 2
    if policy_path is not None:
        if violations:
            print(f"candor-agents: {len(violations)} policy violation(s)", file=sys.stderr)
            return 1
        print("candor-agents: policy ✓", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
