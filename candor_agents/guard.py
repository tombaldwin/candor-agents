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

from candor_agents import policy as _policy
from candor_agents.policy import EFFECTS
from candor_agents.scan import TOOL_EFFECTS, MCP_TABLE, read_mcp

# SPEC §1's effect table (⟨0.24⟩ phrasing: "every effect in the table above, excluding `Unknown`") —
# taken from the shared list rather than re-typed, which is how `Llm` came to be missing from all
# THREE of this engine's copies of it at once. guard is the dual of the gate, so an effect the gate
# now enforces must at least be RECOGNISED here (and reported as having no denyable tool), never
# silently mis-read as the rule's scope token.
VOCAB = set(EFFECTS)
VOCAB_LOWER = {e.lower(): e for e in VOCAB}  # case-fold map, to catch a miscased `deny net`
# `Unknown` shares the same miscasing hazard as the 11 named effects but is deliberately absent from
# VOCAB itself (policy.py's own comment: "minus the `Unknown` visibility marker"), so it needs its own
# entry in the SAME map rather than a second one — `deny UNKNOWN`/`deny unknown` must warn exactly like
# `deny net` does, not silently vanish into "scope" with no signal at all (see _case_suspect). Effect
# tokens stay case-SENSITIVE: policy.parse_policy does not fold them, and silently upgrading a miscased
# token here would make guard's runtime enforcement answer a DIFFERENT question than the gate asks of
# the identical policy text — the divergence guard exists not to have. It only earns the warning.
VOCAB_LOWER["unknown"] = "Unknown"


def effect_tools():
    """Effect -> the built-in tools that DIRECTLY produce it (the inverse of TOOL_EFFECTS)."""
    inv = {}
    for tool, effs in TOOL_EFFECTS.items():
        for e in effs:
            inv.setdefault(e, set()).add(tool)
    return inv


def _case_suspect(tok):
    """A scope token that case-folds onto an effect name — `deny net`, `deny Net UNKNOWN`. Returns the
    properly-cased effect, or None. Effect tokens are case-SENSITIVE in the shared grammar, so such a
    token really IS read as an agent scope; the operator has to be told, not left to think they wrote a
    rule for a real agent. Strips an optional `[…]` suffix so `UNKNOWN[dispatch]` is caught too."""
    base = tok[:tok.index("[")] if "[" in tok and tok.endswith("]") else tok
    return VOCAB_LOWER.get(base.lower())


def parse_denies(text):
    """The `deny <Effect...> [scope]` rules of a CANDOR_POLICY (§6.2), for runtime compilation.
    Returns ([(effects, scope_or_None)], suspects, widened, fatal_errors).

    THE PARSE IS `policy.parse_policy`'s, NOT A SECOND ONE. This function used to re-tokenise the
    policy text itself, on the reasoning that it was a faithful positional mirror of the canonical
    parser — and it was, on the token partition. It was not on anything else, and every divergence
    fell out the moment the two were run over the same string:

      `deny\\tNet`               policy ENFORCES it; guard's `startswith("deny ")` wanted a literal
                                SPACE, so the line was skipped with NO warning and a single-rule
                                policy compiled to an EMPTY (fully permissive) fragment — the exact
                                shape of the BOM bug one whitespace character over, and the same
                                silent, under-protective direction.
      `Deny Net` / `DENY Net`   guard compiled a fleet-wide Net deny and printed "the harness then
                                enforces this"; policy dropped the line as an unknown rule kind and
                                the gate exited 0 having enforced nothing. Two paths, one policy,
                                opposite answers.
      `deny Net<NBSP>agent`     policy splits on ASCII whitespace ONLY (deliberately — the
                                cross-engine rule) so the rule reads malformed and is REFUSED at
                                exit 2; `str.split()` here is Unicode-wide and read a scoped deny.
      `deny Net\\x0bdeny Fs`     `str.splitlines()` splits on \\v/\\f/\\x85/\\u2028 too; policy's line
                                split does not.
      a FATAL policy            `only …`, `deny Frobnicate`, `deny Unknown[nativ]` — policy REFUSES
                                the whole file (exit 2, §6.2: a policy that cannot be honoured AS
                                WRITTEN is refused, never rewritten). guard compiled the remainder
                                and exited 0, which is that rewrite, in the enforcement layer.

    So: one parser. `suspects` (the case-fold lint) and `widened` (the ⟨0.20⟩ destination-class filter
    guard cannot honour) are read off what THAT parser recorded — the scope token it chose, and the
    tokens it dropped — never re-derived from the raw line, which is how the second parser got in.
    `fatal` is `policy.LAST_POLICY_ERRORS`' fatal entries, so guard can refuse exactly where the gate
    does instead of enforcing a policy the gate will not."""
    parsed = _policy.parse_policy(text)
    errors = list(_policy.LAST_POLICY_ERRORS)
    out, suspects, widened = [], [], []
    for r in parsed["deny"]:
        if not r["effects"]:
            continue  # `pure <scope>` — a purity assertion, not a runtime-denyable capability
        out.append((list(r["effects"]), r["scope"] or None))
        widened.extend(r.get("widened") or [])
        if r["scope"] and (proper := _case_suspect(r["scope"])):
            suspects.append((r["scope"], proper))
    for e in errors:
        # A rule DROPPED for naming no effect never reaches `parsed`, so its scope token — the whole
        # content of `deny net` — is only visible here. policy records it for exactly this reader.
        if e.get("why") == "deny names no known effect" and e.get("scope"):
            if proper := _case_suspect(e["scope"]):
                suspects.append((e["scope"], proper))
    return out, suspects, widened, [e for e in errors if e.get("fatal")]


def server_effects(project_dir):
    """Configured MCP server -> the effect set SCAN would classify it as, plus a warning per voided
    declaration. `read_mcp` is the authority — this used to be a second, hand-rolled `.mcp.json`
    reader, and it disagreed with scan three ways, all of them under-protective:

      PRECEDENCE   scan reads the curated MCP_TABLE FIRST and a `candorEffects` declaration only as a
                   fallback ("curated table outranks" — .mcp.json is project-controlled, so a project
                   cannot narrow candor's own claim about a conventionally-named server). Here the
                   declaration was read first, so a server named `github` declaring `["Fs"]` was
                   omitted from a compiled `deny Net` that scan's gate fires on.
      Llm ⇒ Net    `read_mcp` runs `refine_llm` (SPEC §6.1 ⟨0.24⟩: a model-provider call is an
                   outbound request in every instance, so the engines co-emit `Net`). Without it a
                   server declaring `["Llm"]` was invisible to `deny Net` — again, one the gate fires on.
      SHAPE        a non-list `candorEffects` ("Net", a string) fell to `else: MCP_TABLE.get(...)`
                   and the server was silently un-denied with no warning at all, while scan VOIDS it
                   loudly as `mcp-decl-invalid`. The list-valued typo was already handled here; the
                   wrong-TYPE one was the branch nobody wrote.

    So the classification lives in one place and the trust ladder is scan's, verbatim."""
    warnings = []
    if not project_dir:
        return {}, warnings
    if not os.path.exists(os.path.join(project_dir, ".mcp.json")):
        return {}, warnings
    # read_mcp already prints its own `unreadable .mcp.json` line to stderr; mirror it as a guard
    # warning too, since a server deny that cannot be compiled is an under-protective fragment.
    servers, declared_mcp, declared_bad = read_mcp(project_dir)
    mcp_eff = {}
    for name in servers:
        if name in MCP_TABLE:
            eff = MCP_TABLE[name]
        elif name in declared_mcp:
            eff = declared_mcp[name]
        elif name in declared_bad:
            warnings.append(f"mcp server {name}: candorEffects has an invalid effect "
                            f"'{declared_bad[name]}' — declaration voided (SPEC §1); the server can't "
                            f"be matched by a deny rule until fixed.")
            continue
        else:
            continue  # uncurated + undeclared: reads Unknown in scan, and Unknown binds no tool here
        if eff:
            mcp_eff[name] = set(eff)
    return mcp_eff, warnings


