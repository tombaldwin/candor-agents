#!/usr/bin/env python3
"""candor-agents — the agent-fleet effect engine. Declared grants vs observed use; drift is the alarm.

Reads a fleet's agent definitions (.claude/agents, .mcp.json, settings, commands,
skills, cron) for what it MAY do, and its session transcripts for what it DID —
the gap between the two is the least-privilege advice. Every report is the same
envelope the rest of the candor family emits, so the unmodified candor-query
binary and the shared policy grammar work over both modes.

USAGE
  candor-agents <action> [args] [options]     one action per invocation (below)
  candor-agents <dir>                         a bare dir is `scan <dir>`, the static default

ACTIONS
  scan <dir>                 DECLARED: what the fleet MAY do
  observe <dir>              OBSERVED: what it DID (session transcripts)
  drift <dir>                declared vs observed — trim candidates and anomalies (--strict exits 1)
  guard <policy> [<dir>]     ENFORCED: compile a deny-policy into the harness's permissions.deny
  stats [<dir>]              MEASURED: edit-time gate activity (the stop-hook log)
  digest [<dir>]             the OWNER report over the same log → CANDOR-REPORT.md
  log-gate <gate.json>       feed the digest from a jar --gate-json CI run
  savings [<dir>]            MODELLED: what candor-query saved vs re-deriving
  agents                     the agent contract for this installed version (AGENTS.md)

OPTIONS  (per action; `scan -h` / `observe -h` show the full surface)
  --out <prefix>             where to write the report (scan/observe) or digest (--out <path|->)
  --json                     machine-readable output (scan, observe, stats, savings)
  --policy <file>            evaluate a policy — exit 1 on a violation (scan, observe)
  --gate-json <file>         write the structured gate verdict as JSON (scan, observe)
  --fleet <name>             name the fleet (default: the project dir's basename)
  --transcripts <dir>        read transcripts from here (observe, drift)
  --strict                   drift: exit 1 on an anomaly
  --since <iso>              window the activity log (stats, digest)
  -V, --version              print the installed version (offline)
  -h, --help                 this help

EXAMPLES
  candor-agents scan .
  candor-agents drift . --strict
  candor-agents digest . --since 2026-07-01
  candor-agents guard fleet.policy .

Docs: candor.poly.io   ·   Verify an install: candor doctor
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


def _run(mod, args, gate_free=False):
    # Dispatch a sibling module as its own process via `-m candor_agents.<mod>` so the package
    # resolves wherever it's installed (pipx/venv/site-packages) — not by a fragile file path.
    # `gate_free` (drift's internal scan/observe): the child env is SCRUBBED of CANDOR_POLICY and
    # CANDOR_CONFIG and marked no-gate — drift COMPARES declared vs observed; with a standing policy
    # in the env (or a checked-in .candor/config the child would discover), the internal scan exited
    # 1 on any violation and drift ABORTED as a scan error. The user-facing gate surfaces are
    # scan/observe themselves; drift's verdict is --strict over its anomalies.
    env = None
    if gate_free:
        env = {k: v for k, v in os.environ.items() if k not in ("CANDOR_POLICY", "CANDOR_CONFIG")}
        env["_CANDOR_AGENTS_NO_GATE"] = "1"
    return subprocess.call([sys.executable, "-m", f"candor_agents.{mod}"] + args, env=env)


def drift(target, strict, transcripts=None):
    fleet = os.path.basename(os.path.abspath(target)).lstrip("-") or "fleet"
    with tempfile.TemporaryDirectory() as td:
        rc = _run("scan", [target, "--out", os.path.join(td, "d"), "--fleet", fleet], gate_free=True)
        if rc != 0:
            return rc
        # Pass the SAME --fleet to observe so both halves write `{d,o}.<fleet>.*.json` — without it
        # observe derived the name independently and renamed it for dirs basenamed dev/null/empty,
        # so drift's json.load below hit a FileNotFoundError on a project dir literally named `dev`.
        obs_args = [target, "--out", os.path.join(td, "o"), "--fleet", fleet]
        if transcripts:
            obs_args += ["--transcripts", transcripts]
        rc = _run("observe", obs_args, gate_free=True)
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
    # Top-level `-V`/`--version` (spec §3.3): the build + the candor-spec version it speaks, offline.
    # Routed before the help/dispatch so `candor-agents -V` (no subcommand) works like the family.
    if args and args[0] in ("-V", "--version"):
        from candor_agents.scan import SPEC, VERSION
        print(f"candor-agents {VERSION} (candor-spec {SPEC})")
        print("upgrade: pipx upgrade candor-agents  (or: pip install -U candor-agents)")
        return 0
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    if cmd in ("--agents", "agents"):
        # The agent contract for THE INSTALLED VERSION, embedded as a module (AGENTS.md itself
        # isn't shipped in the wheel) — doc and engine cannot drift (the §2.1 version-trust rule
        # applied to documentation).
        from candor_agents import agentsmd
        from candor_agents.scan import VERSION
        # Canonical header shape, consistent across the family: `candor-<engine> <version>`
        # (VERSION is "agents-<semver>"; the first `-` separates engine from version).
        print(f"<!-- candor-{VERSION.replace('-', ' ', 1)} · the agent contract for this installed version -->")
        sys.stdout.write(agentsmd.AGENTS_MD)
        return 0
    if cmd == "scan":
        return _run("scan", rest)
    if cmd == "observe":
        return _run("observe", rest)
    if cmd == "guard":
        # may -> ENFORCED: compile a fleet deny-policy into the settings.json permissions.deny the
        # harness enforces natively (the dual of scan, which READS permissions.deny to subtract).
        from candor_agents import guard
        return guard.main(rest)
    if cmd == "stats":
        # MEASURED edit-time gate activity from the stop hook's .candor/activity.jsonl (counted, not modelled).
        from candor_agents import stats
        return stats.main(rest)
    if cmd == "digest":
        # The OWNER-facing protection report over the same log (integrations/DIGEST-SPEC.md): a periodic,
        # aggregate, no-paths summary that makes the SILENT gate visible without adding dev-channel noise.
        from candor_agents import digest
        return digest.main(rest)
    if cmd == "log-gate":
        # Append one activity record from a jar `--gate-json` run so the PURE-JAR PR gate
        # (adopt/candor.yml) feeds the digest too — same record shape, path-free (no CI transcript).
        from candor_agents import log_gate
        return log_gate.main(rest)
    if cmd == "savings":
        # MODELLED: a labelled estimate of what candor-query saved vs re-deriving (transcript-based).
        from candor_agents import savings
        return savings.main(rest)
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
    return _run("scan", args)


if __name__ == "__main__":
    sys.exit(main())
