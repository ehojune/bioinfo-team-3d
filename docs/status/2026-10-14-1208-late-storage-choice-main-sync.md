## 결론

PR #583의 늦은 저장 선택과 main #580·#585의 수정 사항을 함께 보존했습니다. Refs #558.

## 바뀐 것

- 검토 뒤 실행, 카드 재사용·재시작 복원, 취소된 부분 결과, 기한 없는 선택을 유지했습니다.
- 편집자 실패 시 기본 정리본, 저장 선택 approval 처리 순서, 게시 파일 단일 commit을 유지했습니다.
- 검토할 수 없는 `completed` 상태는 API·웹의 늦은 저장 허용 목록에서 뺐습니다(리뷰 4266335778).
- Node 자동 수집 51개에 main 명시 목록 50개가 모두 포함됩니다.

## 확인

- Windows 관련 pytest: 141 passed, 1 skipped. GitHub 호출은 mock만 사용했습니다.
- `for f in tests/*.cjs; do node --test "$f"; done`: 51개 파일 통과.
- 로컬 전체 pytest: ROUNDSIZE 전체 suite 실행 중이라 지시된 예외에 따라 미실행했습니다.
- push 뒤 CI 결과는 PR 본문에 기록합니다.

## 남은 지적

- 로컬 전체 pytest 미실행. PI/trial 실행·재시작, 리뷰 요청·답글, PR 병합은 하지 않았습니다.
