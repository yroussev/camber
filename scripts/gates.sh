#!/usr/bin/env bash
# Run every release gate against one or more refs, the same way each time.
#
#   scripts/gates.sh                          # the current checkout (HEAD)
#   scripts/gates.sh --tip release-0.87       # one ref, in a temporary worktree
#   scripts/gates.sh --release-tips           # every local release-* branch, oldest first
#   scripts/gates.sh --fast                   # lint, types and tests only
#   scripts/gates.sh --bench-report-only      # benchmarks report moves but don't fail
#
# Gates: ruff check + format (use the CI ruff: CI installs the latest), mypy,
# pytest with the 90% coverage floor, the public-API snapshot, the synthetic, fleet,
# LBNL, BDG2 and BDG2 savings benchmarks, mkdocs --strict, site neutrality and attribution (tree,
# CHANGELOG, commit messages), version consistency and release notes, the
# validation dossier, and an sdist/wheel build with package-data and clean-install
# checks.
#
# Environment:
#   PYTHON    interpreter of a venv that has camber's dev extras (default: python3)
#   RUFF      ruff binary; use the same version CI uses (default: ruff)
#   GATES_OUT output root (default: ${TMPDIR:-/tmp}/camber-gates)
#   DATA_DIR  examples/_data to link into temporary worktrees (default: the
#             invoking checkout's examples/_data); LBNL/BDG2 are skipped without it
#
# Dev tooling; not packaged and not run in CI. Exit status is non-zero when any gate fails.
set -u

PYTHON=${PYTHON:-python3}
RUFF=${RUFF:-ruff}
OUT_ROOT=${GATES_OUT:-${TMPDIR:-/tmp}/camber-gates}
ROOT=$(git rev-parse --show-toplevel)
DATA_DIR=${DATA_DIR:-$ROOT/examples/_data}
FAST=0
BENCH_REPORT_ONLY=0
TIPS=()
RANGE_BASE=origin/main

usage() { sed -n '2,25p' "$0"; exit 2; }
while [ $# -gt 0 ]; do
  case $1 in
    --tip) TIPS+=("$2"); shift 2 ;;
    --release-tips)
      while IFS= read -r b; do TIPS+=("$b"); done < <(git for-each-ref --format='%(refname:short)' 'refs/heads/release-*' | sort -V)
      shift ;;
    --fast) FAST=1; shift ;;
    --bench-report-only) BENCH_REPORT_ONLY=1; shift ;;
    --range-base) RANGE_BASE=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

PYBIN=$(dirname "$(command -v "$PYTHON")")
echo "python: $("$PYTHON" -c 'import sys; print(sys.version.split()[0])')  ruff: $("$RUFF" --version 2>/dev/null || echo missing)"

