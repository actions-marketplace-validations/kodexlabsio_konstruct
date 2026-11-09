from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / '.kodex' / 'scripts' / 'prepare_release.py'


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('prepare_release', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def test_a_patch_unless_breaking_and_never_1_0() -> None:
    assert release.next_version('0.11.1', breaking=False) == '0.11.2'
    assert release.next_version('0.11.1', breaking=True) == '0.12.0'
    assert release.next_version('0.99.3', breaking=True) == '0.100.0'
    with pytest.raises(SystemExit):
        release.next_version('1.0.0', breaking=False)


def test_the_changelog_section_groups_releasable_commits() -> None:
    text = release.section(
        '0.11.2',
        [
            ('feat(mcp): Add the ontology tool', ''),
            ('docs: Explain the ontology', ''),
            ('fix: Skip the fetch', ''),
            ('perf(build): Build faster', ''),
            ('feat(cli)!: Rename build', ''),
            ('refactor(sql): Tidy', 'BREAKING CHANGE: the lineage API moved'),
        ],
    )
    assert text.startswith('## v0.11.2 (')
    body = text.split('\n', 1)[1]
    assert body == (
        '\n### BREAKING CHANGE\n\n- **cli**: Rename build\n- **sql**: Tidy\n\n'
        '### Feat\n\n- **mcp**: Add the ontology tool\n- **cli**: Rename build\n\n'
        '### Fix\n\n- Skip the fetch\n\n'
        '### Refactor\n\n- **sql**: Tidy\n\n'
        '### Perf\n\n- **build**: Build faster\n'
    )


def test_nothing_to_release(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(release, 'last_tag', lambda: 'v0.11.1')
    monkeypatch.setattr(release, 'commits_since', lambda tag, base: [('docs: Explain', ''), ('ci: Pin', '')])
    assert release.main(['--title', 'test(tests): Add a case', '--dry-run']) == 0
    assert capsys.readouterr().out == 'nothing to release since v0.11.1\n'


def test_a_dry_run_counts_main_and_the_pull_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release, 'last_tag', lambda: 'v0.11.1')
    monkeypatch.setattr(release, 'commits_since', lambda tag, base: [('fix(sql): Order sources', '')])
    assert release.main(['--title', 'feat(cli): Add ontology', '--dry-run']) == 0
    out = capsys.readouterr().out
    assert out.startswith('0.11.2\n## v0.11.2 (')
    assert '### Feat\n\n- **cli**: Add ontology' in out
    assert '### Fix\n\n- **sql**: Order sources' in out
