#!/usr/bin/env bash
# Check text before posting it anywhere public (issue, comment, PR, release notes):
# fails if it contains an absolute local filesystem path or the local account name.
#
#   scripts/check-text.sh FILE...      # or: some-command | scripts/check-text.sh -
#
# Uses the same patterns as the site-neutrality guard and the text-hygiene workflow.
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
status=0
while IFS=$'\t' read -r pat why; do
  case $why in *"local filesystem path"*|*"local account"*) ;; *) continue ;; esac
  for f in "${@:--}"; do
    if grep -niE -- "$pat" "$f" | cut -d: -f1 | sed "s|^|$f: line |; s|$|: $why|" | grep .; then status=1; fi
  done
done < <(python3 "$root/.github/scripts/site_neutrality_patterns.py")
[ $status -eq 0 ] && echo "clean"
exit $status
