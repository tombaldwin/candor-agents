#!/usr/bin/env bash
# The whole exploration in one run: scan the fixture fleet, query it with the UNMODIFIED
# candor-query binary, evaluate the §6.2 policy (current state + pre-edit whatif).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANDOR="${CANDOR:-$HERE/../candor-rust}"
Q="$CANDOR/target/debug/candor-query"
[ -x "$Q" ] || { echo "need candor-query: (cd $CANDOR && cargo build -p candor-query)"; exit 1; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

echo "== scan (fleet → spec §2 report + §2.2 sidecar)"
python3 "$HERE/scan.py" "$HERE/fixture" --out "$W/r" --fleet demo || exit 1

echo; echo "== the unmodified candor-query over the fleet report"
echo "-- where Net:";               "$Q" where   "$W/r" Net 0
echo; echo "-- show orchestrator:"; "$Q" show    "$W/r" orchestrator 0
echo; echo "-- callers researcher (blast radius):"; "$Q" callers "$W/r" researcher 0

echo; echo "== current-state policy gate (deny rules × inferred; parse via candor-query parsepolicy)"
"$Q" parsepolicy "$HERE/fixture/policy" >"$W/p.json" || exit 1
python3 - "$W/r.demo.Fleet.json" "$W/p.json" <<'PY'
import json, sys
rep = json.load(open(sys.argv[1])); pol = json.load(open(sys.argv[2]))
viol = []
for rule in pol.get("deny", []):
    for f in rep["functions"]:
        if rule["scope"] and rule["scope"] in f["fn"]:
            for eff in rule["effects"]:
                if eff in f["inferred"]:
                    how = "direct" if eff in f["direct"] else "inherited"
                    viol.append(f"  [AS-EFF-006] {f['fn']} reaches {eff} ({how})  (rule: deny {eff} {rule['scope']})")
print("\n".join(viol) if viol else "  gate clean")
PY

echo; echo "== pre-edit gate (whatif × policy)"
echo "-- whatif researcher Ipc:"; "$Q" whatif "$W/r" researcher Ipc "$HERE/fixture/policy"
echo; echo "-- whatif mailer Exec:"; "$Q" whatif "$W/r" mailer Exec "$HERE/fixture/policy"

echo; echo "(The fixture is DELIBERATELY already in violation: the orchestrator reaches Exec through"
echo " the coder's unnamed delegation — exactly the finding a fleet owner needs surfaced.)"

echo; echo "== COMBINED MODE: fleet + code under one prefix, linked"
if [ -d "$CANDOR/sample" ] && [ -x "$CANDOR/target/debug/candor-scan" ]; then
  C="$W/combo"; mkdir -p "$C"
  "$CANDOR/target/debug/candor-scan" "$CANDOR/sample" --out "$C/r" >/dev/null 2>&1
  python3 "$HERE/scan.py" "$HERE/fixture" --out "$C/r" --fleet fixture --link "$C/r" >/dev/null
  echo "-- show coder (fleet agent inheriting the CODE's measured effects):"
  "$Q" show "$C/r" coder 0
  echo "-- callers now_ms (a CODE fn's blast radius climbs into the FLEET):"
  "$Q" callers "$C/r" now_ms 0
  echo "-- whatif now_ms Exec (the gate verdict crosses the boundary):"
  "$Q" whatif "$C/r" now_ms Exec "$HERE/fixture/policy"
  echo "   (exit $? — 1 = the orchestrator's deny Exec rule fires from a code-level edit)"
else
  echo "   SKIP (need candor-rust sample/ + candor-scan built)"
fi
