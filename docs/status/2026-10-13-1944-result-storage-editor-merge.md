## 결론

PR #566에 main `1b3b0cb`를 병합했다. 두 화면의 저장 위치 카드와 검토 흐름을 보존하고 PI 검토·확인 완료 뒤 게시 버튼을 활성화한다. 시설팀과 편집자를 함께 등록하고 배지·직원 표를 갱신했다.

## 확인

- 최신 main 재병합 뒤 관련 Python 307 passed, 3 skipped. Node 44개 파일 통과.
- 정규직 13명·3D 등록 14개 검증 갱신 뒤 관련 112 passed, 1 skipped.
- CI에서 편집자 task를 고르던 기존 회귀를 direct task 선택으로 수정. 관련 29 passed, 2 skipped; 검증 조건은 유지.
- Windows 전체 pytest: 6,026 passed, 66 skipped (11분, 첫 merge 직후).
- Node 실행 파일을 PATH 앞에 두어 격리 홈에서 실행 경로가 끊기지 않게 했다.
- 공개·패치노트·목차·diff 검사 통과. 현재 head CI 결과는 PR checks에 기록한다.

## 미해결

- 임시 폴더 삭제는 자동 승인 검토의 `blocked by policy`로 차단됐다.
- 리뷰 요청·PR 병합·PI/trial 실행은 하지 않는다.

Refs #558

🤖 Generated with Codex (gpt-6) for the labhq dev lead
