# STATUS — labhq

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).

## 2026-09-25 · PR #3 리뷰 수정 · 스킨 scale과 상태 표지 분리

- 한 일: glTF의 named/추정 head·양손·label 앵커를 scene-space proxy로 제공. world 위치·회전만 따라가고 scale은 1로 유지한다. 손 포즈는 원본 노드를 움직이며 proxy는 숨김·dispose도 따른다.
- 회귀 확인: scale 0.01·1·5 × 앵커 유/무, 108포즈 표본 통과. 표지 부품 크기는 procedural 대비 1.0배(오차 <1e-7), world AABB 대각선은 포즈 차이로 0.975–1.031배. 콘솔 오류·외부 요청 0.
- 테스트: Windows: lab3d 10 passed; 전체 11 failed·25 passed, 기존 실패 11개 ID 동일. 기본 procedural 사무실 9 drawCalls/69,382 triangles 유지. `bash scripts/check_public.sh` 통과.
- 바꾼 파일: `src/skins.js`, `lab3d/README.md`, `lab3d/SKINS.md`, `STATUS.md`. 브라우저 QA와 상세 로그는 로컬 `.pr-drafts/p4-3d-artlab/review3/`.
- 막힌 점: 이번 지적 없음. Linux·실기 브라우저는 UNVERIFIED. push·merge는 수행하지 않는다.

## 2026-09-25 · P4 선행 · 3D 아트 랩 (Codex gpt-6-astra, 게이트웨이 미연결)

- 한 일: 원본 종이숲의 12석·캐릭터 형상을 유지해 독립 lab3d로 이관. procedural/glTF 스킨 계약, 공용 상태 표지, 폰 이름표·승인 칩, 파견직 최대 4명과 모자 배지 추가.
- 바꾼 파일: `labhq/web/lab3d/`, `labhq/web/vendor/three/`, `tests/test_lab3d.py`, `STATUS.md`.
- 테스트: Windows Python 3.12: 새 pytest 10 passed. 전체 11 failed · 25 passed로 기존 실패 11개의 ID가 기준선과 같다. 공개 검사 통과.
- 브라우저: Chrome 153 headless · 390×844 CSS px · DPR 2: 12종×6상태, glTF/GLB, 클립·앵커 누락, 실패 복구, 파견직, 폰 탭·승인 순회, 480/481px 경계, reduced-motion 확인. 페이지 콘솔 오류·외부 요청·가로 넘침 0.
- 막힌 점: GPU 자식 프로세스가 샌드박스에서 종료돼 SwiftShader로 측정했다. 수치는 해당 실행의 표본이다. Linux·iPhone Safari 실기 성능/발열·외부 rigged 모델은 UNVERIFIED.
- PI 결정: 3D로 간다. 두 아트 시안 중 gpt-6-astra 시안(캐릭터·배치)을 골랐다. 캐릭터는 나중에 외부 3D 모델로 바꿀 수 있어야 한다.
- 의존성: three.js 0.186.1(MIT)을 `labhq/web/vendor/three/`에 벤더링했다. 세 자문(gpt-6-sol, gpt-6-astra, Gemini)이 모두 권한 방식이다. 되돌리려면 이 커밋 하나를 revert한다.
- 다음: `/3d` 서빙과 본 구현은 P1–P3 뒤 P4에서 한다. 기존 2.5D 사무실과 게이트웨이는 이 PR에서 바뀌지 않는다.
- 근거: 로컬 `.pr-drafts/p4-3d-artlab/`의 스크린샷 3장·검증 로그. PR 첨부는 요청자가 진행한다.

| 화면 | drawCalls | triangles | fps |
|---|---:|---:|---:|
| 사무실 | 9 | 69,382 | 39 |
| 80px 도감 | 6 | 69,316 | 54 |
| CSO glTF 교체 | 13 | 65,694 | 37 |
| 파견직 3명 | 9 | 75,970 | 35 |

## 2026-09-25 · P0+ CI (Codex)
- 한 일: 모든 브랜치 push·PR·수동 실행에서 Ubuntu Python 3.10·3.12 pytest를 돌리는 CI 추가. README에 배지와 저장소 주소 추가.
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
