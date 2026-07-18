"""candor-agents — effect analysis for agent fleets (candor-spec 0.22).

The CLI entry point is `candor_agents.cli:main`. Modules are runnable individually via
`python -m candor_agents.<module>` (scan/observe/cli), which is how the CLI dispatches its
subprocesses — so this package MUST NOT eagerly import its submodules (that would re-enter
`candor_agents.scan` under `runpy -m` and warn). `VERSION`/`SPEC` are exposed lazily for embedders.
"""

__all__ = ["VERSION", "SPEC"]


def __getattr__(name):  # PEP 562 — lazy, to keep `-m candor_agents.scan` runpy-clean
    if name in __all__:
        from candor_agents.scan import VERSION, SPEC
        return {"VERSION": VERSION, "SPEC": SPEC}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
