# #1456 — 작업 폴더의 개인 경로 오탐 진단

2026-11-17, r1. [배정](https://github.com/ehojune/bioinfo-team-3d/issues/1456) · [최초 관측](https://github.com/ehojune/bioinfo-team-3d/issues/1445#issuecomment-6942177146). 현장 원문·경로는 로컬 전용 기록에 보존했다. 아래 경로는 익명화했다.

| 확인 | 결과 |
|---|---|
| 설정과 실제 목록 | 설정 30개, 해당 task의 MCP 환경 목록 37개. 설정 원문으로는 불일치, 실제 목록으로는 작업 root 하나만 일치 |
| 기준 폴더 | 카드 workdir와 `LABHQ_WORKDIR`가 같다. 사용자 홈으로 잘못 푼 사례가 아니다 |
| 일치 | `cd "D:/lab/runs/day/task_bio"`가 runner가 추가한 `D:/lab/runs`에 일치 |
| 허용 범위 | task 폴더와 `inputs/precedent`가 `LABHQ_PRIVATE_OPEN_READS`에 이미 있다 |
| 판정 재생 | 저장한 카드·MCP 환경·staff 설정으로 `evaluate_tool`을 호출해 카드의 `ask`와 사유 전체를 재현. 셸 명령은 실행하지 않음 |
| 코드 대조 | 진단 checkout `407d66d6`과 실행 기준 `979d5a1b`의 policy/private_paths/pi_language/tool_command는 동일. runner의 경로 추가 블록과 tool_explanation도 동일 |

## 원인

1. `labhq/runner/daemon.py:2894–2911`: artifact 계약에서 작업 root와 task inputs를 private 목록에 추가한다. 형제 task 보호와 입력 쓰기 방지를 위한 목록이 개인 경로 판정에도 쓰인다.
2. `labhq/tool_command.py:17,37`: 실제 명령의 `*`와 `wc`가 좁은 읽기 문법 밖이어서 `read_commands`와 `managed_shell_read`는 `None`을 반환한다. 읽기 허용 예외가 성립하지 않는다.
3. `labhq/policy.py:2639`: 이후 원문 전체에서 private 항목을 찾으므로 자기 작업 폴더의 `cd`가 상위 작업 root에 걸린다. 별도 workspace 문법도 `cd ...;`를 거절한다. 승인 요청 자체와 개인 경로라는 사유를 구분해야 한다.
4. `labhq/tool_explanation.py:154`: 사유에 `private_paths`만 있으면 개인 파일·자격 증명 접근과 높은 위험으로 설명한다. 실제 경로 접근이 확인되지 않은 `read scope is ambiguous`도 같은 설명을 만든다.

익명 재현:

```sh
cd "D:/lab/runs/day/task_bio"; ls -la inputs/precedent; wc -l inputs/precedent/*; cat inputs/precedent/.labhq-inputs.json | head -50
```

## PI 설명 실패

- 저장된 설명 task는 `ok=true`, exit 0. 답변은 한국어 230자, 코드·경로 없음, **3문장**이었다.
- `labhq/pi_language.py:202–205`의 2문장 상한만 충족하지 못해 `failed`가 됐다. 이 카드에서는 예산 부족·호출 실패·timeout이 원인이 아니다.
- 형식 제한 완화나 별도 설명 개선은 이번 경로 수리와 분리해 판단한다. 호출 재실행은 하지 않았다.

## 현재 계약 — r4

[승인 계약](https://github.com/ehojune/bioinfo-team-3d/issues/1456#issuecomment-6947828510). r3의 셸 실행 해석은 아래 범위로 축소했다. 당시 리뷰·수리 기록은 PR #1457에 보존한다.

| 범위 | 처리 |
|---|---|
| 명시한 절대경로·home 표기 | 각 경로의 private 자식·credential·열린 하위 폴더·실제 위치를 확인 |
| 단순 경고 문법 | `;`로 이은 cd·pwd·which·ls·cat·head·tail·find와 wc의 literal 인자. 승인 문법 확장 아님 |
| 분기·함수·stack·substitution·동적 실행·상한 | 실행 의미를 추정하지 않고 읽기 범위 미확인. 뒤의 독립적인 명시 경로 근거는 유지 |
| 승인 | 미확인은 ask, 실제 runner의 batch classifier에서도 제외. 파일 거부·입력 쓰기·private 목록·registry 보호 유지 |

남은 P1의 `then cd`, while 조건의 미호출 함수, 33단계 그룹을 보존했다. 각각 직접 닫힌 절대경로를 뒤에 붙인 대조도 확인한다. 기존 실행 의미 문구 단언은 계약에 맞춰 변경했으며 ask/deny 단언과 재현은 유지했다.

`policy._private_decision`의 미확인 사유에는 `closed or credential` 제외 표식을 남긴다. `approval_grants.classify`는 이 표식으로 개별 카드를 유지한다. 표시만 바꾸고 batch 허용으로 새는 결함을 실제 classifier 호출로 검사한다.

실제 카드 수·실행 A·배포 효과는 미검증이다. 실제 요청·인스턴스·HPC·pi_display 문장 상한은 변경하지 않았다.