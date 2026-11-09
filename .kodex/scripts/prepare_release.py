"""Set the next release's version and changelog in a pull request.

main is protected, so the release workflow cannot commit a version bump itself. Instead, a pull
request that should release runs this script: it reads the releasable commits on main since the
last tag, adds the pull request's own title, works out the next version, and updates
pyproject.toml, uv.lock, konstruct/__init__.py and CHANGELOG.md. When the pull request is merged
and Build and Test passes on main, the release workflow tags that version and publishes it.

While the version is 0.x a release is a patch, unless a change is marked breaking (`feat!:` or a
`BREAKING CHANGE:` footer), which gives the next minor. The script never moves the version to 1.0:
that is the owner's decision, made by hand.

Usage:
    uv run python .kodex/scripts/prepare_release.py --title "fix(sql): Handle an empty catalog"
    uv run python .kodex/scripts/prepare_release.py --title "..." --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASABLE = {'feat': 'Feat', 'fix': 'Fix', 'refactor': 'Refactor', 'perf': 'Perf'}
ORDER = ['BREAKING CHANGE', 'Feat', 'Fix', 'Refactor', 'Perf']
HEADER = re.compile(r'^(?P<type>\w+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?: (?P<subject>.+)$')
BREAKING_FOOTER = re.compile(r'^BREAKING[ -]CHANGE: ', re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def last_tag() -> str:
    tags = [t for t in git('tag', '--list', 'v*').split() if re.fullmatch(r'v\d+\.\d+\.\d+', t)]
    if not tags:
        sys.exit('no release tag found; fetch tags first (git fetch --tags)')
    return max(tags, key=lambda t: tuple(int(p) for p in t[1:].split('.')))


def commits_since(tag: str, base: str) -> list[tuple[str, str]]:
    """Subjects and bodies of the commits on ``base`` since ``tag``, newest first."""
    out = git('log', '--no-merges', '--format=%s%x00%b%x1e', f'{tag}..{base}')
    found = []
    for record in out.split('\x1e'):
        if record.strip():
            subject, _, body = record.strip('\n').partition('\x00')
            found.append((subject, body))
    return found


def next_version(current: str, breaking: bool) -> str:
    major, minor, patch = (int(p) for p in current.split('.'))
    if major == 0:
        return f'0.{minor + 1}.0' if breaking else f'0.{minor}.{patch + 1}'
    sys.exit('the version is 1.0 or later; this script only prepares 0.x releases')


def section(version: str, entries: list[tuple[str, str]]) -> str:
    groups: dict[str, list[str]] = {}
    for subject, body in entries:
        match = HEADER.match(subject)
        if not match or match['type'] not in RELEASABLE:
            continue
        line = f'- **{match["scope"]}**: {match["subject"]}' if match['scope'] else f'- {match["subject"]}'
        groups.setdefault(RELEASABLE[match['type']], []).append(line)
        if match['breaking'] or BREAKING_FOOTER.search(body):
            groups.setdefault('BREAKING CHANGE', []).append(line)
    parts = [f'## v{version} ({dt.date.today().isoformat()})']
    for name in ORDER:
        if name in groups:
            parts.append(f'### {name}\n\n' + '\n'.join(groups[name]))
    return '\n\n'.join(parts) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--title', required=True, help="the pull request's title, as it will be squash-merged"
    )
    parser.add_argument(
        '--body', default='', help="the merge commit's body, to find a BREAKING CHANGE footer"
    )
    parser.add_argument('--base', default='origin/main', help='the branch the pull request merges into')
    parser.add_argument('--dry-run', action='store_true', help='print the version and changelog only')
    args = parser.parse_args(argv)

    tag = last_tag()
    entries = [(args.title, args.body), *commits_since(tag, args.base)]
    releasable = [(s, b) for s, b in entries if (m := HEADER.match(s)) and m['type'] in RELEASABLE]
    if not releasable:
        print(f'nothing to release since {tag}')
        return 0
    breaking = any(
        (m := HEADER.match(s)) and (m['breaking'] or BREAKING_FOOTER.search(b)) for s, b in releasable
    )
    version = next_version(tag[1:], breaking)
    if not version.startswith('0.'):
        sys.exit(f'refusing to prepare {version}: Konstruct stays below 1.0 until the owner decides')
    changes = section(version, releasable)
    if args.dry_run:
        print(version)
        print(changes)
        return 0

    subprocess.run(['uv', 'version', '--no-sync', version], cwd=ROOT, check=True, capture_output=True)
    init = ROOT / 'konstruct' / '__init__.py'
    init.write_text(
        re.sub(r"^__version__ = '[^']*'$", f"__version__ = '{version}'", init.read_text(), flags=re.M),
        encoding='utf-8',
    )
    changelog = ROOT / 'CHANGELOG.md'
    changelog.write_text(changes + '\n' + changelog.read_text(encoding='utf-8'), encoding='utf-8')
    print(version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
