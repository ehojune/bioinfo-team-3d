## 결론

Fixes #657. PR #661. 필수 증거 칸 누락은 한 번 확인한 뒤 출처가 있는 빈칸으로 드러내고, 성공한 독립 단계의 결과는 CP2로 보냅니다.

## 바뀐 것

- 같은 세션에 칸 id → 시도 상태·이유만 질문합니다. 코드가 `slot_repair` 또는 `omitted_by_output` 행을 씁니다. 모두 `unavailable`이며 근거를 지지·반박하지 않습니다.
- 원본 근거가 없는 `attempted_found`는 검증된 발견으로 승격하지 않습니다. 무응답·재개 불가·질문 실패도 출처와 이유를 CP2·reviewer·보고서 한계에 남깁니다.
- 실패 단계의 의존 단계만 건너뜁니다. 성공한 독립 단계가 없으면 요청 실패이며, 예산·PI 승인·frozen PLAN 검사는 유지합니다. environment는 기존 시설팀으로 갑니다.
- 오류 서명·요청 id·단계 id를 기존 dev log 또는 state의 `dev-reports.jsonl`에 남깁니다. PI에게 개발자 연락을 맡기지 않습니다.
- `slot_repair`는 별도 후속 turn schema를 씁니다. 내부 기록 필드 `origin`·`attempt_status`는 최초 step schema에서 제외해 off 경로 계약을 유지합니다.
- manual과 `docs/decisions.md`에 기존 필수 칸 salvage 금지 규칙을 바꾸는 이유와 안전장치를 기록했습니다.

## 확인

- 새 테스트 17건: main `7dd0057`에서 기존 16건 중 **14 failed, 2 passed** → branch **17 passed**. 환경 경로·잘못된 plan binding 거부는 main에서도 통과합니다.
- 관련 pytest **1,158 passed, 1 skipped**. 전체 pytest는 CI에 맡겼습니다.
- CI 실패 재현 뒤 output-types **14 passed**, shadow 제거 **1 passed**. 고정 hash는 갱신하지 않았습니다.
- CP2 Node **7 passed**, `scripts/check_public.sh` 통과. 기존 prompt hash pin 변경 없음.
- manual **149,931 → 150,793자**, 결정 기록 **0 → 612자**.
- patch-notes·notes-index 검사와 push 후 CI 상태는 기록 커밋 후 확인합니다.

## 남은 지적

- 실제 직원 CLI·PI 요청은 실행하지 않았습니다. 리뷰 요청과 병합은 총괄에게 넘깁니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
