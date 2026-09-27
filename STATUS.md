# STATUS — labhq

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).

## 2026-09-28 · P4 1단계 — 공유 reducer와 실시간 3D
- 무엇을: `state.js`로 상태 처리를 분리하고 `/3d`에 실제 roster·포즈·요청 보드·DOM 승인/거절·since 재연결을 연결했다. 2.5D 전환과 데모 유지. 정적 경로 제한과 배포 에셋 포함.
- 테스트: Windows 기준선 11 failed/203 passed/13 skipped → 11 failed/227 passed/14 skipped(새 실패 0). Node에서 원본 43개 이벤트 상태·재연결·승인 검사, 공개 검사 통과.
- 화면: Chrome 153 headless, 390×844/DPR 2·1280×900. 실시간 승인/거절·roster 추가/삭제·재연결·2.5D 렌더 통과, 콘솔 오류·가로 넘침 0. 데모 9 calls/69,382 triangles 유지, 실제 12명 9 calls/69,538 triangles(+0.22%, 배지).
- 막힌 점: Windows 기존 실패 11개와 symlink 권한 skip. Linux CI·iPhone Safari 실기·wheel 설치 실행은 미검증. Chrome은 샌드박스 제약 때문에 테스트 전용 프로필에서 SwiftShader로 측정했다.
- 다음: Claude가 브라우저 확인 후 push·PR. 신규 production 모듈은 `state.js`와 `lab3d/src/live.js` 2개.

