## 결론

PR #568의 P1(4257865735)을 `03c3415`에서 수정했다. 준비 중 정지했다 재개하면 CLI 실행 직전에 입력 hash를 갱신한다.

## 바뀐 것

- spawn 직전 pause gate를 유지하고 project·upstream 입력을 다시 스캔한다. gate를 앞으로 옮길 때 생기는 준비 중 pause 누락을 피한다.
- 스캔 중 새 pause가 들어오면 다시 기다린 뒤 스캔한다. 실행 중 입력 변경은 갱신한 hash를 보존하고 `changed_during_step`으로 표시한다.
- PR의 기존 세션 id·재개 안내·steer 재전송·빠른 재개 보존 수정도 유지한다.

## 확인

- 회귀 4건: `7363c30`에서 모두 실패, 수정 후 통과. 실제 합성 CLI가 읽은 내용과 manifest hash를 비교한다.
- 관련 pytest: **61 passed**. Windows 전체: **5,993 passed, 66 skipped**(11분 53초).
- 공개 검사 통과. 패치노트·목차 원본·diff 검사 후 push한다. CI는 최종 head의 각 job을 확인한다.

## 미해결

- basetemp 한 폴더의 삭제가 자동 승인 검토에서 `blocked by policy`로 차단됐다. 경로는 로컬 보고에 남긴다.
- 리뷰 요청·병합·PI/trial 실행과 재시작은 하지 않는다.

Refs #59

🤖 Generated with Codex (gpt-6) for the labhq dev lead
