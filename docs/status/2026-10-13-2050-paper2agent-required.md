## 변경

PR #553의 Paper2Agent 직원 설치 기능을 main `1b3b0cb`와 병합했다. 충돌한 doctor 인자에는 Paper2Agent 검사·수정 옵션과 GitHub 연결 검사 옵션을 모두 남겼다.

- 직원 전용 설치, plugin·skill 읽기 예외, 설치 commit pin, Codex recruiter 조건부 필드를 보존했다.
- 사용자 홈 접근 guard와 main의 기여 동의·기타 변경을 보존했다.
- 병합 커밋 `f912336`의 패치노트 SHA는 따옴표로 감싼 7자리 문자열이다.

## 검증

- 관련 테스트: 427 passed, 1 skipped.
- Windows 전체 pytest: 6097 passed, 66 skipped (751.84초).
- pytest 임시 폴더 삭제 완료.
- 공개·diff·패치노트·목차 검사를 수행하며, 원격 CI 결과는 PR checks에 기록한다.

## 남은 지적

- 전체 테스트는 통과했지만 subprocess 출력 디코딩의 thread warning이 1건 있었다. 이번 충돌 해소 범위에서는 수정하지 않았다.
- 실제 직원 CLI·PI·trial 실행, 리뷰 요청 댓글, PR 병합은 하지 않았다.

Refs #549

🤖 Generated with Codex (gpt-6) for the labhq dev lead