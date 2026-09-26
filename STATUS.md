# STATUS — labhq

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).

## 2026-09-26 · P1+ ④ 직원 CLI 개인 설정 격리
- 한 일: 어댑터가 PI 개인 CLI 설정을 빼고 직원 CLI를 띄운다(`engines.*.isolate_user_config`, 기본 켜짐). `engines.*.env`가 실제로 전달되게 고쳤다(전에는 무시됐다).
- 실측(Windows 11, 실제 계정): 개인 지침에 있는 단어를 묻는 질문으로 확인했다.
  - Claude 2.1.282: 기존 명령은 skill 55·plugin 6·개인 서브에이전트 3·SessionStart hook·전역 CLAUDE.md를 불러왔다. 격리 후 skill 0, 내장 plugin 2, 내장 서브에이전트 6, hook 0, 전역 지침 없음. 같은 질문의 입력 토큰이 약 12k 줄었다.
  - Codex 0.155: `--ignore-user-config --ignore-rules`로 config.toml의 plugin·notify hook·MCP가 빠졌다(입력 24k → 15.5k 토큰). 전역 AGENTS.md는 남는다. `project_doc_max_bytes=0`, `features.agents_md=false`로도 안 빠졌다.
  - Codex on Windows: config.toml을 건너뛰면 `[windows] sandbox`도 빠져 파일 쓰기가 막히는데 종료 코드는 0이었다. `windows.sandbox="elevated"`를 다시 넣어 쓰기를 확인했다.
  - agy 1.2.11: 격리 전에도 전역 지침을 읽지 않았다. `--disable-slash-commands`는 과학 DB skill까지 끌 수 있어 넣지 않았다.
- 가림 보강: Claude `rate_limit_event`에 요금제 사용률·재설정 시각·조직 초과사용 설정이 있었다. main의 Claude fixture 7개에 들어 있던 것을 가리고 구조 테스트를 붙였다.
- 테스트: Windows는 기존 실패 10개 그대로, 새 테스트 9개 통과. Linux는 CI.
- 막힌 점: Codex 전역 AGENTS.md는 직원용 `CODEX_HOME` 로그인(PI 조치)이나 러너 전용 계정으로만 빠진다. 직원용 로그인 방식은 미검증.
- 다음: P1+ 나머지 갈래(② CSO, ③ 데이터 경계, ①⑤ 영속화·이벤트 순번) 병합.

