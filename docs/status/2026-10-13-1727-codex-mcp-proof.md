## 결론

실패 MCP probe가 exit 1을 반환한다. 실제 Codex 90초 지연 호출도 재시도 없이 한 번 완료돼 #276의 남은 실측을 채웠다.

## 바뀐 것

- summary·진단을 보존하고 `ok`·정확한 marker·서버/도구 호출 각 1회·tool error 0을 모두 검사한다.
- Claude inbound는 sender 성공, refuse의 거부 통지·로그, accept/unset의 큐 기록을 별도로 판정한다. `--no-sender`는 MCP만 검사한다.
- task timeout을 summary에 기록하고 매뉴얼의 MCP·inbound 설명을 갱신했다.
- 개발 기준 `f0cbb32`: triage `70f5b72` 이후 #547·#532·#551·#552 반영. 해당 probe 파일에 선행 변경은 없다.

## 실행한 것

| 검사 | 결과 |
|---|---|
| 새 회귀 29건 | 기준 코드 29 failed → branch 통과 |
| 관련 pytest | 37 passed, 1 skipped |
| Windows 전체 pytest | 5,648 passed, 66 skipped (554.79초) |
| 실제 Codex | 0.162.0-alpha.17.2 · 기존 직원 모델 gpt-5.6-sol · 105.4초 · exit 0 |
| 실제 호출 | 지연 90초 · 승인 60초 · MCP 1,320초 · task 1,800초 · 서버/도구 각 1회 · error 0 |
| 공개 검사 | 통과 |

전용 설정·임시 작업 폴더와 기존 직원 인증으로 한 번 실행했다. workspace-write/elevated 격리를 유지했고 PI·trial gateway/runner에는 연결하지 않았다. raw stream·개인 경로는 로컬에만 보관한다. Claude 실제 inbound는 #286의 기존 근거이며 이번 실행은 Codex다.

## 남은 지적

- #286의 sender launcher 지원 범위(P2)는 그대로다. 봇 리뷰 판단과 병합은 총괄이 이어받는다.

Fixes #276

🤖 Generated with Codex (gpt-6) for the labhq dev lead
