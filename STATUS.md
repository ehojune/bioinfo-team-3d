# STATUS — labhq

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).

## 2026-09-25 · P0+ CI (Codex)
- 한 일: push(main)·PR·수동 실행에서 Ubuntu Python 3.10·3.12 pytest를 돌리는 CI 추가. README에 배지와 저장소 주소 추가.
- 테스트: `bash scripts/check_public.sh` 통과. Windows 11(Python 3.12) `pytest -q`는 main db49ed4와 같은 11 failed · 15 passed. Linux 26 passed는 WSL·CI에서 별도 확인.
- 다음: P1 실제 CLI — 첫 실계정 실행 전 ⛔.

## 2026-09-25 · P0 인수인계 문서 (Codex)
- 한 일: 부록 A 파일 5개 추가, 버전 0.2.0 → 0.2.1, .gitignore에 로컬 전용 노트 파일 2개 추가
- PI 결정: 엔지니어 에이전트는 Codex. 웹 사무실은 최종적으로 3D로 간다 (설계 제안은 별도 PR).
- PI 위임: Codex 리뷰 봇과 PR당 최대 10회 주고받고, 합의된 PR은 Claude가 병합한다.
- 테스트: Linux(WSL Ubuntu 22.04, Python 3.10) 26 passed. Windows 11(Python 3.12)은 main db49ed4와 같은 11 failed · 15 passed.
- 막힌 점: Windows 전용 실패 11개는 기본 인코딩 cp949(4개, PYTHONUTF8=1로 풀림), fake CLI 셸 스크립트 실행 WinError 193(4개), 경로 구분자 가정(3개).
- Windows 경로 문제: 통제 경로 Read가 deny 대신 allow, Claude 권한 규칙이 `Read(/\data\cohort/**)`로 깨짐, 공개 가드가 `/data/cohort`를 가리지 못함. Linux 러너 대상이라 이번 범위 밖이지만 Windows 러너에서는 데이터 구역 가드가 작동하지 않음.
- 다음: P0+ CI PR (이 PR 위에 쌓음)

## 2026-09-25 · P0 GitHub 저장소 (PI, 확인: Claude)
- ehojune/bioinfo-team-3d에 public으로 올림 (main db49ed4, v0.2.0, 파일 62개)
- 확인: main에서 pytest 26 passed, 비밀값·실제 설정 파일 없음 (검사에 걸린 두 곳은 탐지용 정규식 문자열)
- 인수인계본(v0.2.1)과의 차이: 버전 표기와 인수인계 문서뿐, 코드는 같음
- 다음: handoff-docs PR → P0+ CI → P1
- 결정 대기: HANDOFF.md 맨 아래 목록
