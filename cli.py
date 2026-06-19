#!/usr/bin/env python3
"""candor-agents — effect analysis for agent fleets (candor-spec 0.7).

  candor-agents scan    <project-dir> [--out <prefix>]   # DECLARED: what the fleet MAY do
  candor-agents observe <project-dir> [--out <prefix>]   # OBSERVED: what it DID (transcripts)
  candor-agents drift   <project-dir> [--strict]         # declared vs observed (least-privilege advice)
  candor-agents guard   <policy-file> [<project-dir>]    # ENFORCED: compile a deny-policy to runtime

scan answers "what MAY this fleet do"; observe answers "what DID it do"; drift is the gap between
them: a grant no session ever used is a least-privilege trim candidate (the AS-EFF-002 analog), an
observed effect outside the declarations is an anomaly worth reading (a scan gap, an undeclared
agent type, or transcript noise — --strict exits 1 on it). guard closes the loop from may to
ENFORCED: it compiles a `deny <Effect>` policy into the settings.json permissions.deny the harness
enforces natively — the dual of scan, which READS permissions.deny to subtract effects. Reports are
candor-spec §2 envelopes, so the unmodified candor-query binary and the §6.2 policy grammar work over both modes.
"""
import json
import os
import subprocess
import sys
import tempfile

# Harness BUILT-IN agent types: they have no project `.md` declaration by design, so an undeclared
# observation of one is NOT drift. Everything else with no declaration that PERFORMED effects IS drift
# (a renamed/custom agent, or a `subagent` unit from a dropped meta sidecar) — see drift(). Conservative
# by intent: a builtin not listed here is flagged (a false alarm the operator resolves by declaring or
# extending this set) rather than silently passed — the gate fails loud, never open.
BUILTIN_AGENTS = {
    "general-purpose", "Explore", "Plan", "claude", "claude-code-guide",
    "statusline-setup", "output-style-setup",
}


def _run(mod, args):
    return subprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), mod)] + args)


