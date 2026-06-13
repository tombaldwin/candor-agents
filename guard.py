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
    warnings = []
    if project_dir:
        mp = os.path.join(project_dir, ".mcp.json")
        if os.path.exists(mp):
            try:
                for name, cfg in (json.load(open(mp)).get("mcpServers") or {}).items():
                    decl = cfg.get("candorEffects") if isinstance(cfg, dict) else None
                    if isinstance(decl, list):
                        # Validate like scan (SPEC §5.1): an invalid effect VOIDS the declaration loudly
                        # — otherwise a typo'd candorEffects (`["Database"]`) silently leaves the server
                        # un-denied (under-protect), inconsistent with scan, which voids it.
                        bad = [e for e in decl if e not in VOCAB]
                        if bad:
                            warnings.append(f"mcp server {name}: candorEffects has an invalid effect '{bad[0]}' — "
                                            f"declaration voided (SPEC §1); the server can't be matched by a deny rule until fixed.")
                            continue
                        eff = set(decl)
                    else:
                        eff = MCP_TABLE.get(name, set())
                    if eff:
                        mcp_eff[name] = eff
            except Exception:
                pass
    deny, notes, cliff = set(), [], set()
    for effs, scope in parse_denies(policy_text):
        for e in effs:
            if scope:
                notes.append(f"deny {e} {scope}: per-agent runtime enforcement isn't expressible via the "
                             f"harness's project-wide permissions.deny — remove {sorted(inv.get(e, set()))} "
                             f"from {scope}'s `tools:` grants instead, or accept a fleet-wide `deny {e}`.")
                continue
            deny |= inv.get(e, set())
            deny |= {f"mcp__{s}" for s, ee in mcp_eff.items() if e in ee}
            if e != "Exec":
                cliff.add(e)
    # The §4 Exec-cliff warning fires per non-Exec effect ONLY when Bash itself isn't denied — if the
    # policy ALSO denies Exec, Bash is in the deny set and the cliff is closed, so no warning (the
    # real-world bug: a `deny Net` + `deny Exec` policy was still told to add `deny Exec`).
    if "Bash" not in deny:
        for e in sorted(cliff):
            tools = sorted(inv.get(e, set()))
            if tools:
                warnings.append(f"deny {e}: a granted `Bash` can still reach {e} (the §4 Exec cliff) — this "
                                f"guard denies {tools} but not Bash; add `deny Exec` to close it.")
            else:
                # No built-in tool PRODUCES this effect (e.g. Db) — it's reachable only via Bash (a
                # shell client) or an MCP server, so permissions.deny on a tool can't enforce it.
                warnings.append(f"deny {e}: no built-in tool produces {e} — it's reached via `Bash` (a shell "
                                f"client) or an MCP server; add `deny Exec` and/or deny the relevant mcp__server "
                                f"to enforce it.")
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
