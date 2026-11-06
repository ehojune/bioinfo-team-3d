# 로그의 토큰 노출 감사 (#37, 2026-11-07)

lookup-only. #37 "로그의 토큰 노출" 항목의 현재 상태를 main `08074efb`에서 코드로 찾고, 가짜 토큰을 넣은 임시 인스턴스로 확인했다. 감사 때는 수정하지 않았고, 같은 PR에서 수정과 재확인을 했다(맨 아래).

## 결론

| 경로 | 토큰 | 노출 | 재현 |
|---|---|---|---|
| gateway 콘솔·uvicorn 접근 로그(`?token=`) | client·runner | **막혀 있음** | 아니오 (`token=[REDACTED]`) |
| runner가 gateway에 못 붙을 때의 로그, `gateway.url` 스킴이 `ws`·`wss`가 아님 | runner | **노출** (`labhq up`이면 상태 폴더 로그 파일에 남음) | 예 |
| `labhq watch`가 같은 설정 오류를 만났을 때의 traceback | client | **노출** (터미널) | 예 |
| `labhq demo`가 접속 주소를 출력 | client | 의도된 출력 (터미널) | 코드로만 확인 |
| `labhq open` | client | 막혀 있음(브라우저에만 전달) | 코드로만 확인 |
| GitHub 토큰, 엔진 API 키 | — | 찾지 못함 | 아니오 (0건) |

토큰이 그대로 찍히는 곳은 두 곳이고, 둘 다 `gateway.url`을 `http://…`처럼 `ws`·`wss`가 아닌 스킴으로 잘못 적었을 때 생긴다. 정상 설정에서는 재현되지 않았다.

## 방법

- 임시 `HOME`·`USERPROFILE`, 포트 18787·18788, `runner.force_engine: mock`인 새 인스턴스를 `labhq init --yes`로 만들고 설정의 두 토큰을 가짜 문자열로 바꿨다. 실제 인스턴스·실제 토큰·실제 네트워크는 쓰지 않았다.
- 프로세스 환경에 가짜 `GITHUB_TOKEN`·`ANTHROPIC_API_KEY`·`OPENAI_API_KEY`·`GEMINI_API_KEY`를 넣었다. 모든 가짜 값은 `FAKE…` 접두어라 파일에서 바이트로 찾을 수 있다.
- gateway와 runner를 띄워 mock 요청을 한 번 보내고(`waiting_pi`까지 진행), 잘못된 토큰으로 HTTP·WebSocket 접속을 시도하고, `status`·`doctor`·`send --no-wait`를 돌렸다. 그 뒤 임시 폴더 전체(stdout·stderr, gateway·runner 상태 폴더의 sqlite·로그·이벤트, 작업 폴더)를 가짜 값으로 검색했다. 이 검색이 이 절의 "0건"이다.
- 실패 경로는 따로 돌렸다: 잘못된 `runner_token`을 가진 runner, `gateway.url`을 `http://`로 둔 runner·`watch`·`send`, 같은 설정의 `labhq up`/`down`.

## 확인한 것

### 막혀 있음 (재현 안 됨)
- gateway uvicorn 로그: `WebSocket /ws/runner?token=[REDACTED]`, `/ws/client?token=[REDACTED]`, `GET /api/health?token=[REDACTED]`. 접속 성공(accepted)과 거절(403) 모두 그렇다. 근거는 `labhq/security.py`의 `TokenRedactionFilter`·`gateway_log_config`(04fb5193)이고, `labhq/cli.py:2324`의 `labhq gateway`가 쓴다. HTTP `Authorization` 헤더 값은 어느 로그에도 없다.
- 잘못된 토큰의 runner: 로그는 `server rejected WebSocket connection: HTTP 403`뿐이고 URL이 없다.
- `labhq status`·`doctor`·`send`의 출력과 gateway·runner 상태 폴더, 작업 폴더에서 runner·client 토큰, GitHub 토큰, 세 API 키는 0건이다.

### 노출 (재현됨)

