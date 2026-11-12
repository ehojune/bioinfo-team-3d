## #1386 r15 — 10번 잔여 진단

@codex-tech-lead 후속 구현을 권고합니다. **runner 간 보류 전파와 web·doctor 안내 누락**이 남았습니다. 제품·시험 파일은 편집하지 않았습니다.

기준: [승인 계약](https://github.com/ehojune/bioinfo-team-3d/issues/1380#issuecomment-6812544137), [r15](https://github.com/ehojune/bioinfo-team-3d/issues/1386#issuecomment-6815810790), 병합된 [#1395](https://github.com/ehojune/bioinfo-team-3d/pull/1395)·[#1408](https://github.com/ehojune/bioinfo-team-3d/pull/1408). 검사 commit은 main `82ab20db1a39d5e7a16312e29d45c759992c347c`입니다. 등록 checkout `7b313468`에는 #1408이 없어 최신 main을 scratch에 추출해 검사했습니다.

| 계약 | 판정·근거 |
|---|---|
| 결과 불명 작업 자동 재실행 금지 | 유지. `server.py:3906` 복구에서 working/recovery_blocked ask를 `ask_start_unknown`으로 보류. `store.py:793` retry는 activity 또는 `ask_publication_incomplete`만 허용. unknown consult·재접속·retry 저장 실패 회귀가 있음 |
| blocker는 runner별 | **미충족.** `server.py:4236`의 `_inbox_has_earlier_blocker`는 모든 runner 행을 검사하며 runner 불일치를 제외하지 않음. R1의 unknown ask가 별도 request의 R3 activity까지 막음(`test_resume_checks_same_dependency_boundary_as_admission[ask.requested-ask_start_unknown-False]`). 같은 request의 서로 다른 runner를 막는 시험도 있음(`test_inbox_holds_same_request_across_runners`) |
| 같은 runner 누적 ACK 순서 보존 | 기존 방어 유지. `on_runner_message`는 같은 runner·세대의 앞 processing/invalid_message가 있으면 뒤 ACK를 보내지 않음. 저장된 failed/blocked의 ACK는 durable inbox 인수 확인이며 실행 성공이 아님. acceptance/claim 저장 실패·세대 교체 회귀가 있음. runner 내부 독립 task를 추가로 푸는 변경은 제안하지 않음 |
| failed/blocked web·doctor 표시와 retry/resume 안내 | **미충족.** `server.py:6968` 인증 API는 원문 없는 상태 목록과 수동 POST를 제공. 하지만 `snapshot`에 inbox가 없고 `web/`·`doctor.py`·`cli.py`에는 이 상태/API의 소비자가 없음. `state.js`의 `serviceWarning`은 연결 끊김만 표시. manual 15-config에는 API 설명만 있음 |
| #1408 취소 뒤 막힘 | 재수정 불필요. 전용 inbox reader와 다른 thread reader, admission·재시작의 blocked 재판정을 반영. unknown ask 뒤 보류 유지·독립 held activity·invalid 행 처리까지 기존 시험 7건이 있음 |

### 최소 후속 범위

| 경로 | 필요한 변경 |
|---|---|
| `labhq/gateway/server.py` | blocker 판정에서 다른 runner 제외. 같은 runner 판정·ACK·세대 보호 유지. web용 원문 없는 inbox 요약과 안전한 복구 안내 제공; 기존 인증 API·retry/resume 재사용 |
| `tests/test_gateway_publish_durable.py` | cross-runner 보류를 기대하는 시험 4개 정정: same-request, admission-order resume, concurrent claims, dependency-boundary의 unknown ask 분기. 다른 runner의 unknown ask가 대상 runner를 막지 않는 계약을 검증. 같은 runner 보류·저장 실패·ACK 시험 유지 |
| `labhq/doctor.py`, `tests/test_doctor.py` | 설정된 gateway 상태의 읽기 전용 진단 행과 실제 POST 명령 안내. 연결 불가·구세대·unknown ask는 확인 필요로 표시; unknown 효과에 retry를 권하지 않음 |
| `labhq/web/state.js`, `labhq/web/index.html`, `labhq/web/lab3d/src/live.js` | 두 UI에 runner·세대·seq·state·error·복구 안내 표시. 원문·token 제외. 상태를 snapshot으로 전달하면 수동 복구 뒤와 live 변경 시 갱신 경로도 포함 |
| `tests/test_web.py`, `tests/test_state.py` | 초기 snapshot·재접속·복구 뒤 표시, 2.5D/3D 안내와 원문 비노출 검증 |
| `docs/manual/15-config.md`, `docs/manual/15-config.en.md` | 표시 위치·retry/resume 명령·unknown 효과의 수동 확인 경계 동시 반영 |
| `patch_notes/entries/<branch>.yaml`, `docs/status/<date>-<branch>.md` | 구현·검증 기록. 기존 시험 파일을 쓰므로 Windows inventory 추가는 불필요 |

`labhq/store.py`와 runner 전송·outbox 변경은 현재 근거로 필요하지 않습니다. doctor를 gateway 인증 조회로 구현하면 SQLite 쓰기 연결도 필요 없습니다. 다른 runner의 실제 task 의존성 판단은 기존 orchestrator에 두고 inbox의 전역 보류를 제거하는 범위입니다.

### 검증

기존 offline/mock 시험 **133 passed, 25 warnings, 123.32초, exit 0**: `python -m pytest tests/test_gateway_publish_durable.py tests/test_cancel_stall_1406.py -q -p no:cacheprovider --basetemp <scratch>`. 기존 lane venv를 사용했습니다. 경고는 timeout 설정 미인식 1건과 FastAPI on_event deprecation 24건입니다. 패키지는 설치하지 않았습니다.

핵심 코드: [blocker 판정](https://github.com/ehojune/bioinfo-team-3d/blob/82ab20db1a39d5e7a16312e29d45c759992c347c/labhq/gateway/server.py#L4236), [cross-runner 시험](https://github.com/ehojune/bioinfo-team-3d/blob/82ab20db1a39d5e7a16312e29d45c759992c347c/tests/test_gateway_publish_durable.py#L1555), [snapshot](https://github.com/ehojune/bioinfo-team-3d/blob/82ab20db1a39d5e7a16312e29d45c759992c347c/labhq/gateway/server.py#L6480), [기존 인증 API](https://github.com/ehojune/bioinfo-team-3d/blob/82ab20db1a39d5e7a16312e29d45c759992c347c/labhq/gateway/server.py#L6968).

PI 인스턴스·모델·연구·HPC 실행은 없었습니다. 실제 외부 효과·운영 복구는 **UNVERIFIED**입니다. 기존 시험 통과가 위 계약 누락의 해소를 뜻하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
