# 직원 세션의 GitHub 신원 — #813

이 PC의 gh 래퍼(`~/bin/gh` → `gh-agent-env`)와 commit-msg hook은 `LABHQ_COMMIT_AGENT`를 먼저 읽는다. 값이 없으면 CLI 세션 변수로 에이전트를 추정한다(`CLAUDECODE` → claude, `CODEX_SANDBOX*`·`CODEX_THREAD_ID` → codex). 래퍼는 이어서 그 gh 호출에 봇의 token을 `GH_TOKEN`으로 넣고, hook은 그 에이전트의 trailer를 붙인다. labhq 직원으로 실행되는 Claude Code·Codex도 자기 tool shell에 이 세션 변수를 넣는다. 그래서 래퍼가 PATH에 있으면 직원이 shell에서 부른 gh가 봇 신원과 봇 token을 얻을 수 있었고, #301에서 직원 env의 GitHub token을 지운 효과가 무력해질 수 있었다. 이 PC에 `~/bin/git` 래퍼는 없다.

PI 정책(직원은 ehojune 신원, 개발 세션은 각 봇)에 따라 `AgentAdapter.staff_env`는 제품 직원 자식 환경의 최종 값을 `LABHQ_COMMIT_AGENT=none`으로 고정한다. 상속값, `engines.<engine>.env`, task env의 값과 대소문자가 다른 같은 이름은 지운 뒤 하나만 넣는다. 읽기 전용 직원도 같다. #301의 GitHub token 이름 제거와 상위 `CLAUDE*`/`CODEX_*` 정리는 그대로이며, runner의 `os.environ`과 입력 dict는 바꾸지 않는다.

`none`은 래퍼의 봇 token 주입과 hook의 에이전트 trailer를 끄는 표식일 뿐이다. 사용자 매뉴얼에는 GitHub 토큰 문단에 한 문장만 둔다. 사용자 keyring이나 `gh auth login`으로 저장된 로그인 같은 사용자 권한을 없애지 않는다. 직원 shell이 그 저장 로그인을 쓸 수 있는지는 #813 밖의 문제다. 개발용 CLI·lead 루틴·이 PC의 래퍼·OAuth 권한·gateway token은 바꾸지 않았다.

검증: 기존 `tests/test_bioinfo_pipeline_pr.py`에 순수 dict 검사 4건을 더했다(Claude·Codex × 쓰기·읽기 전용). 상속 `claude`, engine env `gemini`(대소문자 다름), task env `codex`, `CLAUDECODE`·`CODEX_THREAD_ID`·`GH_TOKEN`이 있어도 결과에는 `LABHQ_COMMIT_AGENT=none` 하나만 남고, token과 상위 marker는 없으며, 부모 env·입력은 그대로다. 실제 직원 CLI, gh, commit, push, 네트워크 인증은 시험하지 않았고 실제 GitHub 표시 신원도 미확인이다. #813은 부분 해결로 두며, 실제 직원 1회의 표시 확인이나 PI 수용 전에는 닫지 않는다.