**E1. runner 연결 실패 로그가 URL째 찍는다.**
- 코드: `labhq/runner/daemon.py:752`가 `…/ws/runner?token=<runner_token>` URL을 만들고, `:796`이 `log.warning("gateway connection lost/failed: %s (retry in %.0fs)", e, backoff)`로 예외 문장을 찍는다. `websockets.connect`가 스킴이 맞지 않으면 `InvalidURI`를 내고 그 문장이 URL 전체를 담는다: `http://127.0.0.1:18787/ws/runner?token=… isn't a valid URI: scheme isn't ws or wss`.
- 재현: `gateway.url`을 `http://127.0.0.1:18787`로 둔 runner. 재시도 간격(1·2·4초…)마다 한 줄이 나온다. 직접 실행하면 stderr(4줄), `labhq up`이면 상태 폴더 `gateway-state/logs/runner.log`에 **7줄**이 파일로 남는다. 로그 폴더는 보고·공유되기 쉬운 곳이다.
- `gateway.url` 스킴 실수는 현실적이다. `_http_base`(`cli.py:215`)가 `ws://`→`http://`로 바꿔 쓰므로 사용자가 두 표기를 섞기 쉽다.
- 이 로그는 `logging.basicConfig`(`cli.py:2210`)의 루트 handler로 가고, gateway와 달리 runner 쪽에는 토큰 가림 filter가 없다.

**E2. `labhq watch` traceback.**
- 코드: `labhq/cli.py:1568`(`_watch`)과 `:1588`(`_send_and_wait`)이 `…/ws/client?token=<client_token>` URL을 만들어 `websockets.connect`에 넘긴다. `watch`는 예외를 잡지 않아 같은 `InvalidURI`가 traceback의 마지막 줄로 터미널에 나온다: `websockets.exceptions.InvalidURI: http://127.0.0.1:18787/ws/client?token=… isn't a valid URI`.
- `send --no-wait`는 HTTP API만 쓰고 "gateway에 연결할 수 없습니다: http://127.0.0.1:18787"만 출력해 토큰이 없다. `_send_and_wait`(기다리는 `send`)는 같은 `websockets.connect`를 쓰므로 코드상 같은 문제가 있지만 이번에 돌리지는 않았다.
- 터미널 출력이라 파일로 남지는 않는다. 다만 CI·화면 공유·이슈 붙여넣기에 흔히 들어간다.

### 의도된 출력
- `labhq demo`: `cli.py:1828`(`_phone_url_lines`), `:1938`, `:1949`가 `?token=…`이 든 주소를 콘솔에 일부러 낸다. 폰·브라우저로 여는 데 필요하기 때문이다. 포그라운드 명령이라 로그 파일에는 남지 않는다. 화면을 녹화하거나 CI에서 돌리면 노출된다.
- `labhq open`: `cli.py:218-231`은 토큰을 브라우저에만 주고 반환 문장에는 넣지 않는다("토큰은 이 브라우저에 저장됩니다"). 코드로만 읽었다.

## 확인하지 못한 것

- 실제 직원 엔진(Claude·Codex·Gemini CLI)을 띄우지 않았다. mock 엔진은 자식 프로세스를 만들지 않는다. 엔진 실패 증거에서 환경 비밀을 가리는 코드(`labhq/adapters/base.py:1487-1522`, `MIN_SECRET_CHARS`)와 자식 환경에서 GitHub 토큰을 빼는 코드(`:1026`)는 읽기만 했다.
- GitHub 연동 오류 문장에 토큰이 들어가는지(`labhq/integrations/github.py`의 `sanitize`)는 실제 네트워크 없이는 보지 못했다.
- `bench.py:384`와 `runner/approvals.py:283`의 uvicorn은 가림 filter 없이 `log_level="warning"`이라 접근 로그를 내지 않는다. 코드로만 읽었다.
- 브라우저 쪽(주소창·`Referer`·`localStorage`)과 HPC 세션 로그는 범위 밖이다.

