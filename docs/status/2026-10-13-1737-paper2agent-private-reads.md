## 결론

PR #553 보안 P1: plugin·recruiter skill을 열어도 상위 설정 폴더는 계속 보호한다.

## 바뀐 것

- runner·doctor의 plugin 경로를 `keep`에서 `open_reads`로 옮겼다.
- `CODEX_HOME` 전체 예외를 없앴다. Codex elevated profile은 홈을 deny하고 skill만 read로 연다.
- Claude의 작업별 저장 출력 읽기는 유지한다. 형제 로그인·history·다른 task·settings·todos는 Read 거부, Bash 승인 대상이다.
- MCP 경로는 `keep`에 추가하지 않는다. 계약 skill은 작업 폴더로 복사하므로 상위 보호 폴더를 열지 않는다.

## 검증

- 새 회귀 10건: `0257229`에서 모두 실패, 수정 뒤 모두 통과. Read/Bash·두 엔진·일반 plugin·recruiter를 검사했다.
- 관련 pytest: 317 passed·1 skipped. skill 읽기 허용과 쓰기 거부도 확인했다.
- Windows 전체 pytest: **5639 passed·66 skipped**, 692 warnings.
- Codex profile의 하위 경로 예외는 [공식 permissions 규칙](https://developers.openai.com/codex/permissions)에 맞췄다.
- 공개 검사·diff 검사 통과. 최종 CI는 PR checks에서 확인한다.

## 미해결

- 전용 pytest 임시 폴더 삭제는 실행 정책에 막혀 남았다.
- 실제 직원 CLI·PI·trial은 실행하지 않았다. Codex 검증은 생성한 permission profile까지다.

Refs #549

🤖 Generated with Codex (model slug unavailable) for the labhq dev lead
