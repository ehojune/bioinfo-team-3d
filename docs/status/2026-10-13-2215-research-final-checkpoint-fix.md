## 결론

PR #573의 CP4 GitHub 전송 시점(P1)과 기존 bundle 갱신 예약(P2)을 `bbcf48d`에서 수정했습니다.

## 바뀐 것

- 검토 대기 초안은 GitHub terminal delivery를 만들지 않습니다. 옛 대기 이벤트도 게시하지 않습니다.
- 최종 결정과 전송 기록을 함께 저장합니다. 수용 뒤 보고서 commit·issue 종료, 거부·만료 뒤 실패 알림·issue 유지로 처리합니다. 재시작 시 남은 전송을 복구합니다.
- `workdir_id`가 없어도 기존 bundle이나 미완료 생성 기록이 있으면 receipt 갱신을 예약합니다.
- main `363cdd4`를 병합했습니다. 패치노트는 기존 들여쓰기와 따옴표로 감싼 7자리 SHA를 유지합니다.

## 확인

- `3392480`에서 새 회귀 5개 실패 → 수정 후 관련 68개 통과. GitHub는 mock transport만 사용했습니다.
- Windows 전체 pytest: 미실행. 다른 레인의 전체 실행이 연속돼 약 40분 대기 후 중단했습니다. 동시 전체 실행 금지를 지켰습니다.
- 공개 검사 통과. 패치노트·목차·diff 검사는 기록 커밋 뒤 실행합니다.

## 남은 지적

- P2는 가져온 report-only bundle의 재생성 예약·receipt 검증을 mock으로 확인했습니다. 현재 원본 탐색기는 모든 `workdir_id`가 없으면 bundle을 직접 복원하지 못합니다. 원본 복구 기능은 이번 수정에 추가하지 않았습니다.
- Windows 전체 통과 조건이 남아 push·인라인 답글·현재 수정 head의 CI 확인을 보류했습니다. 총괄이 전체 pytest 실행 순서를 확보한 뒤 이어갑니다.
- PI/trial 실행·재시작, 실제 보고서 게시, 리뷰 요청, 병합은 하지 않았습니다.

Refs #90

🤖 Generated with Codex (gpt-6) for the labhq dev lead
