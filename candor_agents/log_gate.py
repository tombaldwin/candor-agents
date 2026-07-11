#!/usr/bin/env python3
"""candor-agents log-gate — turn a jar `--gate-json` run into one activity-log record.

  candor-agents log-gate <gate.json> [<report.json>] [--log <path>] [--engine <name>]

The Claude Code stop hook and the candor-review*.sh scripts already append a per-turn record to
the activity log (integrations/claude-code/lib-candor-summary.sh · candor_log_activity). The PURE-JAR
PR gate (adopt/candor.yml) doesn't run those — it invokes candor-java directly for `--json` +
`--gate-json`. This subcommand closes that gap: it reads the machine-readable gate VERDICT (and,
optionally, the effect report for the Unknown/effects fields) and appends the SAME record shape, so
`candor-agents digest` sees the jar-gated CI runs too ("held the line in CI" for the flagship's own
adopt workflow).

The record is PATH-FREE by construction — a CI gate has no transcript, so `edited` is null and no file
name is ever written. `gained` is the effects the AS-EFF-005 baseline ratchet reports as newly
introduced this change (empty when no baseline / no drift); `blastRadius`/`reviewMs` the jar gate
doesn't compute, so they're 0/null (never fabricated). The field shape is the CONTRACT the digest/stats
reader (stats._summary) consumes — kept in lockstep by test_log_gate.py's parity check against it.
"""
import argparse
import datetime
import json
import os
import sys


def _engine_from(report, gate):
    """Best-effort engine name (candor-java / candor-ts / …) from the report toolchain; 'jar' if absent."""
    tc = ((report or {}).get("candor") or {}).get("toolchain") or ""
    for name in ("candor-java", "candor-ts", "candor-swift", "candor-scan"):
        if name in tc:
            return name.split("candor-")[1]  # java / ts / swift / scan — matches the bash writer's tags
    return "jar"


def build_record(gate, report, engine=None, now=None):
    """Map a gate verdict (+ optional report) to the activity-record shape stats/digest read.
    `gate` is the `--gate-json` object {spec, ok, violations:[{rule,fn,effects,detail}]};
    `report` the spec-0.9 effect report (functions[].inferred), or None."""
    report = report or {}
    fns = report.get("functions") or []
    viols = gate.get("violations") or []

    # verdict: the gate says ok/not-ok; a record mirrors the review scripts' 0=clean / 1=blocked.
    verdict = "clean" if gate.get("ok") else "blocked"
    # violation CODES (AS-EFF-…), de-duped and sorted — the same set the bash writer greps out.
    codes = sorted({v.get("rule") for v in viols if v.get("rule")})
    # gained = effects the baseline ratchet (AS-EFF-005) reports as newly introduced this change.
    gained = sorted({e for v in viols if v.get("rule") == "AS-EFF-005" for e in (v.get("effects") or [])})
    # unknowns / effects PRESENT — counted straight from the report (null/[] when no report was given).
    if fns:
        unknowns = sum(1 for f in fns if "Unknown" in (f.get("inferred") or []))
        effects = sorted({e for f in fns for e in (f.get("inferred") or []) if e != "Unknown"})
    else:
        unknowns, effects = None, []

    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ts": ts,
        "sessionId": None,          # CI gate — no agent session
        "engine": engine or _engine_from(report, gate),
        "edited": None,             # no transcript in CI → path-free by construction
        "gained": gained,
        "blastRadius": 0,           # the jar gate doesn't compute transitive blast — 0, never fabricated
        "verdict": verdict,
        "violations": codes,
        "unknowns": unknowns,
        "effects": effects,
        "reviewMs": None,           # not measured on this path
    }


def _append_capped(record, log_path, cap):
    """Append the record as one JSON line; trim to the last `cap` lines (matches the bash writer's cap)."""
    d = os.path.dirname(log_path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    if cap and cap > 0:
        try:
            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > cap:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-cap:])
        except OSError:
            pass


def main(argv):
    ap = argparse.ArgumentParser(prog="candor-agents log-gate", add_help=True)
    ap.add_argument("gate", help="the --gate-json verdict file")
    ap.add_argument("report", nargs="?", help="the --json effect report (for the Unknown/effects fields)")
    ap.add_argument("--log", default=os.environ.get("CANDOR_ACTIVITY_LOG", ".candor/gate-log.jsonl"),
                    help="the activity log to append to (default .candor/gate-log.jsonl / $CANDOR_ACTIVITY_LOG)")
    ap.add_argument("--engine", help="override the engine tag (default: inferred from the report toolchain)")
    ns = ap.parse_args(argv)

    if ns.log == "off":
        return 0
    try:
        gate = json.load(open(ns.gate, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"candor-agents log-gate: can't read gate verdict {ns.gate}: {e}", file=sys.stderr)
        return 2
    if not isinstance(gate, dict):
        print(f"candor-agents log-gate: {ns.gate} is not a gate verdict object", file=sys.stderr)
        return 2
    report = None
    if ns.report:
        try:
            report = json.load(open(ns.report, encoding="utf-8"))
        except (OSError, ValueError):
            report = None   # the report is optional enrichment — a missing/bad one just drops those fields

    record = build_record(gate, report, engine=ns.engine)
    cap = os.environ.get("CANDOR_ACTIVITY_CAP", "5000")
    cap = int(cap) if cap.isdigit() else 5000
    _append_capped(record, ns.log, cap)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
