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

## 다음 수리 범위 제안

- 최소 후보: `labhq/policy.py`, `labhq/private_paths.py`, `labhq/tool_explanation.py`, `tests/test_private_paths.py`, `tests/test_tool_card_review_fixes.py`.
- 작업 폴더의 경로 일치와 실제 닫힌 개인 경로 접근을 구별한다. 문법을 증명하지 못한 명령은 개별 승인으로 유지한다. private 목록의 작업 root를 통째로 빼지 않는다.
- 회귀 양쪽: 자기 task·staged inputs의 개인 경로 사유 오탐 제거, 실제 개인 폴더·credential·형제 task·명시한 private 자식·link 탈출의 차단 유지. 입력 쓰기 보호도 유지한다.
- 이 r1은 진단 문서만 작성했다. 제품 코드·회귀 시험·배포·실제 요청 변경은 없으며 수리 revision claim이 필요하다.

## r2 수리

- 읽기 문법 밖인 Bash 명령은 계속 `ask`다. 닫힌 경로를 확인한 경우와 읽기 범위를 증명하지 못한 경우를 구별했다. 상위 작업 root 일치만으로 개인 파일 접근을 단정하지 않는다.
- 명시한 개인 경로·registry 근거는 남긴다. `private_paths` 목록과 파일·쓰기 보호는 그대로 사용한다. `managed_shell_read=None`은 경로 일치가 아니라 미확인이라는 계약을 명시했다.
- 미확인 카드에는 읽기 범위 확인 사유를 쓴다. 삭제·통제 자료 등 별도로 확인한 위험은 낮추지 않는다.
- 회귀는 자기 task·staged inputs 읽기 허용과 개인 폴더·credential·형제 task·private 자식·link·입력 쓰기 보호를 함께 확인한다.
- 이 수정은 승인 문법을 넓히지 않는다. 위 복합 명령의 카드도 남으므로 실제 카드 수 감소·실행 A 재생·배포 효과는 별도 확인이 필요하다. pi_display 문장 수 제한도 바꾸지 않았다.

## r3 리뷰 수리

- P1: private root 아래 읽기 예외가 하나라도 있으면 root 전체를 경고 근거에서 빼던 결함을 수정했다. 미확인 문법에서도 각 경로의 표기·실제 위치를 대조해 열린 하위 폴더만 예외로 둔다.
- 절대경로·home·상대경로 × settings·history·다른 task·plugin·project 15건을 추가했다. 관련 4개 시험 파일은 717 passed, 10 skipped.
- safety 안내 길이: 한국어 20,168→20,293자, 영어 36,437→36,711자. 각 언어에 한 항목만 추가했다.
- 후속 P1 2건: 상한 뒤에도 경로 표기의 직접 근거를 남기며 filesystem 해석만 제한한다. 명령을 순서대로 읽고 pipeline 안 `cd`를 바깥 폴더 변경으로 쓰지 않는다. 상한·순서·pipeline 7건 추가, 표적 42건 통과.
- 다음 리뷰의 OR guard·위치 builtin P1 2건도 수리했다. 앞선 폴더 이동에서 도달 가능한 위치만 다음 명령으로 넘기고 `shell_cd_bases`의 위치 표기를 재사용한다. guard·pushd·builtin·`--` 5건 추가, 표적 47건 통과.
- guard 실행 분기 4건과 background·command wrapper 2건도 추가했다. 종료한 분기와 건너뛴 분기를 구별하고 background 위치 이동을 다음 명령에 적용하지 않는다.
- 관련 4개 파일 729 passed, 10 skipped. 분기·wrapper 최종 보완 뒤 표적 53 passed이며 background의 기존 `deny`도 유지했다.
- 그룹·subshell·디렉터리 복원 P1 2건도 수리했다. 상태에 OLDPWD·directory stack을 보존하고 subshell이 끝나면 바깥 상태로 돌아간다. 양방향 7건 추가, 표적 60건 통과.
- 최종 관련 4개 파일: 742 passed, 10 skipped.
- command substitution·popd -n의 P1 2건도 수리했다. substitution 종료 시 바깥 폴더로 복원하고 pushd·popd의 `-n`은 stack만 바꾼다. 양방향 4건 추가, 표적 64건 통과.
- 해당 수리 뒤 관련 4개 파일: 746 passed, 10 skipped.
- process substitution·indexed popd의 P1 2건도 수리했다. substitution 안팎과 선택한 stack 항목을 구별한다. 격리 Bash fixture·help와 대조했고 회귀 3건 추가, 표적 67건 통과.
- 해당 수리 뒤 관련 4개 파일: 749 passed, 10 skipped.
- indexed pushd·미호출 함수 P1 2건도 수리했다. selector를 stack 회전으로 판정하고 선언 본문은 직접 호출될 때만 반영한다. 양방향 5건 추가, 표적 72건 통과.
- 해당 수리 뒤 관련 4개 파일: 754 passed, 10 skipped.
