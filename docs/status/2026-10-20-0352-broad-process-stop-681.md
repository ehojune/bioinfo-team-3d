## 결론

PR #766. 기능 커밋 `67813c8`로 literal 실행 위치·quote removal 검사를 보강했습니다. 배정된 P1 여섯 건을 수정하고, 원문 ForEach 지적 한 건은 기존 `0997a9a`의 stale 근거를 확인했습니다. 광역 종료는 승인 카드 없이 거부합니다. Refs #681.

## 지적별 근거

| 댓글 | 처리 |
|---|---|
| 4317094579 | shell의 결합형 `-lc`와 cmd `/k` payload 검사 |
| 4317094587 | `command -p`·`--` 및 기존 wrapper의 옵션·operand 처리 |
| 4317094594 | 원문은 head `2941c7e`에서 이미 deny. 새 exact 회귀로 확인; 줄바꿈·앞선 문장이 있는 block도 보강 |
| 4317155926 | `if`·`elif`·`while`·`until`·`time`의 실행 위치 검사 |
| 4317155937 | redirection·파일/fd operand를 argv와 구분 |
| 4317155944 | `env -vS`·붙임값·long option split-string 재귀 검사 |
| 4317155962 | quote/escape fragment를 한 word로 읽고 literal quote removal 적용; Windows 경로 판정 유지 |

## 검증

- 기존 head에 새 회귀를 추가해 45 failed·22 passed → 수정 후 전용 문자열/fake 211 passed. 승인 정책의 정적 test 24개를 포함한 최종 실행은 **235 passed**입니다.
- 각 새 실행 사례에서 guard·policy·flat/nested fake broker의 카드 없는 deny와 pending 없음 확인. PID·조회 명령·인용한 출력 문구를 보존했습니다.
- accident guard 로드 확인, `PYTEST_PLUGINS`·`PYTHONPATH`·`LABHQ_MOCK_GUARD_LOG` 유지, blocked event 0건. 실패명·traceback 전체는 로컬 artifact에 보존했습니다. 설치·공용 venv 변경은 없습니다.
- Python 3.10 AST·공개·diff·patch-notes·notes 검사 PASS. manual LF 기준 162,629→162,703자(+74). 중첩 entry를 금지하는 notes gate에 맞춰 기존 flat patch-note 파일을 사용합니다.

## 남은 것

실제 shell/process/proc_tree integration·PI 운영·HPC·원자료·분석은 실행하지 않았습니다. PID tracker #675·동적 명령 OS 격리는 별도 범위입니다. P2는 lead의 영향·비용 선택 뒤 collector가 처리합니다. CI 대기·새 head 리뷰·P1=0 판정·병합·배포는 supervisor 담당입니다.
