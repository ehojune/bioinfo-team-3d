## 결론

Fixes #652. PR #655의 P1 4284969814를 수정합니다. `ShutdownInterrupted`를 일반 `Exception`으로 분리하고 runner만 종료돼도 각 호출자가 중단 상태를 저장하도록 했습니다. 진짜 `asyncio.CancelledError`는 기존 취소 경로를 따릅니다.

기존 변경은 유지합니다: 종료 근거가 있는 요청은 `interrupted`와 재개 카드로 보존하며, 게시 카드는 PI 검토 뒤에만 엽니다. `mark-interrupted` 정정에는 저장된 종료 근거나 실패 직전 gateway 신호 기록이 필요합니다.

## 호출부와 처리

`git grep`의 직접 호출과 콜백 전달을 모두 확인했습니다. 요청 단계는 `run_request()`의 공통 처리에서 실행 중 단계를 정리하고 `_set_status(..., "interrupted")`로 재개 카드를 저장합니다.

| `run_step()` 호출부 | 종료 중단 처리 |
|---|---|
| `run_request`: direct, briefing, 최초·revise·clarify 계획, review, synthesis | 요청 공통 처리로 전파 |
| `run_request`: precedent | 일반 오류 fallback 전에 중단을 다시 던져 요청 공통 처리로 전달 |
| `run_dag.run_one`: 단계 실행·결과 JSON 정정 | 다른 실행 단계를 취소·회수한 뒤 요청 공통 처리로 전달 |
| `_run_solo`: 단독 실행·과학 리뷰 | 요청 공통 처리로 전파 |
| `_research_report`: 연구 리뷰·최종 보고 | 요청 공통 처리로 전파 |
| `prepare_recruits` → `recruit.planning.prepare_plan`: recruit_check·plan_recruit | 실패 receipt로 바꾸지 않고 요청 공통 처리로 전달 |
| `paper_skills.prepare_plan`: paper_skill | 변환 실패로 바꾸지 않고 요청 공통 처리로 전달 |
| `run_followup` | `interrupted`·종료 근거 저장, 다시 질문 허용, live 화면도 중단 상태 유지 |
| `answer_ask` → `_start_ask().route()` | `resolved` 저장, 중단·재시도 안내가 담긴 rejected 응답으로 원 작업의 waiter 해제 |
| `ResultStorage.finish`: result_editor | 저장 상태 `interrupted`; 편집자 runner 재연결 때 schedule. 재연결이 결과 도착 전·마지막 저장 중이어도 재개 |

## 확인

- 기본 회귀: 수정 전 5 failed / 5 passed → 수정 후 10 passed.
- 예외 계층 변경 뒤 발견한 콜백 회귀 3건도 수정 전 실패를 확인했습니다.
- 최종 새 회귀 18건 포함 관련 pytest **732 passed**. 기존 #655 테스트 포함.
- follow-up reducer: 수정 전 중단 상태 assertion 실패 → 수정 후 3 passed.
- 관련 Node **4 passed**. 공개 검사·patch-notes·notes-index 통과.

## 남은 지적

push 뒤 CI를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
