---
name: change
description: Make and finish a change to Konstruct (Python, viewer or docs) so it passes every check CI runs, with docs, golden files and a conforming commit. Use for any code change in this repository.
---

# Making a change to Konstruct

1. **Read before writing.** `AGENTS.md` has the layout, rules and pins. Find the module that owns
   the behaviour (see the layout table) and its test file in `tests/`.
2. **Write the failing test first** when fixing behaviour. Prefer the smallest input: a SQL
   statement in `tests/test_sql.py`, a DAG snippet in `tests/test_airflow.py`, a tiny project in a
   `tmp_path`. Example-project tests use the `jaffle_built_graph` or `jaffle` fixtures.
3. **Make the change.** Typed Python (mypy strict), single quotes, lines of 110. Never import or run
   user code.
4. **Run the checks** that apply:
   ```bash
   uv run ruff check . && uv run ruff format . && uv run mypy
   eval "$(.kodex/scripts/services.sh up)"     # Postgres, Iceberg REST, BigQuery emulator
   uv run pytest --cov                          # at least 90%
   ```
   If CLI text or REPORT.md changed: `KONSTRUCT_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py`,
   then read the diff of `tests/golden/` and keep only intended changes.
   If `viewer/` changed, follow the `viewer` skill. If an output a user sees changed, follow `docs`.
5. **Commit** with Conventional Commits: `type(scope): Sentence-case subject` (scopes are listed in
   `.kodex/scripts/check_commit_message.py`), a body that says why, lines of at most 100 characters
   and no AI attribution. Check it first:
   ```bash
   uv run python .kodex/scripts/check_commit_message.py /tmp/message.txt
   git commit -F /tmp/message.txt
   ```
   If signing fails with "failed to fill whole buffer", ask for 1Password to be unlocked; never
   pass `--no-gpg-sign`.
6. **Push and watch CI** (`gh run watch`). A passing push to main releases automatically: `feat` and
   `fix` give a patch while the version is 0.x.
