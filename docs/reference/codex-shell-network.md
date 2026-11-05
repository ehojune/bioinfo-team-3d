# Codex 직원의 셸 네트워크 (#1055)

조회용 문서입니다. 동작은 `labhq/adapters/codex.py`의 `SHELL_NO_PROXY_ENV`에 있습니다.

## 무슨 일이 있었나
② 연구 5차(`req_8175e28b55`)의 블라인드 리뷰어(`sci_reviewer`, Codex `gpt-6.1-sol`, `sandbox: read-only`, 도구·MCP 없음)가 셸 안에서 Python `urllib`로 Europe PMC·RCSB·ChEMBL을 직접 호출했습니다. 요청 단위 조회 집계(`lookup_admission`)는 `labhq_annot`의 HTTP 송신만 세므로 이 호출은 세지 않았습니다. 리뷰어의 9건은 스스로 적은 기록이었습니다.

## 시험 (Codex 0.165.0-alpha.1.2, Windows, staff `CODEX_HOME`, 셸 `urllib` 한 줄)

| 설정 | 결과 |
|---|---|
| adapter와 같은 `:read-only` 확장 프로필 + `windows.sandbox="elevated"` | 외부 호출 성공 (HTTP 200) |
| 위 + 프로필 `network = {enabled = false}` | 성공 |
| 위 + `sandbox_workspace_write.network_access=false` | 성공 |
| `-s read-only` + elevated | 성공 |
| `-s workspace-write` + `network_access=false` + elevated | 성공 |
| `-s read-only` + `windows.sandbox="unelevated"` | 막힘 (`WinError 10061` 연결 거부) |

elevated sandbox에서는 시험한 어떤 설정도 셸 네트워크를 막지 못했습니다. unelevated는 막았지만 PI 개인 경로 읽기 거부(#382)가 elevated에 의존하므로 쓰지 않습니다. `codex sandbox` 하위 명령은 read-only에서도 연결돼 `codex exec`의 sandbox와 정책이 다르므로 근거로 쓰지 않았습니다.

## 지금의 조치와 한계
read-only이고 웹 도구가 없는 Codex 직원의 **셸에만** `shell_environment_policy.set`으로 닫힌 프록시(`http://127.0.0.1:9`)와 빈 `NO_PROXY`를 줍니다. Codex 프로세스 자신에는 주지 않습니다(모델 연결 보호).

- 막는 것: 환경 프록시를 따르는 기본 HTTP 클라이언트(`urllib`, `requests`, `curl` 등)의 우연한 조회.
- 못 막는 것: 프록시 변수를 무시하는 코드(`ProxyHandler({})`, 소켓 직접 연결). 이것은 경계가 아니라 실수 방지입니다.
- 열어 둔 직원: `sandbox`가 read-only가 아닌 직원(패키지·참고 파일을 받아야 함)과 웹 도구를 준 직원.
- 리뷰어가 공개 원문을 읽어야 하는 일은 이 조치로 `labhq_annot` 경로로만 가능해집니다. 블라인드 초안 품질에 주는 영향은 ② 6차에서 봅니다.
- **통제군 (2026-11-05, `codex.exe` a159911f, 같은 설정으로 한 번씩):** 아래 표. 프록시를 켜도 셸은 정상으로 돌았습니다. 켠 쪽의 외부 HTTP 차단은 UAC 창 때문에 **확인하지 못했습니다**.

| | echo | `python -c "print(1+1)"` | 셸 `urllib` 외부 호출 |
|---|---|---|---|
| 프록시 없음 | exit 0 | exit 0 | 서버에 닿음 (HTTP 403) |
| 프록시 켬 (`SHELL_NO_PROXY_ENV`) | exit 0 | exit 0 | 시작 전 UAC(`consent.exe`)가 떠 중단. 승인하지 않고 프로세스를 종료함 |

- 두 실행 모두 어댑터와 같은 `:read-only` 확장 프로필, `windows.sandbox="elevated"`, staff `CODEX_HOME`, `gpt-6.1-sol`, 웹 도구 없음. `~/bin/codex.cmd` shim은 unelevated를 강제해 쓰지 않았습니다.
- UAC의 원인은 프록시로 보지 않습니다. 이 PC에서 elevated sandbox를 쓰는 `CODEX_HOME` 두 곳이 같은 sandbox 계정 비밀번호를 서로 덮어쓰는 #435일 가능성이 큽니다. 근거는 없고 같은 실행을 다시 돌리지 않았습니다.
- 그래서 "프록시 켠 쪽에서 외부 HTTP가 막히는가"는 이 표로 닫히지 않습니다. 앞서 `codex exec` 6가지 시험(위 표)에서 환경 프록시를 따르는 클라이언트가 막힌다는 근거는 unelevated 한 줄뿐입니다.

근본 대책은 #666(전체 process 격리)입니다.
