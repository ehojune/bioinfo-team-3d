새 설정의 `labhq init`에서 pipeline 코드를 공개 upstream에 PR로 기여할지 묻습니다. `y`(대소문자 무관)만 동의이며 Enter·`--yes`·불분명한 답은 끔으로 저장합니다.

- 기존 동의값과 upstream 대상은 `--force`에서도 보존합니다.
- dry-run은 설정·token·직원 홈을 만들지 않으며 새 instance마다 동의를 따로 받습니다.
- 거절 시 내용 수집·GitHub 호출을 막는 기존 경로를 검증했습니다. fork 자동 생성은 후속 작업입니다.
- 설치와 bioinfo-agent 연결 매뉴얼을 갱신했습니다.

검증: 기준 main `2ffb06d`에서 새 회귀 10 failed·3 passed → 수정 후 관련 129 passed. 공개 검사·diff 검사 통과. 로컬 Windows 전체 pytest는 다른 레인의 전체 suite가 이어서 실행돼 미실행이며, 완료 조건은 아직 충족하지 않았습니다. CI 결과는 현재 head의 checks에서 확인합니다.

Refs #300

남은 지적: fork 지원은 B 레인 후속입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
