#!/usr/bin/env bash
# Rebuild the viewer and fail if the page konstruct ships differs from what is staged, so the
# committed konstruct/viewer/index.html always matches viewer/src. Run by pre-commit and CI.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
pnpm --dir viewer build > /dev/null
if ! git diff --quiet -- konstruct/viewer/index.html; then
    echo "konstruct/viewer/index.html was rebuilt from viewer/src; stage it (git add konstruct/viewer/index.html)."
    exit 1
fi
