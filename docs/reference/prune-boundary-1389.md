# #1389 경계 골격 조사

## r2 결과

- `claude_sandbox.py`, `protected_transport.py`, fixture plan과 전용 시험을 제거했다. 제품 302줄, plan script 101줄, 시험 491줄 삭제.
- `tests/test_auth_broker.py:315`는 보존 중인 process boundary와 observer의 lifecycle도 검증한다. broker와 시험을 보류한다.
- `tests/test_boundary_probe.py:468`부터 보존 중인 seal fixture validator의 수락·거부도 검증한다. probe와 시험을 보류한다.
- `git grep`에서 제거한 모듈·script를 사용하는 제품·시험·CI·현재 manual 참조는 찾지 못했다. 동적 경로 일반에 대한 부재 증명은 아니다.
- 전체 수집 16,138건. 관련 시험 873 passed, 39 skipped, 2 xfailed. 전체 실행·mock 종단·최종 CI와 리뷰·실제 요청 비교는 남았다.

## r1 조사 기록

기준: `0e381aec0f462300052ab04637b3efa2f2cb644d`. 정적 검색이며 실제 실행·삭제는 하지 않았다.

## 제품 후보 5개

| 경로 | 참조 및 구분 |
|---|---|
| `labhq/runner/process_boundary.py` | `labhq/adapters/base.py`의 `ProcessBoundaryGate.refusal`; 시험과 진단 CLI. 연결 제거 후 삭제 |
| `labhq/runner/boundary_observer.py` | `labhq/adapters/base.py`의 `observe_lifecycle`; 시험만 그 밖에 import. 연결 제거 후 삭제 |
| `labhq/runner/auth_broker.py` | 제품 import 없음; `tests/test_auth_broker.py`와 동적 경로를 받는 integration probe의 synthetic broker |
| `labhq/runner/claude_sandbox.py` | 제품 import 없음; `tests/test_claude_sandbox.py`, `tests/test_claude_sandbox_boundary.py`만 참조 |
| `labhq/runner/protected_transport.py` | 제품 import 없음; `tests/test_protected_transport.py`만 import |

`labhq/runner/codex_sandbox.py`는 유지: `labhq/doctor.py`, `labhq/init_wizard.py`, `labhq/runner/daemon.py`가 사용한다. `auth_broker.py`는 이름과 달리 실제 provider 연결이 아닌 synthetic 단계다.

## 다음 revision에 필요한 경로

독립 후보: 위 5개 중 `auth_broker.py`, `claude_sandbox.py`, `protected_transport.py`와 `tests/test_auth_broker.py`, `tests/test_claude_sandbox.py`, `tests/test_claude_sandbox_boundary.py`, `tests/test_protected_transport.py`. 단 `tests/test_auth_broker.py`는 process boundary/observer와도 결합한다. 전체 검증 전 독립 안전성은 확정하지 않는다.

연결 제거와 함께: `labhq/adapters/base.py`, `labhq/runner/daemon.py`, `labhq/runner/process_boundary.py`, `labhq/runner/boundary_observer.py`, `tests/test_boundary_wiring_666.py`, `tests/test_boundary_observer.py`, `tests/test_process_boundary.py`, `tests/test_process_boundary_seal_contract.py`, `tests/fixtures/process_boundary_seal/{cases.json,controls.json,lifecycle-v1.json,validator.py}`. `daemon.py`는 W2 작업 종료 뒤에만 편집한다. `Runner(..., process_boundary=True)`의 명시적 거절도 사라지므로 API 동작 변화다.

추가 조사·정리 후보: `scripts/boundary_probe/{__init__.py,integration_probe.py,probe.py}`, `tests/test_boundary_probe.py`, `scripts/boundary_fixture_plan.py`, `tests/test_boundary_fixture_plan.py`. 앞 둘은 #666 probe와 시험이고 fixture plan은 사용자 문서에 노출되어 있으므로 다음 claim에서 명시적으로 결정한다. `config/ci/windows-boundary-v1.json`에서 삭제하는 시험의 inventory와 사유를 같이 정리한다. `docs/manual/04-install.md`, `.en.md`, `docs/manual/11-safety.md`, `.en.md`는 해당 기능을 삭제할 때 같이 고친다. 기존 reference·status·patch notes는 과거 기록으로 보존하되 끊긴 현재 안내를 확인한다.

`labhq/settings.py`에서 위 모듈명과 `process_boundary` 문자열 참조는 찾지 못했다. 다른 워커의 settings 변경 후 재검색한다. `scripts/boundary_probe/integration_probe.py`는 파일 경로로 모듈을 동적 로드하므로 정적 import 검색만으로 호출 0을 증명하지 못한다. VM 준비 스크립트와 안내는 이 다섯 모듈의 직접 import가 아니므로 이번 최소 삭제 목록에서 제외하고 별도 결정한다.

검증: `git grep`으로 제품·시험·config·manual의 이름과 `ProcessBoundaryGate`를 검색했다. 삭제 줄 수·시험·CI·mock 종단·실제 요청 비교는 미실행이며 다음 revision의 gate다.
