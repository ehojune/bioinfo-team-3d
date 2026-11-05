#!/usr/bin/env bash
# Pre-publish check for a PUBLIC repo: secrets, real configs, large or data files.
# Needs bash with GNU-style xargs -0/find/grep (Git Bash on Windows, Linux CI). Names travel NUL-separated, so
# spaces, newlines, quotes, non-ASCII and a leading "-" reach grep/find intact. A tracked file that cannot be read
# (missing, unreadable, a dangling link) fails the check instead of being skipped. Exit 0 = publishable, 1 = not.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
if git ls-files --error-unmatch config/labhq.yaml >/dev/null 2>&1 || [ -f config/labhq.yaml -a ! -f .gitignore ]; then
  echo "✗ config/labhq.yaml (실제 토큰이 든 설정)이 올라가려 합니다"; fail=1
fi
if git ls-files -z >/dev/null 2>&1; then
  files() { git ls-files -z; }
else
  files() { find . -type f -not -path "./.git/*" -print0; }
fi
pat='gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|BEGIN [A-Z ]*PRIVATE KEY'
# xargs runs one grep per batch of names; a grep error (exit 2) becomes 255 so xargs stops with a failure.
# LC_ALL=C: the ASCII pattern matches the same, and a non-UTF-8 text file prints its line; grep 3.5+ would
# send its "binary file matches" to stderr, where $hits never sees it.
hits=$(files | xargs -0 sh -c 'p=$1; shift; [ $# -eq 0 ] || LC_ALL=C grep -HnIE -e "$p" -- "$@"; [ $? -le 1 ] || exit 255' sh "$pat") \
  || { echo "✗ 추적 파일을 읽지 못해 비밀값 검사를 끝내지 못했습니다"; fail=1; }
[ -n "$hits" ] && { printf '%s\n' "$hits"; echo "✗ 비밀값으로 보이는 문자열이 있습니다"; fail=1; }
# One find per batch, not per file. "./" keeps a leading "-" from reading as an option; find without -L sizes a
# link itself, as before. +5M is find's rounding: more than 5 MiB (5242880 bytes) fails.
big=$(files | xargs -0 sh -c 'for f; do set -- "$@" "./$f"; shift; done; [ $# -eq 0 ] || find "$@" -maxdepth 0 -size +5M -print || exit 255' sh | sed 's|^\./||') \
  || { echo "✗ 추적 파일을 읽지 못해 크기 검사를 끝내지 못했습니다"; fail=1; }
[ -n "$big" ] && { echo "✗ 5MB 넘는 파일: $big"; fail=1; }
data=$(files | tr '\0' '\n' | { grep -E '\.(vcf|bam|cram|fastq|fq|bcf|h5ad|bed)(\.gz)?$' || [ $? -eq 1 ]; }) \
  || { echo "✗ 추적 파일 목록을 읽지 못했습니다"; fail=1; }
[ -n "$data" ] && { echo "✗ 데이터 파일이 포함됨: $data"; fail=1; }
[ $fail -eq 0 ] && echo "✓ 공개 저장소로 올려도 되는 상태입니다"
exit $fail