## 2026-09-27 · P1+ 데이터 경계
- 무엇을: 경로 비교·상대 쓰기·설정 검증을 닫고, 통제 구역이 있는 Windows 러너와 원본을 읽는 POSIX 러너의 시작을 거부한다. `hpc.submit_prefix`는 잡 제출·취소에 적용한다.
- 테스트: Windows 기준선 10 failed/60 passed → 9 failed/69 passed/1 skipped. 새 실패 0개. `scripts/check_public.sh` 통과.
- 막힌 점: POSIX 파일 권한 검사는 Windows에서 건너뜀. Linux CI 확인 필요. UNC Claude 규칙은 미실측이라 러너가 거부한다.
- 다음: Linux CI에서 POSIX 권한 테스트와 계정 분리 배치를 확인한다.
- 후속 수정: `docker -v`·`scp`·`rsync`·`file://`처럼 인자에 붙은 경로를 탐지한다. Windows 9 failed/80 passed/1 skipped(신규 실패 0), 공개 검사 통과.
- 리뷰 반영: 선행 `\` 경로를 절대경로로 판정한다. 계정 전환 제출은 `hpc.job_group`과 POSIX 그룹 소속을 확인하고 잡 스크립트·로그 권한을 맞춘다. Windows 9 failed/82 passed/3 skipped(신규 실패 0).
- 2차 리뷰 반영: execute-only 구역도 거부한다. 전환 잡은 `hpc.user`·양쪽 계정의 그룹 소속을 확인하고 `hpc_out/`에서 실행한다. Windows 9 failed/83 passed/4 skipped(신규 실패 0).
- 4차 리뷰 반영(2026-09-27): main의 CSO 변경을 병합하고, 전환 잡 입력의 other 권한과 드라이브 상대경로의 불확실한 쓰기·구역 판정을 닫았다. Windows 9 failed/112 passed/5 skipped(신규 실패 0), 공개 검사 통과.
- 5차 리뷰 반영(2026-09-27): 전환 잡 제출 때 `workspace_root`·날짜 폴더에 group traverse를 주고, 바깥 상위 경로가 막히면 명확히 거부한다. Windows 9 failed/112 passed/7 skipped(신규 실패 0), 공개 검사 통과.
- 6차 리뷰 반영(2026-09-27): Linux CI의 umask 022 테스트 setup에서 부모 폴더를 각각 0700으로 만들고, 생성 직후·거부 후 권한을 모두 확인한다. 제품 코드는 부모 mode를 가정하지 않아 변경하지 않았다. Windows 9 failed/112 passed/7 skipped(신규 실패 0), 공개 검사 통과.
- 7차 리뷰 반영(2026-09-27): 반복 제출 중 workdir의 g+x를 유지하고, 연결된 task 입력은 원본 mode를 바꾸기 전에 거부한다. 명시적 경계에서만 경로를 분리해 `/scratch/data/cohort` 오탐을 없앴다. Windows 9 failed/114 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 8차 리뷰 반영(2026-09-27): Python 3.12 `Path.chmod`의 `follow_symlinks`를 테스트 monkeypatch에서 전달하고 g+x 단언은 유지한다. 같은 형태의 `os.access` wrapper도 원본 인자를 전달한다. Windows 9 failed/114 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 9차 리뷰 반영(2026-09-27): `hpc.submit_prefix`를 `qdel`에도 적용하고, 취소 실패 때 `sudoers`의 `qdel` 권한을 안내한다. `qstat`은 러너 계정으로 조회한다. Windows 9 failed/115 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 10차 리뷰 반영(2026-09-27): 공백이 든 통제 구역을 구조화된 경로·따옴표 셸 경로에서 보존하고, 따옴표 없는 셸 입력은 원문 경계 검사로 놓침을 막는다. Windows 9 failed/116 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 11차 리뷰 반영(2026-09-27): Bash의 backslash-escaped 공백을 경로로 인식하고, `jobs`의 기존 입력을 private으로 정리하되 이전 잡 스크립트 접근을 유지한다. Windows 9 failed/116 passed/13 skipped(신규 실패 0), 공개 검사 통과.

## 2026-09-26 · P1+ 새 페이지 스냅샷 복구
- 한 일: 웹의 `lastSeq`를 페이지 메모리에만 둔다. 새로 연 페이지는 스냅샷으로 시작하고 같은 페이지의 재연결만 `since`를 보낸다. 예전 localStorage 키는 무시한다.
- 테스트: 정적 회귀 테스트 추가. Windows 10 failed·65 passed → 10 failed·66 passed, 새 실패 0. 공개 검사 통과.
- 막힌 점: 실제 브라우저 재연결은 미검증.
- 다음: Linux CI와 브라우저에서 새로 고침·재연결 확인.

## 2026-09-26 · P1+ 상태 복구·이벤트 재전송
- PR #9 10차: 불확실 task는 같은 runner 세대에만 재전송하고, 완료된 direct 요청은 agent 없이 종결하며, GitHub 계획·리뷰 등의 미게시 이벤트를 재시작 후 전달한다. Windows 10 failed·148 passed(신규 실패 0), 공개 검사 통과.
- PR #9 9차: runner 세대가 바뀐 수락 task는 수동 복구로 종결하고, 같은 세대의 runner 재시작 뒤 추적 중인 HPC job은 재제출 없이 wake로 잇는다. Windows 10 failed·138 passed(신규 실패 0), 공개 검사 통과.
- PR #9 8차: 수락 task의 완료 대기에서 연결 timeout을 빼고, 재시작 후 기존 GitHub issue를 찾아 중복 생성을 막으며 terminal 이벤트를 다음 seq보다 먼저 보낸다. Windows 10 failed·135 passed(신규 실패 0), 공개 검사 통과.
- PR #9 7차: 제어 단계 task와 리뷰 수정 횟수를 재시작 후 이어받고, GitHub 최종 보고·코멘트·닫기 작업을 중복 실행하지 않는다. Windows 10 failed·132 passed(신규 실패 0), 공개 검사 통과.
- PR #9 6차: runner 재시작 task를 실패 결과로 종결하고 불확실한 전송은 같은 ID로 재전송한다. 누락된 GitHub issue를 최종 보고 전에 복구하며 replay-gap 스냅샷은 웹 상태를 교체한다. Windows 10 failed·119 passed(신규 실패 0), 공개 검사·Chrome headless 통과.
- PR #9 5차: HPC 완료 알림은 단계 결과 채택까지 보존하고, 종료 상태·이벤트·GitHub 전달 대기를 한 트랜잭션에 저장해 재시작 때 전달한다. Windows 10 failed·115 passed(신규 실패 0), 공개 검사 통과.
- PR #9 4차: `task.result`는 attempt·revision 이력으로만 저장하고 DAG가 채택한 결과만 완료 처리한다. revision 피드백을 복구하며 runner 수신 ACK와 같은 task ID 재전송·중복 실행 방지를 추가했다. Windows 신규 실패 0, 공개 검사 통과.
- PR #9 3차: CSO main을 병합해 재개도 skip·재시도·리뷰 판정을 쓰게 하고, 늦게 온 task 결과·HPC 완료 복구, 중요 outbox 이벤트 보존, 완료 단계 runner 대기 제외, 교체 소켓 격리, 재개 거절 실패 이벤트를 고쳤다. Windows 신규 실패 0, 공개 검사 통과.
- PR #9 리뷰 반영: 재개 후 과학 리뷰·수정, runner 세대별 seq, 단계 비용, runner 대기, 복원 불가 승인 만료, GitHub issue 번호 복원을 추가. Windows 10 failed·66 passed → 10 failed·71 passed(새 실패 0), 공개 검사 통과.
- 한 일: 게이트웨이 요청·승인·이벤트와 러너 HPC 잡·outbox를 각 SQLite WAL에 저장. 재시작 요청은 `interrupted`로 두고 PI 승인 뒤 남은 DAG 단계만 실행. 이벤트 `schema_version`·전역 `seq`, WS/REST `since`와 보관 범위 초과 스냅샷을 추가.
- 테스트: Windows 기준선 10 failed·60 passed → 10 failed·65 passed, 새 실패 0. UTF-8 mock 통합 흐름 2개 통과. 공개 검사 통과.
- 막힌 점: Windows 기존 실패 10개는 그대로. 헤드리스 브라우저 재연결은 미검증.
- 다음: Linux CI와 실제 브라우저·HPC 재연결 확인. push·PR은 Claude 담당.

## 2026-09-26 · P1+ ④ 직원 CLI 개인 설정 격리
- 한 일: 어댑터가 PI 개인 CLI 설정을 빼고 직원 CLI를 띄운다(`engines.*.isolate_user_config`, 기본 켜짐). `engines.*.env`가 실제로 전달되게 고쳤다(전에는 무시됐다).
- 실측(Windows 11, 실제 계정): 개인 지침에 있는 단어를 묻는 질문으로 확인했다.
  - Claude 2.1.282: 기존 명령은 skill 55·plugin 6·개인 서브에이전트 3·SessionStart hook·전역 CLAUDE.md를 불러왔다. 격리 후 skill 0, 내장 plugin 2, 내장 서브에이전트 6, hook 0, 전역 지침 없음. 같은 질문의 입력 토큰이 약 12k 줄었다.
  - Codex 0.155: `--ignore-user-config --ignore-rules`로 config.toml의 plugin·notify hook·MCP가 빠졌다(입력 24k → 15.5k 토큰). 전역 AGENTS.md는 남는다. `project_doc_max_bytes=0`, `features.agents_md=false`로도 안 빠졌다.
  - Codex on Windows: config.toml을 건너뛰면 `[windows] sandbox`도 빠져 파일 쓰기가 막히는데 종료 코드는 0이었다. `windows.sandbox="elevated"`를 다시 넣어 쓰기를 확인했다.
  - agy 1.2.11: 격리 전에도 전역 지침을 읽지 않았다. `--disable-slash-commands`는 과학 DB skill까지 끌 수 있어 넣지 않았다.
- 가림 보강: Claude `rate_limit_event`에 요금제 사용률·재설정 시각·조직 초과사용 설정이 있었다. main의 Claude fixture 7개에 들어 있던 것을 가리고 구조 테스트를 붙였다.
- 리뷰 반영(Codex 봇 P1): 전역 AGENTS.md가 있으면 Codex 직원 작업을 실행 전에 거부한다(`engines.codex.allow_global_agents_md`로만 허용). 어댑터 파일 입출력을 UTF-8로 고정했다. 한국어 Windows에서 역할 지침 파일을 cp949로 쓰다 죽던 기존 결함이다.
- 리뷰 반영 2차: `isolate_user_config`는 구현된 Claude·Codex에만 둔다(엔진 설정의 모르는 키는 거부). 실측 스크립트도 같은 preflight를 거친다. prepare 이후에 subprocess 환경을 다시 만든다.
- 리뷰 반영 3·5차: 거부 검사와 CLAUDE.md 제외는 자식 CLI가 쓸 수 있는 home 후보 전부(병합된 `HOME`과 `USERPROFILE`)를 본다. `CODEX_HOME`·`CLAUDE_CONFIG_DIR`이 있으면 그것 하나(상대경로는 작업 폴더 기준).
- 리뷰 반영 4차: 실측 스크립트가 `LABHQ_CONFIG`를 읽는다. 설정 파일을 UTF-8로 읽는다(한국어 주석이 든 예시 설정이 Windows에서 cp949로 깨지던 결함).
- 리뷰 반영 6차: Claude는 작업 폴더에서 위로 올라가며 CLAUDE.md를 읽는다. 상위 폴더에 카나리 CLAUDE.md를 두고 격리 세션에 물으니 YES였고, 모든 상위 폴더의 CLAUDE.md·`.claude/CLAUDE.md`를 제외한 뒤 NO가 됐다(실제 계정, haiku, 회당 약 $0.009).
- 규칙: PR 리뷰 답글에 `@codex`를 붙이지 않고, push 뒤 `@codex review`를 한 번만 단다(PI 결정 2026-09-27, 댓글마다 봇 세션이 따로 떴다). CLAUDE.md·AGENTS.md·HANDOFF와 직원에게 주입되는 `ROLE_FOOTER`, README §4를 고쳤다.
- 리뷰 반영 8차: 실측 스크립트의 `--name`은 파일 이름만 받는다(가리지 않은 원본이 `--output-dir` 밖, 예컨대 공개 저장소로 나가지 않게). 예시 설정·`settings.py`의 멘션 안내도 새 규칙으로 맞췄다.
- 테스트: Windows 기존 실패 10개 → 9개(남은 것은 모두 기존 실패), 새 테스트 29개 통과. Linux는 CI.
- 막힌 점: Codex 전역 AGENTS.md는 직원용 `CODEX_HOME` 로그인(PI 조치)이나 러너 전용 계정으로만 빠진다. 직원용 로그인 방식은 미검증.
- 다음: P1+ 나머지 갈래(② CSO, ③ 데이터 경계, ①⑤ 영속화·이벤트 순번) 병합.

## 2026-09-26 · P1+ ② CSO 실패 전파·리뷰·재시도
- PR #7 3차: runner 재연결 신호 대기(기본 30초), skipped 웹 상태, review_unparsed 웹·GitHub 실패 표기를 추가. Windows 8 failed·90 passed(기준선 외 WebSocket 연결 대기 2건), 공개 검사 통과; 헤드리스 DOM 확인 실패.
- PR #7 P2 재검토: 예산 판정·승인을 요청 단위로 직렬화하고 완료 단계는 보존, 미시작 단계만 budget 사유로 skip. Windows 6 failed·89 passed(신규 실패 0), 공개 검사 통과.
- PR #7 리뷰 반영: runner offline·전송 실패는 재시도, 일반 nonzero exit은 중단, 리뷰 스키마 전체 검사. Windows 6 failed·87 passed(기존 실패만), 공개 검사 통과.
- 한 일: 실패·예산 거부·취소의 하위 단계만 skip하고 사유를 보고한다. 리뷰 판정은 엄격히 재요청한 뒤에도 읽지 못하면 `review_unparsed`로 실패 처리한다.
- 설정: `step_max_attempts=2`, `step_retry_backoff_s=0.2`. 재시도 비용도 요청 예산에 합산한다.
- 테스트: Windows 기준선 6 failed·64 passed → 6 failed·80 passed. 신규 실패 0. `scripts/check_public.sh` 통과.
- 막힌 점: Linux CI는 PR에서 확인 필요. 다음: Claude가 PR을 열고 P1+ 다른 갈래와 병합 검토.

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
