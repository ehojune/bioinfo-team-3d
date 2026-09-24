#!/usr/bin/env bash
# Pre-publish check for a PUBLIC repo: secrets, real configs, large or data files.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
if git ls-files --error-unmatch config/labhq.yaml >/dev/null 2>&1 || [ -f config/labhq.yaml -a ! -f .gitignore ]; then
  echo "✗ config/labhq.yaml (실제 토큰이 든 설정)이 올라가려 합니다"; fail=1
fi
files=$(git ls-files 2>/dev/null || find . -type f -not -path "./.git/*")
pat='gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|BEGIN [A-Z ]*PRIVATE KEY'
if echo "$files" | xargs grep -nIE "$pat" 2>/dev/null; then echo "✗ 비밀값으로 보이는 문자열이 있습니다"; fail=1; fi
big=$(echo "$files" | xargs -I{} find {} -maxdepth 0 -size +5M 2>/dev/null || true)
[ -n "$big" ] && { echo "✗ 5MB 넘는 파일: $big"; fail=1; }
data=$(echo "$files" | grep -E '\.(vcf|bam|cram|fastq|fq|bcf|h5ad|bed)(\.gz)?$' || true)
[ -n "$data" ] && { echo "✗ 데이터 파일이 포함됨: $data"; fail=1; }
[ $fail -eq 0 ] && echo "✓ 공개 저장소로 올려도 되는 상태입니다"
exit $fail
