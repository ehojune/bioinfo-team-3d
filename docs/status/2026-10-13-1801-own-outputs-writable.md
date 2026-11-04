## 결론

읽기 전용 직원도 자기 task의 `outputs/`·`.tmp/`에 파일을 저장합니다. 검토 대상과 앞 단계 산출물은 계속 보호합니다.

## 바뀐 것

- Claude: Write·Edit를 승인 게이트로 보내 자기 산출·임시 파일만 승인 없이 허용합니다. 셸과 직원 MCP는 열지 않습니다.
- Codex: 읽기 기본 권한에 두 쓰기 루트만 추가한 workspace-write profile을 씁니다. 프로젝트 `--add-dir`와 승인 승격은 끕니다.
- 리뷰·상담·이어 묻기와 역할별 읽기 제한에 같은 정책을 적용합니다. 이어 묻기는 세션을 유지하고 새 작업 폴더에 씁니다.
- 리뷰는 `outputs/review.json`과 최종 JSON 응답을 함께 남깁니다. 기존 산출 수집과 모의 리뷰어도 맞췄습니다.
- 매뉴얼 권한 표·안전 장치 설명을 갱신했습니다(118,283 → 118,352자).

## 검증

- 새 회귀 17건: `origin/main` 11845c0에서 17 failed, branch에서 전부 통과.
- 관련 pytest: 146 passed·1 skipped. 입력·앞 단계·다른 task·프로젝트·통제 구역과 링크·하드링크 쓰기 거부, 검토 대상 변경 감지, 새 폴더·기존 세션 유지 확인.
- Windows 전체 pytest: 5,896 passed·66 skipped (모의 e2e 포함, 10분 9초).
- `scripts/check_public.sh`, `git diff --check`: 통과.
- Codex는 sandbox 인자·쓰기 루트 구성, Claude는 실제 승인 함수까지 검사했습니다. 실제 직원 CLI·PI/trial instance는 실행하지 않았습니다.

## 남은 지적

- Gemini·Antigravity·CLI는 읽기 전용 task의 쓰기 경계를 강제하지 못해 기존 실행 거부를 유지합니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead
