"""candor-agents digest — the owner-facing protection report.

  candor-agents digest [<project-dir>] [--log <path>] [--since <iso>] [--out <path|->] [--title <s>]

The visibility surface for the SILENT gate (integrations/DIGEST-SPEC.md): a periodic, plain-language
summary aimed at whoever owns the renewal, NOT the developer in the loop. It reads the same
`.candor/activity.jsonl` the stop hook already writes and `stats` already parses (single-sourced on
stats._load/_summary — no reparsing), and renders the spec's narrative: lead with the catches, close
with the silence reframed as coverage, and ALWAYS carry the honesty line (what candor could not fully
resolve). Aggregate counts only — never file paths, never a per-developer leaderboard.

Writes a committable `CANDOR-REPORT.md` by default (aggregate, no paths — safe to commit, unlike the
gitignored raw log); `--out -` prints to stdout. Exit 0 on a rendered report (including a quiet
period), 2 on a usage error, and a clean note (exit 0) when no activity has been logged yet.
"""
import os
import sys

from candor_agents.stats import _load, _summary

# AS-EFF code → plain, ACCURATE description (SPEC §6). No invented specifics (the digest doesn't know a
# repo's layer names) — the code plus what it means, so the number is legible without over-claiming.
_CODE = {
    "AS-EFF-001": "performed an effect it didn't declare",
    "AS-EFF-002": "declared a capability it never used",
    "AS-EFF-003": "made unresolved calls — couldn't be certified",
    "AS-EFF-004": "reached for ambient authority directly",
    "AS-EFF-005": "gained an effect versus the baseline",
    "AS-EFF-006": "performed an effect its policy forbids",
    "AS-EFF-007": "an injection-class effect on caller-derived input (advisory)",
    "AS-EFF-008": "reached a value outside the declared allowlist",
    "AS-EFF-009": "a layer depended on a layer it may not",
    "AS-EFF-010": "a boundary effect leaked into a new layer",
}


def _period(summary, title):
    if title:
        return title
    span = summary.get("span")
    if not span:
        return "all time"
    lo, hi = span[0][:10], span[1][:10]
    return lo if lo == hi else f"{lo} → {hi}"


def _allowed_intros(recs):
    """Effect-introductions that were NOT blocked — what got THROUGH the gate (distinct from the
    caught ones in 'Held the line'). The owner's useful split: stopped vs allowed."""
    effs, turns = set(), 0
    for r in recs:
        gained = r.get("gained") or []
        if gained and r.get("verdict") == "clean":
            turns += 1
            effs.update(gained)
    return turns, sorted(effs)


def render(summary, project, title=None, allowed=(0, [])):
    """The owner-facing report, as Markdown (reads fine as plain text too)."""
    s = summary
    L = []
    L.append(f"# candor — protection report · {project} · {_period(s, title)}")
    L.append("")
    turns = s["turns"]
    sess = s["sessions"]
    L.append(f"**Checked** {turns} change{'' if turns == 1 else 's'}"
             + (f" across {sess} session{'' if sess == 1 else 's'}." if sess else "."))

    # Held the line — the catches, led with (the value).
    blocked = s["blocked"]
    if blocked:
        L.append(f"**Held the line** — {blocked} change{'' if blocked == 1 else 's'} caught before merge:")
        for code, n in sorted(s["violations"].items()):
            desc = _CODE.get(code, "violated a declared policy rule")
            L.append(f"  - {n}× {code} — {desc}")
    else:
        L.append("**Held the line** — no policy violation reached a merge this period.")

    # New capability that was ALLOWED THROUGH (clean turns that gained an effect) — the "what your code
    # newly does" line, distinct from the caught ones above. Blocked introductions are not repeated here.
    allowed_turns, allowed_effs = allowed
    if allowed_turns:
        L.append(f"**New capability allowed** — {allowed_turns} change{'' if allowed_turns == 1 else 's'} "
                 f"introduced a new effect ({', '.join(allowed_effs)}) and passed the gate; worth knowing "
                 f"your code now does this.")

    # Largest blast radius — the "candor showed it up front" line.
    blast = s["largestBlastRadius"]
    if blast:
        L.append(f"**Largest blast radius** — one change would have rippled to {blast} function"
                 f"{'' if blast == 1 else 's'}; candor showed the full reach up front.")

    # Coverage / honesty — the disclosure ethos, on candor itself. Present whenever the log carries the
    # field, in BOTH directions: what it couldn't see (the caveat) OR "it saw everything" (the good news).
    if s.get("hasUnknowns"):
        u = s["unknownsMax"]
        if u > 0:
            L.append(f"**Coverage** — candor disclosed code it could not fully resolve (up to {u} "
                     f"construct{'' if u == 1 else 's'} in a change: dynamic dispatch / reflection), "
                     f"flagged for a human look — never passed off as clean.")
        else:
            L.append("**Coverage** — every change resolved to the leaf; nothing was left unexplained.")

    # Quiet is good — close by reframing the silence as coverage.
    clean = s["clean"]
    if turns:
        L.append(f"**Quiet is good** — {clean} of {turns} change{'' if turns == 1 else 's'} crossed no "
                 f"boundary. That silence is the gate working, not idling.")

    if s.get("hasReviewMs"):
        L.append("")
        L.append(f"_candor's own time this period: {s['candorMs'] / 1000:.1f}s across {turns} checks._")
    L.append("")
    L.append("_Aggregate counts only — no file paths, no per-author breakdown. "
             "Generated by `candor-agents digest`._")
    return "\n".join(L) + "\n"


def main(argv):
    target, log, since, out, title = ".", None, None, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--log", "--since", "--out", "--title"):
            if i + 1 >= len(argv):
                print(f"candor-agents digest: {a} needs a value", file=sys.stderr)
                return 2
            val = argv[i + 1]
            if a == "--log":
                log = val
            elif a == "--since":
                since = val
            elif a == "--out":
                out = val
            else:
                title = val
            i += 2
        elif a.startswith("-") and a not in ("-",):
            print(f"candor-agents digest: unknown flag {a}", file=sys.stderr)
            return 2
        elif target == ".":
            target = a
            i += 1
        else:
            print("candor-agents digest: unexpected extra argument", file=sys.stderr)
            return 2

    path = log or os.path.join(target, ".candor", "activity.jsonl")
    recs = _load(path, None, since)
    project = os.path.basename(os.path.abspath(target)) or "this repo"
    if recs is None:
        # No log yet — a clean, honest note, not an empty report. Exit 0 (nothing is wrong).
        print(f"candor-agents: no activity logged yet at {path} — the digest fills in as candor checks "
              f"changes (the stop hook writes the log). Nothing to report.", file=sys.stderr)
        return 0
    report = render(_summary(recs), project, title, _allowed_intros(recs))

    dest = out if out is not None else os.path.join(target, "CANDOR-REPORT.md")
    if dest == "-":
        sys.stdout.write(report)
    else:
        try:
            with open(dest, "w") as f:
                f.write(report)
        except OSError as e:
            print(f"candor-agents digest: could not write {dest} ({e})", file=sys.stderr)
            return 2
        print(f"candor-agents: wrote {dest} ({len(recs)} checks summarised)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