run_gates() {  # $1 = work tree, $2 = label, $3 = ref for commit messages
  local tree=$1 label=$2 ref=$3
  local out=$OUT_ROOT/$label
  rm -rf "$out"; mkdir -p "$out"
  local sum=$out/summary.txt failed=0
  res() { echo "$1: $2" | tee -a "$sum"; case $2 in FAIL*) failed=1 ;; esac; }
  gate() { local name=$1 log=$2; shift 2; if "$@" >"$out/$log" 2>&1; then res "$name" PASS; else res "$name" "FAIL (see $out/$log)"; fi; }
  (
    cd "$tree" || exit 1
    export PATH=$PYBIN:$PATH
    echo "== $label @ $(git rev-parse --short HEAD)" | tee "$sum"
    gate ruff_check ruff_check.log "$RUFF" check .
    gate ruff_format ruff_format.log "$RUFF" format --check .
    gate mypy mypy.log "$PYTHON" -m mypy
    gate pytest pytest.log "$PYTHON" -m pytest -q -p no:cacheprovider --cov=camber --cov-branch --cov-report=term --cov-fail-under=90
    grep -E "^TOTAL|passed|failed" "$out/pytest.log" | tail -2 | sed 's/^/  /' | tee -a "$sum"
    gate api_snapshot snapshot.log "$PYTHON" -m pytest -q -p no:cacheprovider tests/test_public_api.py
    if [ $FAST -eq 0 ]; then
      bench() {  # name script baseline tol needs_data
        local name=$1 script=$2 base=$3 tol=$4 needs=$5
        if [ "$needs" = 1 ] && [ ! -e examples/_data ]; then res "$name" "SKIP (no examples/_data)"; return; fi
        if "$PYTHON" "$script" --gate "$base" --json "$out/$name.json" --tol "$tol" >"$out/$name.log" 2>&1; then
          res "$name" PASS
        elif [ $BENCH_REPORT_ONLY -eq 1 ]; then
          res "$name" "MOVED (report-only; see $out/$name.log)"
        else
          res "$name" "FAIL (see $out/$name.log)"
        fi
        grep -iE "benchmark gate|regress|stable" "$out/$name.log" | tail -2 | sed 's/^/  /' | tee -a "$sum"
      }
      bench bench_synthetic examples/synthetic_fdd/benchmark.py examples/synthetic_fdd/benchmark-baseline.json 0.0 0
      bench bench_fleet examples/fleet_fdd/benchmark.py examples/fleet_fdd/benchmark-baseline.json 0.0 0
      bench bench_lbnl examples/lbnl_fdd/benchmark.py examples/lbnl_fdd/benchmark-baseline.json 0.05 1
      bench bench_bdg2 examples/bdg2/benchmark.py examples/bdg2/benchmark-baseline.json 0.05 1
      bench bench_bdg2_savings examples/bdg2/savings_benchmark.py examples/bdg2/savings-benchmark-baseline.json 0.05 1
      gate mkdocs_strict mkdocs.log "$PYTHON" -m mkdocs build --strict -d "$out/site"
    fi

    # site neutrality + attribution: tree, CHANGELOG and the commit messages since RANGE_BASE
    local msgs; msgs=$(git log --format='%H %s%n%b' "$RANGE_BASE..$ref" 2>/dev/null)
    local nfail=0
    local ex=(':(exclude).github/scripts/site_neutrality_patterns.py' ':(exclude).github/workflows/site-neutrality-guard.yml' ':(exclude).githooks/denylist.local' ':(exclude).githooks/denylist.local.example')
    # third field: paths exempt from that rule in the tree scan only; CHANGELOG and commit
    # messages are checked against every rule, as in site-neutrality-guard.yml
    while IFS=$'\t' read -r pat _ exempt; do
      [ -n "$pat" ] || continue
      local rex=() p
      if [ -n "${exempt:-}" ]; then
        local _paths=()
        IFS=',' read -r -a _paths <<<"$exempt"
        for p in "${_paths[@]}"; do rex+=(":(exclude)$p"); done
      fi
      git grep -iInE -- "$pat" -- . "${ex[@]}" ${rex[@]+"${rex[@]}"} >>"$out/neutrality.log" && nfail=1
      grep -inE -- "$pat" CHANGELOG.md >>"$out/neutrality.log" && nfail=1
      printf '%s\n' "$msgs" | grep -inE -- "$pat" >>"$out/neutrality.log" && nfail=1
    done < <("$PYTHON" .github/scripts/site_neutrality_patterns.py)
    [ $nfail -eq 0 ] && res site_neutrality PASS || res site_neutrality "FAIL (see $out/neutrality.log)"
    local afail=0
    # the patterns live in the pre-commit hook (the single source); read them from there
    local apats=()
    while IFS= read -r p; do apats+=("$p"); done < <(awk '/^patterns=\(/{f=1;next} f&&/^\)/{exit} f{gsub(/^[ \t]+'\''|'\''[ \t]*$/,"");print}' .githooks/pre-commit)
    [ ${#apats[@]} -gt 0 ] || { res attribution "FAIL (no patterns found in .githooks/pre-commit)"; }
    for pat in "${apats[@]}"; do
      git grep -iInE "$pat" -- . ':(exclude).githooks/commit-msg' ':(exclude).githooks/pre-commit' ':(exclude).github/workflows/attribution-guard.yml' >>"$out/attribution.log" && afail=1
      printf '%s\n' "$msgs" | grep -inE "$pat" >>"$out/attribution.log" && afail=1
    done
    git ls-files | grep -iqE '(^|/)CLAUDE\.md$' && afail=1
    [ $afail -eq 0 ] && res attribution PASS || res attribution "FAIL (see $out/attribution.log)"

    # version consistency and release notes (what release.yml checks)
    local pv iv
    pv=$(sed -nE 's/^version = "([^"]+)".*/\1/p' pyproject.toml | head -1)
    iv=$(sed -nE 's/.*__version__ = "([^"]+)".*/\1/p' camber/__init__.py | head -1)
    [ "$pv" = "$iv" ] && res version "PASS ($pv)" || res version "FAIL (pyproject $pv, __init__ $iv)"
    awk -v v="$pv" 'index($0,"## ["v"]")==1{f=1;next} f&&/^## \[/{exit} f{print}' CHANGELOG.md >"$out/RELEASE_NOTES.md"
    [ -s "$out/RELEASE_NOTES.md" ] && res changelog_notes "PASS ($(wc -l <"$out/RELEASE_NOTES.md" | tr -d ' ') lines)" || res changelog_notes "FAIL (no ## [$pv] entry)"

    if [ $FAST -eq 0 ]; then
      # camber has no __main__; call the CLI entry point (the checked-out tree, not an install)
      gate dossier dossier.log "$PYTHON" -c 'import sys; from camber.cli import main; sys.exit(main(sys.argv[1:]))' \
        validate --html "$out/dossier.html" --json "$out/dossier.json"
      # build + package data + clean install
      if "$PYTHON" -m build --outdir "$out/dist" >"$out/build.log" 2>&1 && "$PYTHON" -m twine check "$out"/dist/* >>"$out/build.log" 2>&1; then
        local whl sdist miss=0
        whl=$(ls "$out"/dist/*.whl); sdist=$(ls "$out"/dist/*.tar.gz)
        unzip -Z1 "$whl" >"$out/whl.txt"; tar tzf "$sdist" | sed 's#^[^/]*/##' >"$out/sdist.txt"
        while IFS= read -r f; do
          grep -qx "$f" "$out/whl.txt" || { echo "missing in wheel: $f" >>"$out/pkg.log"; miss=1; }
          grep -qx "$f" "$out/sdist.txt" || { echo "missing in sdist: $f" >>"$out/pkg.log"; miss=1; }
        done < <(git ls-files camber)
        "$PYTHON" -m venv "$out/smoke" >/dev/null 2>&1 && "$out/smoke/bin/pip" install -q "$whl" >>"$out/pkg.log" 2>&1 \
          && (cd "$out" && "$out/smoke/bin/python" -c "import camber, camber.datasets; from importlib.resources import files; assert files('camber.datasets').joinpath('catalog.json').is_file(); print(camber.__version__)") >>"$out/pkg.log" 2>&1 \
          || miss=1
        [ $miss -eq 0 ] && res build_install PASS || res build_install "FAIL (see $out/pkg.log)"
      else
        res build_install "FAIL (see $out/build.log)"
      fi
    fi
    echo "summary: $sum"
    exit $failed
  )
}

status=0
if [ ${#TIPS[@]} -eq 0 ]; then
  run_gates "$ROOT" "$(git rev-parse --abbrev-ref HEAD | tr '/' '-')" HEAD || status=1
else
  for ref in "${TIPS[@]}"; do
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/camber-gates-tree.XXXXXX")
    git worktree add --detach "$tmp" "$ref" >/dev/null 2>&1 || { echo "cannot check out $ref" >&2; status=1; continue; }
    [ -e "$DATA_DIR" ] && ln -s "$DATA_DIR" "$tmp/examples/_data"
    run_gates "$tmp" "$(echo "$ref" | tr '/' '-')" "$ref" || status=1
    git worktree remove --force "$tmp" >/dev/null 2>&1
  done
fi
[ $status -eq 0 ] && echo "ALL GATES PASSED" || echo "SOME GATES FAILED"
exit $status
