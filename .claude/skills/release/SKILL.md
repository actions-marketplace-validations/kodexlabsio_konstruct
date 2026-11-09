---
name: release
description: Understand or change how Konstruct is versioned and released (commitizen, GitHub releases, the v0 action tag, the gated PyPI job). Use when asked about versions, a release did not happen or was the wrong size, or publishing.
---

# Releases

- main is protected, so a release's version arrives in the pull request that releases. Before
  opening a pull request with a `feat`, `fix`, `perf` or `refactor` change, run
  `uv run python .kodex/scripts/prepare_release.py --title "<the pull request's title>"`, which
  reads the releasable commits on main since the last tag, adds the title, and sets the next
  version in `pyproject.toml`, `uv.lock` and `konstruct/__init__.py`, with its section at the top of
  `CHANGELOG.md`. `--dry-run` prints the version and section only.
- While the version is 0.x a release is a patch, unless a change has `!:` in its header or a
  `BREAKING CHANGE:` footer, which gives the next minor. The script never moves the version to 1.0:
  the owner decides that.
- `.github/workflows/release.yml` runs after Build and Test passes on main. When the tested commit
  carries a version with no tag, it tags `vX.Y.Z`, moves the `v0` tag that action users pin, and
  publishes a GitHub release with the changelog section, the wheel and the sdist. It pushes only
  tags, never main. A push whose version is already released (a `docs` change, or one merged without
  running the script) makes no release; the next prepared pull request includes its changes.
- **PyPI** is a separate job, off until the `PUBLISH_TO_PYPI` repository variable is `true` and a
  `pypi` environment with trusted publishing exists. Never turn it on without Dom's go-ahead.
- Never delete or move a published release tag other than `v0`.
