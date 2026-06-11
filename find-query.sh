#!/usr/bin/env bash
# Locate — or fetch and build — the UNMODIFIED candor-query binary; print its path on stdout.
# Ladder: $CANDOR_QUERY (explicit binary) > PATH > a sibling/explicit clone ($CANDOR, default
# ../candor-rust) > a cached clone (~/.cache/candor-agents/candor-rust, fetched on first use).
# Builds with `cargo build --manifest-path` FROM OUTSIDE the repo, so candor-rust's pinned-nightly
# rust-toolchain file does not apply — candor-query is plain stable Rust (the same trick the spec's
# conformance runner uses). All progress goes to stderr; stdout is only the path.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { echo "$@" >&2; }

if [ -n "${CANDOR_QUERY:-}" ] && [ -x "$CANDOR_QUERY" ]; then echo "$CANDOR_QUERY"; exit 0; fi
if command -v candor-query >/dev/null 2>&1; then command -v candor-query; exit 0; fi

build() { # $1 = candor-rust checkout; prints the binary path on success
  [ -f "$1/Cargo.toml" ] || return 1
  if [ ! -x "$1/target/debug/candor-query" ]; then
    say "candor-agents: building candor-query (stable) in $1 …"
    cargo build -q --manifest-path "$1/Cargo.toml" -p candor-query 2>/dev/null || return 1
  fi
  echo "$1/target/debug/candor-query"
}

if build "${CANDOR:-$HERE/../candor-rust}"; then exit 0; fi

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/candor-agents/candor-rust"
if [ ! -d "$CACHE" ]; then
  say "candor-agents: fetching candor-rust (for its query binary) → $CACHE"
  git clone -q --depth 1 https://github.com/tombaldwin/candor-rust "$CACHE" \
    || { say "candor-agents: clone failed (offline?) — set CANDOR_QUERY or CANDOR"; exit 1; }
fi
if build "$CACHE"; then exit 0; fi
say "candor-agents: could not locate or build candor-query (set CANDOR_QUERY to a binary, or CANDOR to a candor-rust checkout)"
exit 1
