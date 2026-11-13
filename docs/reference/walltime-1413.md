# #1413 7번 — 잡 시간 제한 안내 근거

## r8 구현·검증

- 도구 설명과 일반·연구(CP1/CP2)·재계획·공통 단계 prompt에 예상 실행 소요·근거·약 2배 제한 안내를 넣었다. 계획은 근거를 instruction에 넘기고 직원은 제출 직전 실제 입력·자원으로 갱신한다.
- 추정 불가 시 필요한 정보나 기존 승인 범위의 측정을 요구한다. 사이트/PI 제한 초과는 분할·판단 요청으로 돌리며 묵시적으로 자르지 않는다. 4시간 기본값·API·parser·승인/재제출 보호는 유지했다.
- 관련 4개 시험 파일: **448 passed, 7 skipped**. CSO·계획 추정 2개 파일: **259 passed**. 새 회귀는 prompt 렌더링/dispatch, MCP 도구 설명·기본값/명시값 전달, SGE/PBS/Slurm 시간 전달, core-h 승인 경계를 다룬다.
- 로컬 환경에 pytest-timeout이 없어 `Unknown config option: timeout` 경고 1건이 각 실행에 있었다. 전체 suite·최종 head CI·리뷰 결과는 PR에 기록한다.
- 실제 모델·HPC·PI/trial 실행은 없다. 안내 준수·실제 제출·큐 단축 효과와 과거 8·12시간 선택의 원인은 미검증이다.

## r7 조사 보존본

2026-11-13 · codex-worker2 · r7 · 소스 `f79d6c36f630c44e43930fbbe9392f05bc726731`.
조회용 기록이다. 구현·실제 모델·HPC·PI/trial 실행은 하지 않았다.

## 판단

최소 수정은 **도구 설명과 공통 계획·직원 지시**다. 예상 소요·근거를 적고 약 2배를 명시해 제출하도록 안내한다. 4시간 기본값을 일괄 축소하면 오래 걸리는 정상 잡이 잘릴 수 있으므로 이번에는 유지할 것을 권한다. 관측된 8·12시간은 이 기본값과 다르며, 해당 값을 만든 실제 직원 추론은 이번 코드 조사로 확정하지 못했다.

