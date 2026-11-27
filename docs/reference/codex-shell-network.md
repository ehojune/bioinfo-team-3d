# Codex 직원의 셸 네트워크 (#1055, #1538)

조회용 문서입니다. 구현은 `labhq/adapters/codex.py`의 `SHELL_NO_PROXY_ENV`에 있습니다.

## 현재 설정

웹 검색을 끈 read-only 직원에게만 `shell_environment_policy.set`으로 아래 값을 줍니다. 이 설정은 직원의 셸 명령에 적용됩니다.

| 환경 변수 | 값 |
|---|---|
| `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`와 소문자 변수 | `http://labhq-shell-disabled.invalid.` (끝의 점 포함) |
| `NO_PROXY`, `no_proxy` | 빈 문자열 |

기본 프록시 설정을 따르는 HTTP 클라이언트의 우발적인 외부 조회를 막습니다. 프록시를 쓰지 않는 연결에는 이 조치가 적용되지 않습니다. 프로세스 단위 격리는 #666에서 다룹니다. 쓰기 직원과 웹 검색을 허용한 직원에는 이 값을 넣지 않습니다.

## Windows 진단

1. 어댑터가 만든 `web_search`와 `shell_environment_policy.set`을 확인합니다. 웹 검색이 꺼진 read-only 조합에만 위 프록시 값이 있어야 합니다.
2. 직원용 `CODEX_HOME`의 sandbox 로그에서 `offline firewall settings changed`와 `stored_ports` / `desired_ports`를 대조합니다. #1538에서는 옛 프록시 `http://127.0.0.1:9`를 넣는 조합과 그 외 조합을 바꿀 때 `[9]`와 `[]`가 엇갈렸습니다. 현재 값은 명시된 loopback 포트가 없어 이 설정에서 포트 9를 추가하지 않습니다. UAC가 뜨면 로그에서 불일치 여부부터 확인합니다. [Codex 방화벽 설정 코드](https://github.com/openai/codex/blob/main/codex-rs/windows-sandbox-rs/src/setup.rs)
3. 승인된 실환경 확인에서는 같은 직원용 `CODEX_HOME`에서 read-only(웹 검색 꺼짐)와 쓰기 직원을 번갈아 실행합니다. 두 쪽 모두 셸이 시작되는지, 위 포트 불일치와 UAC 요청이 생기는지 확인합니다. read-only 쪽에서는 기본 `urllib`로 승인된 시험 URL에 HTTP·HTTPS 요청을 보내 프록시 이름 해석 단계에서 실패하는지 봅니다.

Windows 회귀 테스트는 직원 조합별 셸·웹 설정을 확인했습니다. `urllib`의 HTTP·HTTPS 실패는 프록시 이름 해석 실패를 모의해 확인했습니다. 실제 Codex 직원 교대 실행과 방화벽 전환은 미검증입니다.

## 이전 관측

- #1055에서 read-only 리뷰어의 셸 `urllib` 호출이 외부 서버에 닿았습니다. 요청 단위 조회 집계는 `labhq_annot`의 HTTP 송신만 셌습니다.
- Codex 0.165.0-alpha.1.2의 Windows 시험에서 elevated 설정의 외부 호출은 성공했고 unelevated는 `WinError 10061`로 실패했습니다. 직원은 PI 개인 경로 읽기 거부(#382)를 위해 elevated를 씁니다.
- 2026-11-05 통제 시험에서 옛 프록시를 켜고 끈 셸의 간단한 명령은 성공했습니다. 프록시를 켠 외부 HTTP 시험은 시작 전 UAC에서 멈춰 판정하지 못했습니다. 당시 UAC 원인은 미확인입니다.
