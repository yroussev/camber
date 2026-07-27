#!/usr/bin/env bash
# Enable CAMBER's versioned git hooks (attribution guard).
#
# Git does not share .git/hooks across clones, so the hooks live in the tracked
# .githooks/ directory. This one-liner points git at them for your local clone.
#
#   bash scripts/install-hooks.sh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

git config core.hooksPath .githooks
chmod +x .githooks/commit-msg .githooks/pre-commit 2>/dev/null || true

echo "Installed: core.hooksPath -> .githooks"
echo "Active hooks: commit-msg, pre-commit (attribution guard)."
