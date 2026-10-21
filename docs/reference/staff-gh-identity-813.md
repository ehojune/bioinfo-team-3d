# 직원 세션의 GitHub 신원 — #813

이 PC의 gh/git 래퍼(`~/bin/gh`, `gh-agent-env`)와 `.githooks/commit-msg`는 `LABHQ_COMMIT_AGENT`를 먼저 읽는다. 값이 없으면 CLI 세션 변수로 에이전트를 추정한다(`CLAUDECODE` → claude, `CODEX_SANDBOX*`·`CODEX_THREAD_ID` → codex). 래퍼는 이어서 그 봇의 token을 `GH_TOKEN`으로 shell에 넘긴다. labhq 직원이 띄운 Claude Code·Codex도 자기 tool shell에 이 세션 변수를 넣으므로, 직원이 shell에서 gh를 부르면 봇 신원이 되고 #301에서 지운 GitHub token이 다시 주어질 수 있었다.

PI 정책(직원은 ehojune 신원, 개발 세션은 각 봇)에 따라 `AgentAdapter.staff_env`는 제품 직원 자식 환경의 최종 값을 `LABHQ_COMMIT_AGENT=none`으로 고정한다. 상속값, `engines.<engine>.env`, task env의 값과 대소문자가 다른 같은 이름은 지운 뒤 하나만 넣는다. 읽기 전용 직원도 같다. #301의 GitHub token 이름 제거와 상위 `CLAUDE*`/`CODEX_*` 정리는 그대로이며, runner의 `os.environ`과 입력 dict는 바꾸지 않는다.

`none`은 래퍼의 봇 token 주입과 hook의 에이전트 trailer 추정을 끄는 표식일 뿐이다. 사용자 keyring이나 `gh auth login`으로 저장된 로그인 같은 사용자 권한을 없애지 않는다. 직원 shell이 그 저장 로그인을 쓸 수 있는지는 #813 밖의 문제다. 개발용 CLI·lead 루틴·이 PC의 래퍼·OAuth 권한·gateway token은 바꾸지 않았다.

검증: 기존 `tests/test_bioinfo_pipeline_pr.py`에 순수 dict 검사 4건을 더했다(Claude·Codex × 쓰기·읽기 전용). 상속 `claude`, engine env `gemini`(대소문자 다름), task env `codex`, `CLAUDECODE`·`CODEX_THREAD_ID`·`GH_TOKEN`이 있어도 결과에는 `LABHQ_COMMIT_AGENT=none` 하나만 남고, token과 상위 marker는 없으며, 부모 env·입력은 그대로다. 실제 직원 CLI, gh, commit, push, 네트워크 인증은 시험하지 않았다. 실제 직원 1회의 표시 확인이나 PI 수용 전에는 #813을 닫지 않는다.