근거: [배정 r7](https://github.com/ehojune/bioinfo-team-3d/issues/1413#issuecomment-6841397889), [항목 7](https://github.com/ehojune/bioinfo-team-3d/issues/1413#issuecomment-6840720522), [제한 변경 기록](https://github.com/ehojune/bioinfo-team-3d/issues/1375#issuecomment-6839600965). 마지막 기록은 당시 효과 미확인이며, 시작 시각을 이번에 따로 검증하지 않았다.

## 경로와 빠진 안내

아래 줄 번호는 위 소스 기준이다.

| 경로 | 현재 동작 |
|---|---|
| `labhq/tools/hpc_mcp.py:295–310,353–356` | MCP `hpc_submit.walltime` 기본 `04:00:00`. 설명은 `HH:MM:SS`, `reason`은 작업 목적만 요구. 지정값을 broker에 전달 |
| `labhq/runner/daemon.py:3439–3505` | task 신원·읽기 전용·실행 위치·로그인 한도 확인 후 runner 제출 경로 사용 |
| `labhq/runner/hpc_jobs.py:101–163,185–187` | 값 누락·빈 문자열은 다시 4시간. core-h 계산, 제출 인자 확인, 승인 카드에 시간과 reason 표시. 제출 전 파일 불변 확인 |
| `labhq/tools/scheduler.py:112–116,518–556` | 정규화 후 SGE `h_rt`, PBS `walltime`, Slurm `--time`에 전달. `Scheduler.submit`도 기본 4시간. SGE runtime_resource가 비면 시간 인자를 보내지 않음 |
| `labhq/policy.py:2877–2900` | 시간 해석 → 코어 수×시간 → 승인 필요 여부. `hpc_core_hours_threshold`는 **승인 임계값**이며 최대 잡 시간은 아님 |
| `labhq/orchestrator/cso.py:754,809,934,1122,5627` | 일반·연구·재계획과 단계 지시에 HPC 사용·대기 안내는 있으나 예상 소요·근거·2배 기준 없음. `STEP_PROMPT`는 `RESEARCH_STEP_PROMPT`를 포함 |
| `labhq/orchestrator/execution.py:46–63` | 요청 전체 HPC 계획에 실행 위치·산출 경로·대기 안내를 추가. 시간 선택 안내 없음 |
| `agents/core/analyst.yaml:19`, `data_steward.yaml:16`, `bioinfo-agent.yaml:21`, `qc_reviewer.yaml:20` | 분석가의 포괄적인 자원 추정, 다운로드·검사 HPC 사용 안내만 있음 |

## 구현 배정 제안

| 수정 후보 | 최소 변경 |
|---|---|
| `labhq/tools/hpc_mcp.py` | 도구 설명에 예상 실행 소요·근거·약 2배 제한을 `reason`에 기록하고 `walltime`을 명시하라고 안내. 4시간은 추정치가 아닌 호환 기본값임을 명시 |
| `labhq/orchestrator/cso.py` | 한 공통 안내를 일반·연구·재계획과 공통 단계 지시에 넣음. 계획은 각 HPC 단계 instruction에 추정 근거를 전달하고 직원은 제출 직전 입력 크기·코어·선행 실행에 맞춰 갱신 |
| `docs/manual/09-staff-tools.md`, `docs/manual/09-staff-tools.en.md` | 같은 짧은 시간 선택 안내와 한계 반영 |
| `tests/test_step_prompt_rules.py` | 실제 생성 계획·단계 prompt에서 일반·연구·재계획 안내가 전달되는지 확인. 새 필수 schema·기존 계획 변경 없음 |
| `tests/test_hpc_followups.py`, `tests/test_scheduler.py`, `tests/test_policy.py` | 기존 제출·승인·scheduler 회귀 실행. 아래 경계 중 기존 시험에 없는 항목만 추가 |
| `patch_notes/entries/codex-worker2--1413-walltime.yaml`, `docs/status/<구현시각>-codex-worker2-1413-walltime.md` | 구현·검증 결과 기록 |

이번 r7의 쓰기 허용은 이 파일 하나뿐이다. 위 경로는 다음 claim 후보이며 아직 수정하지 않았다. runner·scheduler·policy·직원 YAML·execution.py는 읽기 확인 대상으로 두면 중복 안내와 호환 변경을 줄인다. 시간 생략 거절이나 새 추정 필드를 강제하려면 별도 API 변경 범위를 정해야 한다.

## 안내와 회귀 경계

- 추정 근거는 유사 입력의 실측, 입력량/처리율, 도구·코어 수다. 대기열 시간은 실행 소요에 더하지 않는다. 예: 예상 20분 → `00:40:00`, 예상 2시간30분 → `05:00:00`. 2배와 다른 여유가 필요하면 이유를 적는다.
- 추정치가 없으면 모른다고 적는다. 승인된 작은 사전 측정이나 공개 처리율로 근거를 얻고, 그마저 불가능하면 필요한 입력량·자원 정보를 질문한다. 큰 기본값이나 0/무제한을 추정치처럼 쓰지 않는다. 사전 측정도 기존 실행 계약을 따른다.
- 도구에 주는 표기는 `HH:MM:SS`. 현재 해석기는 `HH:MM`, 일수 표기, 숫자 시간도 받는다. `90:00`은 90시간이며 90분이 아니다. `1-02:00:00`은 26시간. 이를 scheduler 원시 형식과 혼동하지 않도록 안내한다.
- SGE·PBS·Slurm에 같은 명시 시간이 전달되는지 확인한다. SGE 시간 자원 미설정과 사이트별 템플릿은 기존 동작을 유지한다. 예상×2가 사이트/PI 제한을 넘으면 묵시적으로 자르지 말고 분할 또는 판단을 요청한다.
- 승인 경계는 threshold 0에서 항상 질문, 임계값과 같은 core-h는 통과, 초과는 질문이다. scheduler directive·원격 PBS 추가 승인, task 종료·스크립트 변경 시 제출 거절, PI 거절 뒤 재제출 금지도 보존한다.
- 음수·NaN·무한대·분/초 범위 검증은 현재 해석기의 별도 점검 대상이다. 이번 안내 변경에서 parser를 새로 설계하거나 기존 보호를 약화하지 않는다.

검증: AST 기본값·runner fallback 3건, 추출한 순수 함수의 시간 형식 5건·승인 경계 3건으로 11건 PASS. 제품 모듈 기동·scheduler 호출 없이 확인했다. 한국어 금지 표현 3종은 0건이다. 실제 제출, 큐 단축 효과, 모델의 안내 준수는 미검증이다. #1413 7번의 구현 완료를 뜻하지 않는다.
