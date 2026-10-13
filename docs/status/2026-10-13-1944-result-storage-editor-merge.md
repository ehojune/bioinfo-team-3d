## 결론

PR #566에 main `3ee0d64`를 병합했다. 두 화면의 저장 위치 카드와 검토 흐름을 보존하고 PI 검토·확인 완료 뒤 게시 버튼을 활성화한다.

## 확인

- 관련 Python 102 passed, 1 skipped. Node 43개 파일 통과.
- Windows 전체 pytest: 6,026 passed, 66 skipped (11분).
- Node 실행 파일을 PATH 앞에 두어 격리 홈에서 실행 경로가 끊기지 않게 했다.
- 공개·패치노트·목차·diff 검사 통과. 현재 head CI 결과는 PR checks에 기록한다.

## 미해결

- 임시 폴더 삭제는 자동 승인 검토의 `blocked by policy`로 차단됐다.
- 리뷰 요청·PR 병합·PI/trial 실행은 하지 않는다.

Refs #558

🤖 Generated with Codex (gpt-6) for the labhq dev lead