## 고칠 범위 (감사 때의 제안, 아래 "수정과 재확인"에서 실제로 한 것)

| 대상 | 변경 | 파일 |
|---|---|---|
| E1 | runner·CLI의 루트 로그 handler에 `TokenRedactionFilter`를 단다. gateway가 이미 `gateway_log_config`로 하는 일과 같다. 예외 문장까지 한 번에 가려져 같은 부류(다른 예외가 URL을 담는 경우)를 구조적으로 닫는다 | `labhq/cli.py:2210`(basicConfig 직후), `labhq/security.py` |
| E1 보조 | `daemon.py:796`의 `e`를 `redact_tokens(str(e))`로 | `labhq/runner/daemon.py` |
| E2 | `watch`·`_send_and_wait`가 `InvalidURI`·접속 실패를 잡아 URL 없이 "gateway.url은 ws:// 또는 wss://여야 합니다" 같은 안내를 낸다. 내용이 URL을 담지 않게 한다 | `labhq/cli.py:1568`, `:1588`, `_run_websocket`(`:488`) |
| 시험 | 가짜 토큰이 든 `InvalidURI`를 runner 로그와 `watch`가 가리는지(로그 캡처, 예외 문장 검사). 실제 gateway·네트워크 불필요 | 새 시험 파일 하나 |
| 문서 | manual 설정 장에 `gateway.url`은 `ws://`·`wss://`여야 하고 http로 쓰면 어떻게 되는지 한 줄 | `docs/manual/15-config.md`·`15-config.en.md` |

- 한 PR로 묶을 수 있다(약 세 파일과 시험 한 파일, 코드는 수십 줄). `labhq/security.py`의 filter가 이미 있고 시험도 있어 위험은 낮다.
- 이 감사가 닫는 것은 "gateway 콘솔 로그의 토큰"(이미 해소)이고, #37의 다른 항목(여러 인스턴스, 폰 접속)은 이 문서의 범위가 아니다.


## 수정과 재확인 (같은 PR)

| 항목 | 수정 | 파일 |
|---|---|---|
| E1 | 모든 명령의 루트 로그 handler에 `TokenRedactionFilter`를 단다. `_configure_logging()`이 `logging.basicConfig` 뒤에 `redact_root_logging()`을 부른다. gateway가 쓰던 같은 filter라 `daemon.py`의 warning도 이 filter를 지나가므로 `daemon.py`는 고치지 않았다 | `labhq/cli.py`, `labhq/security.py` |
| E2 | `_run_websocket`이 `InvalidURI`를 따로 잡아 주소 없이 "gateway.url은 ws:// 또는 wss://로 시작해야 합니다"를 낸다. `watch`와 `_send_and_wait`가 모두 이 함수를 지난다 | `labhq/cli.py` |
| 문서 | manual 15장 한·영에 `gateway.url`은 `ws://`·`wss://`만 쓴다는 한 줄 | `docs/manual/15-config.md`·`.en.md` |
| 시험 | 루트 handler가 runner warning의 토큰을 가리는지, 필터가 중복되지 않는지, `watch`가 주소 없이 끝나는지, traceback을 `redact_tokens`가 가리는지 | `tests/test_security.py` 4건 |

- 같은 임시 인스턴스 시나리오를 수정 코드로 다시 돌렸다. runner 로그는 `…/ws/runner?token=[REDACTED] isn't a valid URI…`이고, `labhq up`이 남긴 `gateway-state/logs/runner.log`와 `logs/` 아래 stderr에서 토큰은 0건이다. 검색에 걸린 것은 설정 파일(`cfg4.yaml`) 자신뿐이다. `watch`는 위 한 줄 안내만 내고 종료 코드 2다.
- `tests/test_security.py` 19건, `-k "cli or security or send or watch or websocket"` 701건이 통과했다(Windows 로컬).
- 남은 것: `labhq demo`의 의도된 출력은 그대로다. 엔진 CLI 실패 증거와 GitHub 오류 문장은 이번에도 코드로만 읽었다.
