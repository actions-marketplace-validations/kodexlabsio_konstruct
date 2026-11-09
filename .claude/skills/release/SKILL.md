---
name: release
description: Understand or change how Konstruct is versioned and released (commitizen, GitHub releases, the v0 action tag, the gated PyPI job). Use when asked about versions, a release did not happen or was the wrong size, or publishing.
---

# Releases

- `.github/workflows/release.yml` runs after Build and Test passes on main. commitizen decides
  whether the commits since the last tag call for a release (`docs`, `test`, `ci`, `chore` alone do
  not). While the version is 0.x the workflow fixes the size: a patch, unless a commit header has
  `!:` or a body has a `BREAKING CHANGE:` footer, which gives the next minor.
- It updates `pyproject.toml`, `konstruct/__init__.py`, `uv.lock` and `CHANGELOG.md`, commits
  `chore(release): Release X.Y.Z`, tags `vX.Y.Z`, moves the `v0` tag that action users pin, and
  publishes a GitHub release with the wheel and sdist.
- To check what the next release would be: `uv run cz bump --dry-run --yes`.
- **PyPI** is a separate job, off until the `PUBLISH_TO_PYPI` repository variable is `true` and a
  `pypi` environment with trusted publishing exists. Never turn it on without Dom's go-ahead.
- Never delete or move a published release tag other than `v0`.
