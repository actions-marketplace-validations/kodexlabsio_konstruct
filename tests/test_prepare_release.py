from __future__ import annotations

import importlib.util
import subprocess
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


def test_the_section_follows_commitizen() -> None:
    text = release.section(
        '0.12.0',
        [
            ('feat(mcp): Add the ontology tool', ''),
            ('docs: Explain the ontology', ''),
            ('fix: Skip the fetch', ''),
            ('perf(build): Build faster', ''),
            ('feat(cli)!: Rename build', ''),
            ('refactor(sql): Tidy', 'Moves code.\n\nBREAKING CHANGE: the lineage API moved'),
        ],
        date='2026-11-09',
    )
    assert text == (
        '## v0.12.0 (2026-11-09)\n\n'
        '### BREAKING CHANGE\n\n- **sql**: the lineage API moved\n\n'
        '### Feat\n\n- **mcp**: Add the ontology tool\n- **cli**: Rename build\n\n'
        '### Fix\n\n- Skip the fetch\n\n'
        '### Refactor\n\n- **sql**: Tidy\n\n'
        '### Perf\n\n- **build**: Build faster\n'
    )


def test_any_breaking_change_releases() -> None:
    assert release.releasable([('docs: Explain', ''), ('build(deps)!: Drop Python 3.11', '')]) == [
        ('build(deps)!: Drop Python 3.11', '')
    ]
    assert release.releasable([('ci: Pin', 'BREAKING CHANGE: needs uv 1')]) == [
        ('ci: Pin', 'BREAKING CHANGE: needs uv 1')
    ]
    assert release.releasable([('docs: Explain', ''), ('test: Add', '')]) == []


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository at v0.11.1 with a fix on main since the tag."""

    def run(*args: str) -> None:
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / 'konstruct').mkdir()
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "konstruct"\nversion = "0.11.1"\n')
    (tmp_path / 'uv.lock').write_text(
        'version = 1\nrevision = 5\n\n[[package]]\nname = "duckdb"\nversion = "2.0.0"\n\n'
        '[[package]]\nname = "konstruct"\nversion = "0.11.1"\nsource = { editable = "." }\n'
    )
    (tmp_path / 'konstruct' / '__init__.py').write_text("__version__ = '0.11.1'\n")
    (tmp_path / 'CHANGELOG.md').write_text('## v0.11.1 (2026-11-08)\n\n### Fix\n\n- Older fix\n')
    run('git', 'init', '-q', '-b', 'main')
    run('git', 'add', '-A')
    run(
        'git',
        '-c',
        'user.name=t',
        '-c',
        'user.email=t@e',
        '-c',
        'commit.gpgsign=false',
        'commit',
        '-qm',
        'base',
    )
    run('git', 'tag', 'v0.11.1')
    (tmp_path / 'a.txt').write_text('a')
    run('git', 'add', '-A')
    run(
        'git',
        '-c',
        'user.name=t',
        '-c',
        'user.email=t@e',
        '-c',
        'commit.gpgsign=false',
        'commit',
        '-qm',
        'fix(sql): Order sources',
    )
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    return tmp_path


def _prepare(*args: str) -> int:
    return int(release.main(['--base', 'main', '--no-fetch', *args]))


def test_prepares_the_next_version_once(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _prepare('--title', 'feat(cli): Add ontology') == 0
    assert capsys.readouterr().out == '0.11.2\n'
    assert 'version = "0.11.2"' in (repo / 'pyproject.toml').read_text()
    lock = (repo / 'uv.lock').read_text()
    assert 'revision = 5' in lock and 'name = "konstruct"\nversion = "0.11.2"' in lock
    assert 'name = "duckdb"\nversion = "2.0.0"' in lock
    assert (repo / 'konstruct' / '__init__.py').read_text() == "__version__ = '0.11.2'\n"
    changelog = (repo / 'CHANGELOG.md').read_text()
    assert changelog.startswith('## v0.11.2 (')
    assert '### Feat\n\n- **cli**: Add ontology\n\n### Fix\n\n- **sql**: Order sources\n' in changelog
    assert changelog.endswith('## v0.11.1 (2026-11-08)\n\n### Fix\n\n- Older fix\n')
    # Running it again replaces the section rather than adding a second one.
    assert _prepare('--title', 'feat(cli): Add ontology') == 0
    assert (repo / 'CHANGELOG.md').read_text().count('## v0.11.2 (') == 1


def test_nothing_to_release(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    subprocess.run(['git', 'tag', 'v0.11.2'], cwd=repo, check=True)
    assert _prepare('--title', 'docs: Explain', '--dry-run') == 0
    assert capsys.readouterr().out == 'nothing to release since v0.11.2\n'


def test_refuses_while_a_prepared_release_is_untagged(repo: Path) -> None:
    (repo / 'pyproject.toml').write_text('[project]\nname = "konstruct"\nversion = "0.11.2"\n')
    subprocess.run(
        [
            'git',
            '-c',
            'user.name=t',
            '-c',
            'user.email=t@e',
            '-c',
            'commit.gpgsign=false',
            'commit',
            '-qam',
            'x',
        ],
        cwd=repo,
        check=True,
    )
    with pytest.raises(SystemExit, match='not tagged yet'):
        _prepare('--title', 'fix: More')


def test_carries_sections_of_releases_made_from_a_detached_commit(repo: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(args, cwd=repo, check=True, capture_output=True)

    # The workflow released v0.11.2 from a commit above main; main's changelog does not have it.
    run('git', 'checkout', '-q', '--detach')
    assert _prepare() == 0
    run(
        'git',
        '-c',
        'user.name=t',
        '-c',
        'user.email=t@e',
        '-c',
        'commit.gpgsign=false',
        'commit',
        '-qam',
        'release',
    )
    run('git', 'tag', 'v0.11.2')
    run('git', 'checkout', '-q', 'main')
    assert _prepare('--title', 'feat(mcp): Add a tool') == 0
    changelog = (repo / 'CHANGELOG.md').read_text()
    headings = [line.split(' (')[0] for line in changelog.splitlines() if line.startswith('## ')]
    assert headings == ['## v0.11.3', '## v0.11.2', '## v0.11.1']
    assert '## v0.11.3 (' in changelog and '- **mcp**: Add a tool' in changelog.split('## v0.11.2')[0]
    assert '- **sql**: Order sources' in changelog.split('## v0.11.2')[1].split('## v0.11.1')[0]


def test_check_passes_unprepared_and_matching_pull_requests(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _prepare('--title', 'fix: More', '--check') == 0
    assert 'prepares no release' in capsys.readouterr().out
    assert _prepare('--title', 'fix: More') == 0
    capsys.readouterr()
    assert _prepare('--title', 'fix: More', '--check') == 0
    assert 'v0.11.2 is prepared and matches main' in capsys.readouterr().out
    # A breaking title now calls for a minor, so the prepared patch no longer matches.
    assert _prepare('--title', 'feat!: Rename everything', '--check') == 1
    assert '0.12.0 expected' in capsys.readouterr().out
