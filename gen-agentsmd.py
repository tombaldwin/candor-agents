#!/usr/bin/env python3
"""Regenerate candor_agents/agentsmd.py from AGENTS.md (the canonical doc).

The agent contract is embedded as a module so `candor-agents --agents` describes the INSTALLED
build (AGENTS.md itself isn't shipped in the wheel); test.py gates drift between the two. Run this
after editing AGENTS.md.
"""
import pathlib

HERE = pathlib.Path(__file__).parent
doc = (HERE / "AGENTS.md").read_text()
(HERE / "candor_agents" / "agentsmd.py").write_text(
    '"""The embedded agent contract — GENERATED from AGENTS.md by gen-agentsmd.py; do not edit.\n\n'
    'The wheel embeds the contract as a module, so `candor-agents --agents` reads this, not the file.\n'
    'test.py fails if this drifts from AGENTS.md.\n"""\n'
    f"AGENTS_MD = {doc!r}\n"
)
print(f"candor_agents/agentsmd.py regenerated ({len(doc)} chars)")
