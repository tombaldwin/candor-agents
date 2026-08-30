#!/usr/bin/env bash
# The whole exploration in one run: scan the fixture fleet, query it with the UNMODIFIED
# candor-query binary, evaluate the §6.2 policy (current state + pre-edit whatif).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
Q="$("$HERE/find-query.sh")" || exit 1
# The candor-rust CHECKOUT — which is not necessarily where the query BINARY lives. The old form
# derived it as target/debug/candor-query → repo root, which is only true of a build tree: a
# cargo-INSTALLED candor-query sits in ~/.cargo/bin, so ../.. is $HOME. That is how the combined-mode
# demo below skipped on this machine while a perfectly good checkout with sample/ sat at
# ../candor-rust — and its skip line blamed a missing checkout, naming a cause that was not the one.
# Prefer the sibling checkout; fall back to the binary-relative guess.
if [ -d "$HERE/../candor-rust/sample" ]; then
  CANDOR_DIR="$(cd "$HERE/../candor-rust" && pwd)"
else
  CANDOR_DIR="$(cd "$(dirname "$Q")/../.." && pwd)"
fi
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

echo "== scan (fleet → spec §2 report + §2.2 sidecar)"
PYTHONPATH="$HERE" python3 -m candor_agents.scan "$HERE/fixture" --out "$W/r" --fleet demo || exit 1

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
SCAN="$CANDOR_DIR/target/debug/candor-scan"
if [ -d "$CANDOR_DIR/sample" ] && { [ -x "$SCAN" ] || cargo build -q --manifest-path "$CANDOR_DIR/Cargo.toml" -p candor-scan 2>/dev/null; }; then
  C="$W/combo"; mkdir -p "$C"
  "$SCAN" "$CANDOR_DIR/sample" --out "$C/r" >/dev/null 2>&1
  PYTHONPATH="$HERE" python3 -m candor_agents.scan "$HERE/fixture" --out "$C/r" --fleet fixture --link "$C/r" >/dev/null
  echo "-- show coder (fleet agent inheriting the CODE's measured effects):"
  "$Q" show "$C/r" coder 0
  echo "-- callers now_ms (a CODE fn's blast radius climbs into the FLEET):"
  "$Q" callers "$C/r" now_ms 0
  echo "-- whatif now_ms Exec (the gate verdict crosses the boundary):"
  "$Q" whatif "$C/r" now_ms Exec "$HERE/fixture/policy"
  echo "   (exit $? — 1 = the orchestrator's deny Exec rule fires from a code-level edit)"
else
  # Name the leg that actually failed. One message for a two-legged condition is how the old text
  # came to assert a missing checkout on a machine that had one.
  if [ ! -d "$CANDOR_DIR/sample" ]; then
    echo "   SKIP (no candor-rust checkout with sample/ at $CANDOR_DIR)"
  else
    echo "   SKIP (found $CANDOR_DIR/sample, but candor-scan is not built and the build failed)"
  fi
fi
