# 중단·재개 대조표 (#924)

lookup-only 근거입니다. 사용자 안내는 [manual](../manual.md#중단-조건-한눈에)을 봅니다.

## 설정과 집행 위치

| 안내 | 설정·코드 |
|---|---|
| CLI 호출당 6시간 | [RunnerSettings.task_timeout_s](../../labhq/settings.py#L73), [AgentAdapter의 drain timeout·종료](../../labhq/adapters/base.py#L1227). 요청 전체 독립 기한의 증명은 [#877](https://github.com/ehojune/bioinfo-team-3d/issues/877) 별도 범위 |
| task $5·request $30·사전 예약 기본 off | [BudgetSettings](../../labhq/settings.py#L349), [Orchestrator의 hold·요청 합계](../../labhq/orchestrator/cso.py#L4343). 요청 `budget_usd`와 비용 모름 예약을 포함하며 공급자 청구 보장은 아님 |
| 내장 공개 조회 횟수 | [AnnotSettings.request_lookup_limit](../../labhq/settings.py#L619), [LookupAdmission.admit](../../labhq/runner/lookup_admission.py#L58). SQLite transaction으로 요청별 횟수를 보존; 실패·retry·redirect도 각각 한 번 |
| 일시적 실패 최대 2회 시도 | [OrchestratorSettings](../../labhq/settings.py#L436), [run_step](../../labhq/orchestrator/cso.py#L3634). `max_failure_replans=1`은 별도 재계획 상한; 모든 실패를 두 번 자동 재시도한다는 뜻은 아님 |
| 결정 카드 7일·gate 1시간 | [ApprovalRules](../../labhq/settings.py#L324), [Hub._resume_deadline·expire_resume_card](../../labhq/gateway/server.py#L1528). 기존 resume 카드의 생성 시각을 보존. 다른 결정 카드의 재시작 만료와 새 resume 카드 생성은 별도 동작 |
| 중단 결과를 새 중단으로 재사용하지 않음 | [Hub._recovery_matches](../../labhq/gateway/server.py#L1401)·`recovery_task_id`·`dispatch`의 direct checkpoint. 완료 성공 결과와 live task는 재사용하고, 종료된 interrupted task의 비용·조회 원장은 남긴 채 새 task를 보냄 |
| B 90분·$9·$3·HTTP 9회 / proof·카드 운영 조건 | [#910 실행 계약](https://github.com/ehojune/bioinfo-team-3d/issues/910), [GO 요청](https://github.com/ehojune/bioinfo-team-3d/issues/910#issuecomment-6377065987). 실패 2회 중단은 GO-1의 proof, 10분 응답은 GO-2의 CP1·CP2·예산 카드 조건. 전역 기본값이 아니며 이 개발 시험은 GO를 부여하지 않음 |

## 강제 종료 재현

```powershell
python scripts/recovery_fake_check.py --source <checkout> --output <new-disposable-folder>
python -m pytest -q tests/test_recovery_fake_check.py tests/test_shutdown_recovery.py tests/test_resume_cards_607.py
```

- 선택한 checkout 안의 script와 설치판 interpreter를 사용합니다. `--source`가 script의 checkout과 다르거나 parent의 제품·반복 fixture import가 다른 판본이면 출력·process 생성 전에 거부합니다.
- 실제 Hub·Runner·SQLite·WebSocket·broker를 사용합니다. 2단계 fake DAG의 s1을 끝내고 s2의 loopback 조회 뒤 gateway 또는 runner를 강제 종료합니다.
- 종료는 시험이 만든 process handle만 사용합니다. Windows는 venv redirector 대신 실제 interpreter를 시작하고 PID·역할·nonce를 대조한 뒤 `TerminateProcess`, POSIX는 `SIGKILL`을 보냅니다. 두 역할은 다른 generation으로 재기동합니다.
- 직원 실행은 deterministic mock이며 child의 subprocess 생성은 거부합니다. stdout/stderr를 분리하고 마지막에 보유 handle 전부의 종료를 확인합니다. 실제 모델·외부 서비스·HPC·PI 인스턴스·B/c3 proof는 실행하지 않습니다.
- receipt는 전후 요청·원장·승인·task 수락·adapter 호출·HTTP 횟수·비용·산출 hash·종료/재시작·잔류 수를 보존합니다. kill 전 완료된 plan·s1의 identity와 단일 실행을 요구하고 모든 task의 known 비용을 원장에서 재합산합니다. 완료 phase·같은 task 재실행, 재승인, 원장 누락·중복은 실패입니다. 중단된 단계의 새 task 실행은 허용합니다.
- 시험 driver가 고정한 90초 deadline과 같은 비용/조회 설정을 재시작 후에도 씁니다. 제품의 요청 전체 독립 timer·실제 adapter 취소·공급자 청구 상한을 증명하지 않습니다. 두 case만 실행하고 자동 재시도하지 않습니다.
- #922→#923 병합 뒤 설치판 interpreter로 최신 main 통합 재현이 필요합니다. `repeat_fixture_integrated`는 #923의 `REQUEST_BODY`·`configure` 재사용 여부이며, 복구 barrier/plan·작업 시간/비용·조회 횟수 설정은 이 시험 전용입니다.

## 같은 설치판 최종 검증 (2026-10-24)

W2 인계 설치 `a5fd6bede531524b79b062fe6de8c750884c429d`의 Python 3.14.7에서 `python scripts/recovery_fake_check.py --source <preserved-clone> --output <new-output>`를 실행했습니다. 설치·venv는 변경하지 않았으며 두 case 모두 첫 실행 PASS입니다.

| receipt | gateway | runner |
|---|---|---|
| 재개 승인 / 대기 카드 | 1 / 0 | 1 / 0 |
| 완료 plan·s1 재실행 / 같은 task 중복 | 0 / 0 | 0 / 0 |
| known 비용 / 원장 합계 | $0.04 / $0.04 | $0.04 / $0.04 |
| loopback 조회 / 잔류 | 2 / 0 | 3 / 0 |
| 전후 contract | 일치 | 일치 |
| receipt SHA256 | `52673a0e463cad8856c235f360ef79ff382f98237e308244b0234cf60cb9b3fa` | `37100d9f48e066bc202ece1011ef8a44a29b1530523612b4fba4a52636210476` |

설정 SHA256 `8e328a95d2ce75953a04b840206a84f214007a3c065b37fd5e41d491371a0987`, freeze SHA256 `78b6533d8eab0ec2dea866ca84de0e13e042435b010ff7f0ada08d190399392b`가 W2 인계와 일치합니다. unknown 비용은 이 known-cost fixture가 아닌 consult 회귀로 검사합니다. 제품 독립 hard-stop·실제 청구·연구·HPC는 미검증입니다.

## 개발 근거

base `d40e7b31`에서 runner case가 두 번째 resume 카드를 여는 실패와 step/direct 회귀 2 failed·2 passed를 재현했습니다([범위 조정 요청](https://github.com/ehojune/bioinfo-team-3d/issues/924#issuecomment-6377222088)). 관련 회귀 52건·live adoption/비용 13건·강제 종료 2건·CI selection 85건이 통과했습니다. gateway·runner 모두 승인 1회, s1 실행 1회, 비용 $0.04와 task 원장 일치, 조회 각각 2·3회, 잔류 0입니다. 봇 P2의 새 task 비용 행·합계 동시 누락도 재현한 뒤 거부하도록 보강했습니다. 최종 CI·최종 head 봇 검토·병합·W1/W2 설치판 통합은 미확인입니다.
