## 결론

통역 표시 작업이 semantics 비교용 연구 기록에 비결정적으로 섞이던 main CI flake를 고쳤습니다. Refs #557.

## 바뀐 것

- `pi_interpretation` task와 lifecycle event, `messenger.translated`를 round 연구 기록에서 제외했습니다.
- raw ledger·메신저 전달·요청 비용 집계·종료 대기는 유지했습니다.
- reconnect snapshot의 작업 목록에서도 표시 전용 task/event를 제외했습니다.

## 확인

- 수정 전 `confirm` 2회째 재현: `messenger.translated.data.message_seq` 84/87 불일치.
- 실패 parameter 3개를 10회 반복: 30 passed, 0 failed.
- 관련 pytest: 152 passed + round/environment 139 passed.
- `web_office_browser.cjs`와 pytest wrapper 통과. CI의 기존 실패는 Chrome 시작 10초 timeout이었고 제품 assertion은 재현되지 않았습니다.
- `scripts/check_public.sh` 통과. 실제 모델·GitHub API test 호출, PI/trial/HPC 실행은 없습니다.

## 남은 지적

- CI 3.10·3.12·Windows 확인 대기.

🤖 Generated with Codex (gpt-5) for the labhq dev lead