## 2026-09-26 · P1 실제 CLI 연동 (Claude·Codex·agy·Gemini)
- 버전: claude 2.1.282 · codex-cli 0.155.0-alpha.16 · gemini-cli 0.57.0 · agy 1.2.11 (Windows 11 호스트, 실제 계정)
- 한 일: 실측 스트림 19개를 가려 `tests/fixtures/real/`에 넣고 파서 테스트를 붙였다. 조용한 실패 세 가지를 막았다: Claude의 `is_error: true`+`subtype: success`, Gemini CLI의 빈 stdout+종료 코드 0, agy의 권한 거부(`denied_actions`에만 남음).
- Codex: `exec` 기본 승인 정책 `never`가 MCP 호출을 실패시킨다(#24135 재현). labhq 내장 MCP 서버에만 `default_tools_approval_mode="approve"`를 붙인다. 승인은 도구 안의 폰 승인이 맡는다.
- Gemini: 개인 계정은 Gemini CLI 지원이 끝났다(`IneligibleTierError`). 새 `antigravity` 엔진을 추가하고 여우 문헌 담당을 옮겼다. agy에는 호출 단위 승인 훅이 없어 통제 데이터에 닿는 직원에게 쓰지 않는다.
- Claude: labhq 게이트웨이·러너를 거친 직접 요청이 정상 응답했다. 승인 도구가 실제 Claude와 호환되지 않던 결함을 고쳤다(mcp 2.x의 구조화 결과 때문에 거부). 수정 후 작업 폴더 안 쓰기는 바로 허용, 밖 쓰기는 폰 승인 요청 → 승인하면 기록, 거부하면 차단을 확인했다.
- Claude deny 규칙: Windows에서는 `Read(//c/Users/...)` 형식만 막힌다. 정책이 `C:\...`를 이 형식으로 바꾸게 고쳤고, 가짜 비밀 파일 읽기가 막히는 것을 실제 계정으로 확인했다.
- 도구: `scripts/probe_engines.py`(실측 재캡처), `scripts/redact_stream.py`(경로·계정·ID 가림, init 이벤트는 허용 목록 필드만).
- 테스트: Linux(WSL Ubuntu 22.04, Python 3.10) 70 passed. Windows는 기존 실패 11개 중 10개가 남았다(Claude 규칙 테스트는 이번 경로 수정으로 통과), 새 실패 없음. agy 어댑터는 실제 계정으로 한 번 돌려 확인했다.
- 막힌 점: 헤드리스 CLI가 PI 개인 설정(hook·서브에이전트·config·전역 지침)을 불러온다. 한 줄 응답에 $0.26이 든 것도 이 때문으로 보인다(UNVERIFIED). P1+에서 격리한다. Codex CLI는 OpenAI 쪽 401 오류로 현재 쓸 수 없다.
- 다음: P1+ 안전·복구 최소선.

## 2026-09-26 · 로드맵 갱신 (PI 결정)
- 한 일: HANDOFF [3]에 P1+ 안전·복구 최소선을 넣었다. P1 다음, P2·P3 전에 상태 영속화·CSO 실패 전파·통제 데이터 경계·CLI 개인 설정 격리·이벤트 순번을 한다.
- P4를 실사용 점검 + 3D 도입으로 고쳤다. 3D 사무실(#3)을 `/3d`로 연결하고 2.5D와 함께 둔다.
- 근거: 세 자문(gpt-6-sol, gpt-6-astra, Gemini 3.8 Flash) 설계 검토. 셋 다 교체가 아니라 수정이라고 판정했다.
- 다음: P1 실제 CLI 연동.

## 2026-09-25 · PR #3 리뷰 수정 · 스킨 scale과 상태 표지 분리

- 한 일: glTF의 named/추정 head·양손·label 앵커를 scene-space proxy로 제공. world 위치·회전만 따라가고 scale은 1로 유지한다. 손 포즈는 원본 노드를 움직이며 proxy는 숨김·dispose도 따른다.
- 회귀 확인: scale 0.01·1·5 × 앵커 유/무, 108포즈 표본 통과. 표지 부품 크기는 procedural 대비 1.0배(오차 <1e-7), world AABB 대각선은 포즈 차이로 0.975–1.031배. 콘솔 오류·외부 요청 0.
- 테스트: Windows: lab3d 10 passed; 전체 11 failed·25 passed, 기존 실패 11개 ID 동일. 기본 procedural 사무실 9 drawCalls/69,382 triangles 유지. `bash scripts/check_public.sh` 통과.
- 바꾼 파일: `src/skins.js`, `lab3d/README.md`, `lab3d/SKINS.md`, `STATUS.md`. 브라우저 QA와 상세 로그는 로컬 `.pr-drafts/p4-3d-artlab/review3/`.
- 막힌 점: 이번 지적 없음. Linux·실기 브라우저는 UNVERIFIED. push·merge는 수행하지 않는다.
- 두 번째 지적: 메시를 움직이는 손 노드·bone을 L/R 이름으로 추정하고, 한쪽이라도 없으면 상태별 전신 포즈를 적용한다. `skin.motion`에 선택 경로를 기록했다.
- 추가 검증: 명시 앵커·이름 추정·L/R 접미사·weighted bone·손 없음·빈 노드 6모델의 실제 정점 이동 통과. scale 108표본·9 drawCalls 유지, pytest 10 passed/전체 기존 실패 11개 동일·공개 검사 통과. 로컬 근거: `.pr-drafts/p4-3d-artlab/review3-motion/`.

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
