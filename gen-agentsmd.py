#!/usr/bin/env python3
"""Regenerate agentsmd.py from AGENTS.md (the canonical doc).

The wheel ships py-modules only, so the agent contract is embedded as a module; test.py gates
drift between the two. Run this after editing AGENTS.md.
"""
import pathlib

HERE = pathlib.Path(__file__).parent
doc = (HERE / "AGENTS.md").read_text()
(HERE / "agentsmd.py").write_text(
    '"""The embedded agent contract — GENERATED from AGENTS.md by gen-agentsmd.py; do not edit.\n\n'
    'The wheel ships py-modules only, so `candor-agents --agents` reads this module, not the file.\n'
    'test.py fails if this drifts from AGENTS.md.\n"""\n'
    f"AGENTS_MD = {doc!r}\n"
)
print(f"agentsmd.py regenerated ({len(doc)} chars)")
