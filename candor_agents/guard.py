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

guard EMITS the permissions.deny fragment (to stdout) for you to merge into .claude/settings.json —
it never writes the file itself (no risk of clobbering an existing settings.json). It is the
read/write dual of scan in INTENT: scan READS permissions.deny to subtract effects from the surface;
guard produces the permissions.deny that removes the capability.

  candor-agents guard <policy-file> [<project-dir>]   # prints a settings.json permissions.deny fragment
"""
import json
import os
import sys

from candor_agents.policy import EFFECTS
from candor_agents.scan import TOOL_EFFECTS, MCP_TABLE

# SPEC §1's effect table (⟨0.24⟩ phrasing: "every effect in the table above, excluding `Unknown`") —
# taken from the shared list rather than re-typed, which is how `Llm` came to be missing from all
# THREE of this engine's copies of it at once. guard is the dual of the gate, so an effect the gate
# now enforces must at least be RECOGNISED here (and reported as having no denyable tool), never
# silently mis-read as the rule's scope token.
VOCAB = set(EFFECTS)
VOCAB_LOWER = {e.lower(): e for e in VOCAB}  # case-fold map, to catch a miscased `deny net`


def effect_tools():
    """Effect -> the built-in tools that DIRECTLY produce it (the inverse of TOOL_EFFECTS)."""
    inv = {}
    for tool, effs in TOOL_EFFECTS.items():
        for e in effs:
            inv.setdefault(e, set()).add(tool)
    return inv


def parse_denies(text):
    """The `deny <Effect...> [scope]` lines of a CANDOR_POLICY (§6.2). Other rules (allow/pure/forbid/
    layer) aren't runtime-enforceable this way and are ignored. Returns [(effects, scope_or_None)].

    Parsed POSITIONALLY to mirror the canonical engine parser (candor-classify policy.rs): leading
    effect tokens are collected, and the FIRST non-effect token is the scope and ENDS the rule — a
    later effect-looking token (`deny Net foo Db`) is NOT collected. A set-membership partition would
    diverge here (it would treat the trailing `Db` as a scoped deny the engine never gates), so guard
    would no longer be the faithful dual of the engine's §6.2 enforcement."""
    out, suspects = [], []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line.lower().startswith("deny "):
            continue
        effs, scope = [], None
        for t in line.split()[1:]:
            if t in VOCAB:
                effs.append(t)
            else:
                scope = t  # first non-effect token is the scope and ends the rule
                # Effect tokens are case-SENSITIVE but `deny ` isn't: a miscased `deny net` would
                # otherwise read `net` as a scope and enforce nothing, silently. Flag the look-alike.
                if t.lower() in VOCAB_LOWER:
                    suspects.append((t, VOCAB_LOWER[t.lower()]))
                break
        if effs:
            out.append((effs, scope))
    return out, suspects


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
            except Exception as e:
                # An unreadable/malformed .mcp.json means the server denies CANNOT be compiled — staying
                # silent would emit an under-protective permissions.deny with no disclosure (a deny rule
                # against an mcp server would simply be missing). Warn, mirroring scan.py's reader.
                warnings.append(f".mcp.json unreadable ({e}) — mcp server denies omitted; verify manually")
    rules, suspects = parse_denies(policy_text)
    for tok, proper in suspects:
        warnings.append(f"`deny {tok}`: effects are case-sensitive — `{tok}` was read as a scope name, not "
                        f"the effect `{proper}`; use `deny {proper}` to enforce it fleet-wide.")
    deny, notes, cliff = set(), [], set()
    for effs, scope in rules:
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
    bash_denied = "Bash" in deny
    for e in sorted(cliff):
        tools = sorted(inv.get(e, set()))
        if tools:
            # A direct producer was denied; the residual reach is a granted `Bash` (the §4 cliff),
            # which is closed iff Bash is itself denied (the `deny Net` + `deny Exec` case).
            if not bash_denied:
                warnings.append(f"deny {e}: a granted `Bash` can still reach {e} (the §4 Exec cliff) — this "
                                f"guard denies {tools} but not Bash; add `deny Exec` to close it.")
        else:
            # No built-in tool PRODUCES this effect (e.g. Db) — it's reachable only via `Bash` (a shell
            # client) or an MCP server. Disclose the residual paths this guard can't bind INDEPENDENT of
            # whether Bash is denied (a closed shell still leaves an uncurated/future MCP server), and
            # don't instruct denying an mcp server already added to `deny` above.
            residual = []
            if not bash_denied:
                residual.append("a granted `Bash` (add `deny Exec` to close it)")
            denied_mcp = {s for s, ee in mcp_eff.items() if e in ee}
            residual.append("an uncurated/unmodeled MCP server reaching it (deny its `mcp__server`)"
                            if denied_mcp else "an MCP server reaching it (deny its `mcp__server`)")
            warnings.append(f"deny {e}: no built-in tool produces {e}; this guard binds it only insofar as "
                            f"you also close {' and '.join(residual)}.")
    return {"deny": sorted(deny), "warnings": warnings, "notes": notes}


def main(args):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 0 if args else 2
    # guard takes NO flags and at most two positionals. An unknown flag or an extra argument FAILS
    # (exit 2) — it was silently dropped before (the gateless-ignore class the family forbids:
    # `guard pol --bogus` emitted the fragment as if nothing was wrong), diverging from every other
    # subcommand's arg contract.
    usage = "usage: guard <policy-file> [<project-dir>]"
    pol, proj = args[0], None
    for a in args[1:]:
        if a.startswith("--"):
            print(f"candor-agents guard: unknown flag {a} ({usage})", file=sys.stderr)
            return 2
        if proj is not None:
            print(f"candor-agents guard: unexpected extra argument {a} ({usage})", file=sys.stderr)
            return 2
        proj = a
    try:
        text = open(pol, encoding="utf-8").read()
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
