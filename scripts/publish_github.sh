#!/usr/bin/env bash
# One-shot: create a PUBLIC GitHub repo from this folder and push it.
#   ./scripts/publish_github.sh            → <your-account>/labhq
#   ./scripts/publish_github.sh my-lab-hq  → <your-account>/my-lab-hq
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:-labhq}"
command -v gh >/dev/null || { echo "GitHub CLI가 필요합니다: https://cli.github.com (brew install gh / conda install -c conda-forge gh)"; exit 1; }
gh auth status >/dev/null 2>&1 || gh auth login
[ -d .git ] || git init -b main
git add -A
./scripts/check_public.sh
git commit -m "labhq v0.2: multi-agent bio lab — web office, GitHub project updates, contract agents" || true
if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin main
else
  gh repo create "$NAME" --public --source . --remote origin --push \
    --description "Bio lab HQ: Claude Code · Codex · Gemini agents as a bioinformatics lab — CSO orchestration, HPC (SGE/PBS), Paper2Agent contract agents, live web office"
fi
gh repo view --web >/dev/null 2>&1 || true
echo "✓ 올렸습니다: $(gh repo view --json url -q .url)"
