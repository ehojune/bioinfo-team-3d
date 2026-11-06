# #1076 간헐 실패 집계 (2026-11-04 ~ 11-06)

lookup-only. 근거는 [#1076](https://github.com/ehojune/bioinfo-team-3d/issues/1076)에 기록된 실패 증거 댓글이다. 재실행으로 통과한 것만 센다. 원인이 확인된 것은 없다.

## 시험별·OS별

| 시험 | OS·Python | 횟수 | 오류 첫 줄 | 근거 |
|---|---|---|---|---|
| `test_web_3d.py::test_office_in_node` (`web_*.cjs` 5종) | Windows | **5** | 아래 표 | 11-04 02:02, 11-05 02:26·04:25·17:53 |
| `test_approval_durable_606.py::test_backpressured_runner_delivery_has_a_bound_even_after_cancel[close]` | Windows | 1 (이전 run 포함 2) | `runner backpressure held cancellation and the card lock past the configured send timeout` | [run 41965066999](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41965066999/attempts/1) |
| `test_request_deadline_descendants_1232.py::test_the_deadline_ends_the_cli_and_every_process_it_started_and_only_those` | Linux 3.12 | 1 | `ValueError: invalid literal for int() with base 10: ''` | [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42067643511/job/127386308030) |
| `test_gateway_publish_durable.py::test_asgi_cancel_scope_only_shields_storage` | Linux 3.12 | 1 | `TimeoutError` | [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42072725666/job/127404861448) |
| `test_bench.py::test_labhq_staff_model_replacement_is_claude_only_and_preserves_source[override1-haiku]` | Linux 3.10 | 1 | `TypeError: 'NoneType' object is not iterable` | [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42029103680/attempts/1/job/127251367092) |
| CI `validation-manifest` | Linux | 1 | `unbound checkout/workflow identity: checkout-parents` | [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41985987585/job/127110779619) |
| `pytest (3.12)` 설치 단계 | Linux 3.12 | 1 | package download `IncompleteRead` (시험 전) | #1076 r41 결과 댓글 |

합계 11건. Windows 6건(web 5, approval 1), Linux 5건(시험 3, CI 단계 2).

## `test_office_in_node` 5건

| 스크립트 | 실패 | 날짜·head | 근거 |
|---|---|---|---|
| `web_visual_tooling.cjs` | `the driver lists a timer callback exception in errors…`, `demo cleanup incomplete` (pending: `chrome-stdio`, `websocket`) | 11-04, 같은 SHA `60b912cd` 두 schedule run 중 한 번 | [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41556743196/job/125657446685), 성공은 [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41739040647/job/126275686112) |
| `web_office_focus_layout.cjs` | `office prioritizes decisions, exposes plan progress and opens failed results` | 11-05, PR #538 `cc5f68d` | [job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41945766103/job/126977542711) |
| `web_office_browser.cjs` | `AssertionError: web_office_browser.cjs` | 11-05, main `981c1c88` | [attempt 1](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41965066999/attempts/1) |
| `web_resizable_panels.cjs` | `demo ui deadline reached during Runtime.evaluate` | 11-05, PR #1288 `af9034fc` | [run 42064927949](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42064927949) |
| `web_resizable_panels.cjs` | 같은 오류 | 11-05, PR #1288 `1d612ab5` | [run 42072693929](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42072693929) |

- 공통점: 모두 실제 Chrome을 띄우는 Windows 스크립트이고, 마지막 줄이 demo driver의 deadline이나 cleanup이다.
- 다섯 모두 변경과 무관했고 같은 head 재실행 1회로 통과했다.
- #833, #1162로 이미 한 번씩 손본 영역이다.

## 로컬 재현 시도 (2026-11-06)

| 항목 | 값 |
|---|---|
| 대상 | `test_office_in_node`의 Chrome 4종(`web_office_browser`, `web_visual_tooling`, `web_office_focus_layout`, `web_resizable_panels`) |
| 환경 | 이 Windows PC(Python 3.12), origin/main 2026-11-06 checkout, 명령 `pytest tests/test_web_3d.py -k "office_in_node and (…)"` |
| 부하 | 동시 4개 × 5회(20 invocation), 동시 12개 × 1회(12 invocation) |
| 결과 | 32 invocation, 128 시험 **모두 통과**. 단독 약 35초, 12 동시 약 50초 |

재현하지 못했다. 이 PC는 CI Windows runner(Chrome 첫 요청 11.6초, 페이지 부팅 6.5초가 보고된 환경)보다 한가하다. 시간 여유가 줄어드는 조건(느린 디스크·백신·cold start)은 만들지 못했다. 그래서 "재현 불가"는 원인 후보를 약하게 만든다. 반증은 아니다.

## 원인 후보와 고칠 범위

| 후보 | 근거 | 아직 모르는 것 |
|---|---|---|
| runner에서 Chrome cold start·페이지 부팅이 길어져 시험 안의 deadline에 걸림 | focus_layout 보고(Chrome 첫 요청 11.6초 대기), resizable의 `deadline reached during Runtime.evaluate`, 로컬 무부하에서는 통과 | 어느 단계가 느려졌는지. 실패 로그에 단계별 시각이 없다 |
| 실패 뒤 cleanup이 `chrome-stdio`·`websocket`을 닫지 못해 두 번째 오류가 덮음 | visual_tooling의 `demo cleanup incomplete` | 처음 실패와 cleanup 실패 중 무엇이 먼저인지 |
| 같은 runner에서 pytest 4 worker가 동시에 Chrome을 띄워 자원 경쟁 | 실패가 모두 병렬 pool 안에서 났다(확인 가능한 run 기준) | 단독 직렬 run에서 나지 않았다는 직접 증거 |

고칠 범위(제안). 1번은 같은 PR에서 `tests/support/demo_driver.cjs`에 넣었다(아래 "진단 추가"):
1. 진단 먼저: `tests/support/demo_driver.cjs`가 실패 때 단계별 monotonic 시각(Chrome spawn, 첫 요청, 페이지 ready, 마지막 CDP 호출)과 pending 핸들을 한 줄로 출력한다. approval 시험 #1274처럼 실패 메시지가 원인을 가르게 한다.
2. 그 뒤 로그로 후보를 가린 다음 `tests/support/demo_driver.cjs`, `tests/web_resizable_panels.cjs`, `tests/test_web_3d.py`의 해당 경로만 고친다. 원인 확인 전에는 timeout만 늘리지 않는다.
3. Linux 3건과 CI 인프라 2건은 한 번씩만 나왔다. 두 번째 재발 때 같은 방식으로 진단한다.

## 한계

- #1076 댓글에 남은 건만 셌다. 기록 없이 재실행한 실패는 빠진다.
- 실패 run의 원본 로그를 직접 다시 읽지는 않았다(재실행으로 attempt 1 로그를 열 수 없는 run이 있다). 오류 문구는 댓글에 적힌 대로다.

## 진단 추가 (같은 PR)

실패 때 원인을 가르기 위한 관찰만 더했다. timeout·단언·실행 순서는 그대로다.

- 작업 deadline이 끝낸 순간의 행(`work-deadline`·`startup-deadline`·`ui-deadline`)에 추가: 그때 기다리던 `until`·`waitReal`의 종류·label·경과 ms, 응답 없는 CDP 호출(method·나이), 마지막 CDP 응답 이후 ms, 미완료·완료·실패 요청 수, 정적 서버가 받은 요청 수, 페이지 오류 수, Chrome 종료 관찰 여부. 시험 안 표현식·URL은 싣지 않는다.
- cleanup 진단(`demo cleanup: …`)에 추가: cleanup 시작 후 경과 ms, 닫히지 않은 핸들(`chrome-stdio`·`websocket`·`http-server`)별로 닫기 요청이 실제로 시작된 뒤 경과 ms(아직 시작 전이면 null). 기존 주입 진단이 받는 행은 바뀌지 않는다.
- 다음 실패 로그에서 "어느 단계가 느렸나"(후보 ①), "cleanup이 먼저 걸렸나"(후보 ②)를 가린다.


## web_resizable_panels 실패 분석 (#1272, main `3f018499`, 2026-11-06)

실패: [run 42224884369](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42224884369/job/127915899306), `pytest-windows` "Serial critical Windows contracts", `test_office_in_node[web_resizable_panels.cjs]` desktop. 오류는 `demo ui deadline reached during Runtime.evaluate`(`DEMO_WORK_DEADLINE`)이고, #1291이 넣은 진단 행이 처음으로 실패 로그에 찍혔다.

### 60초가 어디에 쓰였나

Python이 이 스크립트를 띄운 시각(`launch`)부터 바깥 한계가 60초이고, 브라우저 작업의 작업 한계는 거기서 닫기 예약 8초를 뺀 52초다.

| 구간 | 걸린 시간 | 근거 |
|---|---|---|
| Node가 시작해 스크립트 첫 줄에 닿기까지 | **19.3초** | `module_entry_mono_ms: 19310`(Node 프로세스 시작 기준) |
| 모듈 읽기 | 4.1초 | `module-ready` mono 23368 |
| 앞의 시험 두 개(브라우저 없음) | 2.7초 | `open` mono 26025, 두 시험 합 2.65초 |
| 소계: `openDemo` 시작 전 | **28.6초** (launch 기준) | `open` 행의 `epoch_ms` − `launch_epoch_ms` |
| 작업 한계까지 남은 시간(open 시점) | 23.3초 | `work_remaining_ms: 23346` |
| Chrome 기동~DevTools 포트 | 2.1초 | `debug-port-ready` 2115 |
| **`/json`이 처음 답하기까지** | **17.6초** | `target-ready` since_previous 17648, `json_attempts: 1, json_unanswered: 0` |
| 소켓·프로토콜 준비 | 0.4초 | `protocol-ready` 20186 |
| 페이지 요청→응답 | 0.5초 + 1.7초 | `server-request` 20661, `server-response` 22393 |
| boot 대기 | 0.9초에서 deadline | `ui-deadline` elapsed 23346, `wait: waitReal "demo boot" running_ms 910` |

- ui 단계의 한계는 20초 예산이 아니라 **남은 작업 한계 23.3초**였다. 시작 단계가 20.2초를 쓰고 페이지 이동이 2.2초를 쓴 뒤 boot를 0.9초 기다렸을 때 끝났다.
- deadline 순간: 응답 없는 CDP 호출은 `Runtime.evaluate` 1개(0.9초 전에 보냄), 마지막 응답은 0.9초 전, 마지막 이벤트는 0.5초 전. 미완료 요청 11개, 완료 6개, 실패 0, 정적 서버가 받은 요청 17개, 페이지 오류 0, Chrome은 살아 있었다. `port_reads`는 `{ENOENT: 1, EBUSY: 1}`로 포트 파일을 두 번 읽었고 곧 읽혔다(`debug-port-ready` 2.1초).
- 미완료 11개는 페이지가 부팅하며 부르는 자기 서버의 자원 요청이다(정적 서버가 17개를 냈고 6개가 끝난 상태). 페이지 자체가 멈춘 증거는 없고 boot를 0.9초밖에 기다리지 못했다.
- 정리(cleanup)는 정상이다: `closed`, 0.99초, 닫히지 않은 핸들 없음. 후보 ②(정리가 먼저 실패)는 이번에는 아니다.

### 같은 시각 다른 시험도 느렸다

같은 job, 같은 단계에서 바로 앞에 돈 `tests/test_windows_job_ci.py::test_allowed_breakaway_child_survives_parent_job_close`가 24.4초 걸렸고 WMI 부트스트랩이 `provider_timeout`(PowerShell 시작 후 `header_read` 9.7초)이었다. `test_real_wmi_bootstrap_…[receipt_timeout]`도 10.0초다. 새 프로세스를 띄우는 일(PowerShell, Node, Chrome)이 이 구간에 한꺼번에 10~20초씩 느렸다.

### 원인 후보

| 후보 | 근거 | 반증·미확인 |
|---|---|---|
| ① runner가 이 시각에 새 프로세스를 띄우는 데 평소보다 10~20초 더 걸림(백신 검사·디스크·CPU 경합) | Node 시작 19.3초, Chrome 첫 `/json` 응답 17.6초, PowerShell `header_read` 9.7초가 같은 3분 안에 겹침. 로컬 128회 재현에서 이 시험 전체(desktop+phone)가 1.7초였음(`--durations`) | 어느 자원이 막혔는지는 로그에 없음. runner 부하 지표를 모은 적이 없다 |
| ② 스크립트의 60초 한계가 Node 시작(19.3초)을 포함해 시험이 쓰는 시간이 줄어듦 | Node 시작·앞선 두 시험만으로 28.6초(47%)가 `openDemo` 전에 사라짐. 같은 desktop 호출의 ui 단계는 3.1초만 남았다 | 한계를 늘리면 증상은 가리지만 원인(후보 ①)은 그대로 |
| ③ 정리 실패가 먼저 | cleanup이 0.99초에 정상 종료 | — |
| ④ 페이지·제품 결함 | boot를 0.9초만 기다림, 페이지 오류 0 | 판단 근거 없음 |

#1076의 이전 5건(`web_visual_tooling`, `web_office_focus_layout`, `web_office_browser`, `web_resizable_panels` ×2)은 진단 행이 없어 이 표에 올릴 수 없다. 이번 1건만 단계 시각이 있다.

### 고칠 범위 (제안, 이번에는 코드 수정 없음)

1. **측정 먼저**: `tests/test_web_3d.py`가 Node 시작 시간을 따로 남기게 한다. 지금은 Python이 `launch` 시각만 알고, Node 안의 `module_entry_mono_ms`는 실패 로그에만 나온다. 성공한 run에도 같은 한 줄(Node 시작·모듈 읽기·open 시점의 남은 시간)을 찍으면 평소 값과 이번 값을 비교할 수 있다. 파일: `tests/test_web_3d.py`, `tests/web_resizable_panels.cjs`.
2. **한계의 기준을 옮기는 안**: 바깥 한계를 `launch` 기준 60초로 두되 Node 시작이 느린 경우를 위해 작업 한계를 스크립트 진입 시각 기준으로 다시 계산한다. 한계 합(60초)은 늘리지 않으므로 하드 캡은 같다. 다만 이번처럼 Node 시작이 19초면 한계 자체가 줄어든 것이 아니라 시험이 쓸 시간이 줄어든 것이어서, 이 안은 후보 ②만 다룬다.
3. **한계를 늘리는 안**: Windows CI에서만 `web_resizable_panels.cjs`의 바깥 한계를 60초에서 90초로 올린다. 이번 실패의 흐름대로면 Node 시작과 시험 앞부분 28.6초, 브라우저 열기 20.2초, 이동·boot 몇 초를 합쳐 70초 안팎이 필요하다. 기존 단언은 그대로다. 비용은 최악의 경우 이 시험이 30초 더 걸리는 것뿐이다. 후보 ①이 원인이면 근본 대책은 아니지만, 원인이 runner 쪽이면 다른 방법이 없다.
4. **원인 확인 순서 권고**: 1 → 다음 실패 시 값 비교 → 3. 2와 3은 증상 완화이고 원인 후보 ①을 가리지 못한다. 권고는 1을 먼저 넣고, 그 사이 같은 단계에서 WMI 실패(#1076, `docs/reference/ci-1076-wmi-provider-timeout.md`)가 겹쳐 나는지 함께 본다.

한계: 이 분석은 실패 run 로그 한 개에 근거한다. runner의 CPU·디스크 지표와 백신 설정은 볼 수 없었다.
