#!/usr/bin/env python3
"""candor-agents guard — compile a fleet deny-policy into RUNTIME enforcement (roadmap #2: may→enforced).

A candor `deny <Effect>` gate is advisory: scan DETECTS a violation, but nothing stops it. `guard`
compiles the same policy into the settings.json `permissions.deny` that the HARNESS enforces natively
— so the boundary holds at runtime, not just in CI. It is the exact dual of scan: scan READS
permissions.deny to subtract effects from the surface; guard WRITES it to remove the capability.

Honest about the §4 Exec cliff: denying the tools that DIRECTLY produce an effect doesn't close a
granted `Bash` that can reach the same effect (a shell can curl). guard denies the direct tools and
WARNS about the cliff; `deny Exec` closes it. Per-agent scopes can't be expressed by the harness's
project-wide permissions.deny — guard says so and points at grant-tightening instead.

  candor-agents guard <policy-file> [<project-dir>]   # prints a settings.json permissions.deny fragment
"""
import json
import os
import sys

from scan import TOOL_EFFECTS, MCP_TABLE

VOCAB = {"Net", "Fs", "Db", "Exec", "Env", "Clock", "Ipc", "Log", "Rand", "Clipboard"}


def effect_tools():
    """Effect -> the built-in tools that DIRECTLY produce it (the inverse of TOOL_EFFECTS)."""
    inv = {}
    for tool, effs in TOOL_EFFECTS.items():
        for e in effs:
            inv.setdefault(e, set()).add(tool)
    return inv


def parse_denies(text):
    """The `deny <Effect...> [scope]` lines of a CANDOR_POLICY (§6.2). Other rules (allow/pure/forbid/
    layer) aren't runtime-enforceable this way and are ignored. Returns [(effects, scope_or_None)]."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.lower().startswith("deny "):
            continue
        toks = line.split()[1:]
        effs = [t for t in toks if t in VOCAB]
        rest = [t for t in toks if t not in VOCAB]
        if effs:
            out.append((effs, rest[0] if rest else None))
    return out


def compile_guard(policy_text, project_dir=None):
    """A fleet deny-policy -> {deny:[tool…], warnings:[…], notes:[…]}. `deny` is the permissions.deny
    the harness enforces; warnings flag the Exec cliff; notes flag scoped denies it can't enforce."""
    inv = effect_tools()
    mcp_eff = {}  # configured server -> its classified effect set (for mcp__server denies)
    if project_dir:
        mp = os.path.join(project_dir, ".mcp.json")
        if os.path.exists(mp):
            try:
                for name, cfg in (json.load(open(mp)).get("mcpServers") or {}).items():
                    decl = cfg.get("candorEffects") if isinstance(cfg, dict) else None
                    eff = set(decl) if isinstance(decl, list) else MCP_TABLE.get(name, set())
                    if eff:
                        mcp_eff[name] = eff
            except Exception:
                pass
    deny, warnings, notes = set(), [], []
    for effs, scope in parse_denies(policy_text):
        for e in effs:
            if scope:
                notes.append(f"deny {e} {scope}: per-agent runtime enforcement isn't expressible via the "
                             f"harness's project-wide permissions.deny — remove {sorted(inv.get(e, set()))} "
                             f"from {scope}'s `tools:` grants instead, or accept a fleet-wide `deny {e}`.")
                continue
            tools = inv.get(e, set())
            deny |= tools
            deny |= {f"mcp__{s}" for s, ee in mcp_eff.items() if e in ee}
            if e != "Exec" and "Bash" not in tools:
                warnings.append(f"deny {e}: a granted `Bash` can still reach {e} (the §4 Exec cliff) — this "
                                f"guard denies {sorted(tools)} but not Bash; add `deny Exec` to close it.")
    return {"deny": sorted(deny), "warnings": warnings, "notes": notes}


def main(args):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 0 if args else 2
    pol = args[0]
    proj = args[1] if len(args) > 1 and not args[1].startswith("--") else None
    try:
        text = open(pol).read()
    except Exception as e:
        print(f"candor-agents guard: cannot read policy {pol} ({e})", file=sys.stderr)
        return 2
    g = compile_guard(text, proj)
    for w in g["warnings"]:
        print(f"candor-agents guard: {w}", file=sys.stderr)
    for n in g["notes"]:
        print(f"candor-agents guard: {n}", file=sys.stderr)
    if not g["deny"]:
        print("candor-agents guard: no fleet-wide deny rule to enforce (scoped denies need grant-tightening).",
              file=sys.stderr)
        return 0
    # The settings.json fragment the harness enforces natively — merge into .claude/settings.json.
    print(json.dumps({"permissions": {"deny": g["deny"]}}, indent=2))
    print(f"candor-agents guard: ^ merge into .claude/settings.json — the harness then enforces this "
          f"deny boundary at runtime (the dual of scan reading permissions.deny).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
