# Releasing candor-agents

candor-agents installs **from a git tag** (`pipx install git+https://github.com/tombaldwin/candor-agents@vX.Y.Z`),
so a tag is what makes a version installable. But a bare tag is invisible on GitHub's Releases API and on
candor.poly.io/releases (which reads `releases/latest`) — so **every tag MUST also get a GitHub Release**.
Skipping it leaves the site and anyone browsing Releases showing a stale "latest".

## The steps (all four — none optional)

1. **Bump the version** in both places (they must match — a drift is a release bug):
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `candor_agents/scan.py` → `VERSION = "agents-X.Y.Z"`
2. **Run the suite** — `python3 test.py` must be green on the release tree.
3. **Update `CHANGELOG.md`** — a new `## [X.Y.Z] — <date>` entry at the top (⚠ marks a verdict/report-affecting
   change). This entry is the release notes.
4. **Commit, tag, push, and cut the GitHub Release:**
   ```sh
   git commit -am "release: candor-agents X.Y.Z — <summary>"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title "candor-agents X.Y.Z — <summary>" --notes "<the CHANGELOG entry>"
   ```

## Versioning

major.minor tracks the **spec** candor-agents declares (`0.8.x` → spec `0.8`); the patch floats. Additive
tooling (a new subcommand) is a patch; a spec-rung bump moves the minor. See the family
[versioning ladder](https://github.com/tombaldwin/candor-spec/blob/main/SPEC.md#versioning-policy).

## After a release

The site (candor.poly.io/releases) refetches `releases/latest` on its hourly build — no action needed once
the GitHub Release exists. If the adopt workflows pin a version
(`candor-agents@vX.Y.Z` in `candor/adopt/*.yml`), bump those pins in the umbrella repo too.