def drift(target, strict, transcripts=None):
    fleet = os.path.basename(os.path.abspath(target)).lstrip("-") or "fleet"
    with tempfile.TemporaryDirectory() as td:
        rc = _run("scan.py", [target, "--out", os.path.join(td, "d"), "--fleet", fleet])
        if rc != 0:
            return rc
        # Pass the SAME --fleet to observe so both halves write `{d,o}.<fleet>.*.json` — without it
        # observe derived the name independently and renamed it for dirs basenamed dev/null/empty,
        # so drift's json.load below hit a FileNotFoundError on a project dir literally named `dev`.
        obs_args = [target, "--out", os.path.join(td, "o"), "--fleet", fleet]
        if transcripts:
            obs_args += ["--transcripts", transcripts]
        rc = _run("observe.py", obs_args)
        if rc != 0:
            return rc
        declared = json.load(open(os.path.join(td, f"d.{fleet}.Fleet.json")))
        observed = json.load(open(os.path.join(td, f"o.{fleet}.Observed.json")))
    # DIRECT sets on both sides: grants are per-agent facts, and transitive inheritance (an agent
    # that MAY delegate) would smear every delegator with its delegates' effects, muddying the
    # least-privilege advice. The transitive view stays available via candor-query over the reports.
    d = {e["fn"]: set(e["direct"]) for e in declared["functions"]}
    o = {e["fn"]: set(e["direct"]) for e in observed["functions"]}
    owhy = {e["fn"]: e.get("unknownWhy", []) for e in observed["functions"]}
    anomalies = 0
    print(f"drift — declared (what the fleet MAY do) vs observed (what it DID), fleet `{fleet}`:")
    for unit in sorted(set(d) | set(o)):
        dec, obs = d.get(unit), o.get(unit)
        if dec is None:
            # An observed unit with NO declaration. The harness BUILT-IN agent types legitimately have no
            # project `.md` — exempt them. But a NON-builtin undeclared unit that PERFORMED effects is the
            # gate's whole point: a renamed/custom agent with no declaration, or a dropped `meta.json`
            # sidecar that collapses the unit name to `subagent`, would otherwise launder Net/Exec past
            # `--strict` (it printed a soft note and `continue`d, never counting an anomaly). Flag it.
            if unit not in BUILTIN_AGENTS and obs:
                anomalies += 1
                print(f"  {unit}: OBSERVED-OUTSIDE-DECLARATION {{{', '.join(sorted(obs))}}} — an agent "
                      f"with NO declaration performed effects (a renamed/undeclared agent, or a dropped "
                      f"meta sidecar collapsing the unit to `subagent`); declare it or remove it")
            else:
                print(f"  {unit}: observed only ({', '.join(sorted(obs))}) — no declaration to compare "
                      f"(a built-in agent type)")
            continue
        if obs is None:
            if unit == "hooks":
                # hook executions are not tool_use events — the transcript reader cannot see them,
                # so "never observed" would be a structural artifact, not least-privilege advice
                print(f"  {unit}: declared {{{', '.join(sorted(dec))}}} — hook runs are not "
                      f"observable in transcripts; review the hook commands themselves")
            elif unit == "session" or unit.startswith(("command:", "skill:", "cron:")):
                # The session root and command/skill/cron units aren't recorded as DISTINCT units in
                # transcripts (their tool uses appear under the agent/session), so "never observed" is
                # a structural artifact, not least-privilege advice — and none of them is an "agent" to
                # trim. Only true agent units get the trim-candidate advice below.
                print(f"  {unit}: declared {{{', '.join(sorted(dec))}}} — not recorded as a distinct "
                      f"unit in transcripts (its activity appears under the session); not a trim candidate")
            else:
                print(f"  {unit}: declared {{{', '.join(sorted(dec))}}} but NEVER OBSERVED in these "
                      f"sessions — if that holds over time, the whole agent is a trim candidate")
            continue
        unused = dec - obs - {"Unknown"}
        extra = obs - dec - {"Unknown"}
        # An observed Unknown the agent NEVER DECLARED (Unknown not in dec) is a confined agent reaching
        # an uncurated, unclassifiable capability outside its declaration — the most security-relevant
        # drift. It must count as an anomaly (and trip --strict), not just print a soft note; stripping
        # Unknown from `extra` let it pass the gate clean. (A declared Unknown is acknowledged, so fine.)
        undeclared_unknown = "Unknown" in obs and "Unknown" not in dec and bool(owhy.get(unit))
        if unused:
            print(f"  {unit}: granted-but-unused {{{', '.join(sorted(unused))}}} — least-privilege "
                  f"trim candidates (AS-EFF-002's fleet analog)")
        if extra:
            anomalies += 1
            print(f"  {unit}: OBSERVED-OUTSIDE-DECLARATION {{{', '.join(sorted(extra))}}} — read this: "
                  f"a scan gap, a renamed grant, or the harness let something through")
        if undeclared_unknown:
            anomalies += 1
            print(f"  {unit}: OBSERVED-OUTSIDE-DECLARATION Unknown via {', '.join(owhy[unit])} — an "
                  f"uncurated tool the agent never declared ran; curate it or it is undeclared drift")
        elif "Unknown" in obs and owhy.get(unit):
            print(f"  {unit}: observed Unknown via {', '.join(owhy[unit])} — declared; an uncurated "
                  f"tool ran, curate it or read the transcript")
        if not unused and not extra and not undeclared_unknown:
            print(f"  {unit}: declarations match observation")
    if anomalies and strict:
        print(f"drift: {anomalies} anomaly unit(s) (--strict)", file=sys.stderr)
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    if cmd in ("--agents", "agents"):
        # The agent contract for THE INSTALLED VERSION, embedded as a module (the wheel ships
        # py-modules only) — doc and engine cannot drift (the §2.1 version-trust rule applied
        # to documentation).
        import agentsmd
        from scan import VERSION
        # Canonical header shape, consistent across the family: `candor-<engine> <version>`
        # (VERSION is "agents-<semver>"; the first `-` separates engine from version).
        print(f"<!-- candor-{VERSION.replace('-', ' ', 1)} · the agent contract for this installed version -->")
        sys.stdout.write(agentsmd.AGENTS_MD)
        return 0
    if cmd == "scan":
        return _run("scan.py", rest)
    if cmd == "observe":
        return _run("observe.py", rest)
    if cmd == "guard":
        # may -> ENFORCED: compile a fleet deny-policy into the settings.json permissions.deny the
        # harness enforces natively (the dual of scan, which READS permissions.deny to subtract).
        import guard
        return guard.main(rest)
    if cmd == "drift":
        # Parse flag/value pairs explicitly so an unknown flag FAILS (never silently runs non-strict
        # or against the wrong transcripts), a trailing value-less flag errors instead of IndexError,
        # and the positional target isn't dropped when it happens to equal a flag's value.
        strict, transcripts, target = False, None, None
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--strict":
                strict = True; i += 1
            elif a == "--transcripts":
                if i + 1 >= len(rest):
                    print("candor-agents: --transcripts requires a value", file=sys.stderr); return 2
                transcripts = rest[i + 1]; i += 2
            elif a.startswith("--"):
                print(f"candor-agents: unknown flag {a} "
                      f"(usage: drift <dir> [--transcripts <dir>] [--strict])", file=sys.stderr)
                return 2
            elif target is None:
                target = a; i += 1
            else:
                print(f"candor-agents: unexpected extra argument {a}", file=sys.stderr); return 2
        return drift(target or ".", strict, transcripts)
    # bare `candor-agents <dir>` = scan, the static default
    return _run("scan.py", args)


if __name__ == "__main__":
    sys.exit(main())