def compile_guard(policy_text, project_dir=None):
    """A fleet deny-policy -> {deny:[tool…], warnings:[…], notes:[…], fatal:[…]}. `deny` is the
    permissions.deny the harness enforces; warnings flag the Exec cliff; notes flag scoped denies it
    can't enforce; `fatal` is the §6.2 policy errors that make the gate REFUSE (see main)."""
    inv = effect_tools()
    mcp_eff, warnings = server_effects(project_dir)
    if project_dir and os.path.exists(os.path.join(project_dir, ".mcp.json")) and not mcp_eff:
        # read_mcp degrades-and-discloses on an unreadable/malformed file (it prints to stderr and
        # returns nothing), so an empty result over a file that EXISTS may mean "couldn't read it".
        # Only add the caveat when the file genuinely yielded no server, never in place of read_mcp's
        # own diagnostic — a duplicated reader is what this function stopped having.
        try:
            with open(os.path.join(project_dir, ".mcp.json"), encoding="utf-8") as fh:
                json.load(fh)
        except Exception as e:
            warnings.append(f".mcp.json unreadable ({e}) — mcp server denies omitted; verify manually")
    rules, suspects, widened, fatal = parse_denies(policy_text)
    for tok, proper in suspects:
        warnings.append(f"`deny {tok}`: effects are case-sensitive — `{tok}` was read as a scope name, not "
                        f"the effect `{proper}`; use `deny {proper}` to enforce it fleet-wide.")
    for tok, eff in widened:
        warnings.append(f"`deny {tok}`: a destination-class filter isn't recognised at this layer — "
                        f"enforced as the unscoped `deny {eff}` (widened, never narrowed).")
    deny, notes, cliff = set(), [], set()
    for effs, scope in rules:
        for e in effs:
            if e == "Unknown":
                # `Unknown` names an UNRESOLVED capability (an uncurated MCP server, an unlisted tool),
                # never a concrete tool grant — there is no permissions.deny entry that removes "the
                # ability to reach something the classifier couldn't resolve". Disclose that plainly
                # rather than silently dropping the rule (the guard-as-enforcer analog of the scan
                # classifier's own under-report rule).
                target = f"deny Unknown {scope}" if scope else "deny Unknown"
                dest = notes if scope else warnings
                dest.append(f"{target}: guard cannot bind `Unknown` at runtime — it marks an "
                            f"unresolved capability, not a concrete tool grant permissions.deny can "
                            f"remove. Curate the MCP server (DECLARING.md) or read the flagged unit's "
                            f"`unknownWhy` in the scan/observe report and tighten its `tools:` grants "
                            f"instead.")
                continue
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
    return {"deny": sorted(deny), "warnings": warnings, "notes": notes, "fatal": fatal}


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
    if g["fatal"]:
        # §6.2: A POLICY THAT CANNOT BE HONOURED AS WRITTEN IS REFUSED, NOT REWRITTEN. `policy.run_gate`
        # has exited 2 on this set since ⟨0.24⟩; guard did not, because it never asked the parser — it
        # compiled the surviving remainder and printed "the harness then enforces this deny boundary",
        # so `only reviewer -> Exec` beside a `deny Net` produced a confident fragment for a policy the
        # CI gate refuses outright. Emitting enforcement for a REWRITTEN policy is that rewrite reaching
        # runtime. Same posture, same exit code, same wording as the gate.
        print("candor-agents guard: refusing to compile a policy that cannot be honoured AS WRITTEN "
              "(exit 2, nothing emitted): "
              + "; ".join(f"{e['why']}: {e['raw']}" for e in g["fatal"]), file=sys.stderr)
        print("candor-agents guard: `candor-agents scan --policy` refuses this same policy at exit 2 — "
              "fix it there first; a fragment compiled from the remainder would enforce a boundary "
              "your gate is not checking.", file=sys.stderr)
        return 2
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
