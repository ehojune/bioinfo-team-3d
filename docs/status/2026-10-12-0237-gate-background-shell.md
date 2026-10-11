## 2026-10-12 · 승인 게이트: 백그라운드 셸 도구 기본 허용 (#518 후속)

- 결론: #518 뒤 분류되지 않은 도구는 PI에게 묻는다. Claude 직원이 백그라운드로 돌린 Bash의 출력을 읽거나 멈추는 도구(BashOutput·KillShell, 새 판 TaskOutput·TaskStop)도 그렇게 돼, 긴 분석에서 의미 없는 카드가 쌓일 수 있었다. 이 넷을 기본 허용 목록에 넣었다. 셸 명령 자체는 이미 Bash 게이트를 거쳤다.
- 바뀐 것: `labhq/settings.py` `_default_auto_allow`, `config/labhq.example.yaml`(패키지 사본도 같게), `docs/manual.md` 안전 장치 문장, `tests/test_policy.py`.
- 실행한 것: test가 main 코드에서 실패하고 이 branch에서 통과. trial 이벤트에서 Claude 직원 도구 이름을 세어 나머지(Bash·Read·Edit·Write·PowerShell·StructuredOutput·WebFetch·Grep)는 이미 허용 목록이나 파일·셸 규칙에 있음을 확인. 정책·init test, 전체 `pytest -q`(Windows), `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: PI·trial 설정은 `auto_allow_tools`를 직접 적지 않아 기본값을 쓴다(확인함).
- 근거: trial gateway 이벤트 `agent.tool` 집계, `labhq/policy.py` `_evaluate_tool`.
