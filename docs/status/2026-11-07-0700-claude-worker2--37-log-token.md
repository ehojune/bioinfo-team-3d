## 단계
#37 rev2: 로그의 토큰 노출 감사와 수정. 가짜 토큰을 넣은 임시 인스턴스로 두 경로를 재현했고 같은 PR에서 막았다.

## 한 일
- `labhq/security.py`: `redact_root_logging()`을 더했다. 루트 handler마다 `TokenRedactionFilter`를 한 번만 단다. `gateway_log_config()`가 같은 함수를 쓴다.
- `labhq/cli.py`: `_configure_logging()`이 `basicConfig` 뒤에 위 함수를 부른다. `_run_websocket`이 `InvalidURI`를 잡아 주소 없이 안내하고 종료 코드 2를 낸다.
- manual 15장 한·영: `gateway.url`은 `ws://`·`wss://`만 쓴다는 한 줄.
- `docs/reference/log-token-audit-37.md`: 방법, 막혀 있는 경로, 노출 두 건, 수정과 재확인.
- `daemon.py`는 고치지 않았다. runner의 warning이 루트 handler의 filter를 지나가기 때문이다.

## 확인
- `tests/test_security.py` 19 passed(새 시험 4건), `-k "cli or security or send or watch or websocket"` 701 passed(Windows 로컬).
- 임시 인스턴스 시나리오를 수정 코드로 다시 돌렸다. runner 로그는 `token=[REDACTED]`이고 `gateway-state/logs/runner.log`의 토큰은 0건이다. `watch`는 한 줄 안내로 끝난다.

## 하지 않은 것
- 실제 엔진 CLI 실패 증거의 비밀 가림과 GitHub 연동 오류 문장은 코드로만 읽었다.
- `labhq demo`가 콘솔에 내는 `?token=` 주소는 의도된 출력이라 그대로 뒀다.
