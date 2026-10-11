## 2026-10-12 · 승인 게이트의 native 위임과 Codex 확장 기능 차단

## 결론

분류되지 않은 도구는 PI에게 묻고, Claude Code native 위임은 일반 직원에게서 거부합니다. Codex 쓰기 직원은 multi-agent·memory·apps·plugins를 모두 끕니다.

## 바뀐 것

- `policy._evaluate_tool`: `Task`·`Agent`·`Workflow`·`TeamCreate`·`TeamDelete`를 설정값보다 먼저 deny. 알려진 비위임 도구만 `auto_allow_tools`에 둠.
- Claude Code: 일반 직원은 native 위임을 거부하고, 설정된 recruiter가 `Agent`를 선언한 Paper2Agent 변환만 예외.
- Codex: 쓰기·읽기 직원 모두 `multi_agent`·`memories`·`apps`·`plugins`를 `--disable`.
- manual과 두 example config를 맞추고, example config 두 벌의 바이트 동일성을 test로 고정.

## Codex 기능 확인

설치본에서 `codex features list`로 확인했습니다.

```text
codex-cli 0.160.1
apps          stable  true
memories      stable  true
multi_agent   stable  true
plugins       stable  true
```

## 실행한 것

- 수정 전 새 회귀: 9 failed.
- 관련 test: 182 passed, 4 skipped.
- Windows 전체 pytest: 첫 실행은 바뀐 `Task` 기대값 1건 실패, 기대값을 새 정책에 맞춘 뒤 4989 passed, 63 skipped.
- `bash scripts/check_public.sh`, `git diff --check` 통과.

## 미해결

없음.

## 근거

- `labhq/policy.py`
- `labhq/adapters/claude_code.py`
- `labhq/adapters/codex.py`
- `labhq/settings.py`
- `tests/test_policy.py`
- `tests/test_isolation.py`
- `tests/test_read_only_profile.py`
- `tests/test_intake_references.py`
- `docs/manual.md`

Fixes #421

🤖 Generated with Codex (gpt-5) for the labhq dev lead
