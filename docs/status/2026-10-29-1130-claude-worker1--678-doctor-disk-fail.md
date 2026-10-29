## 단계
#678 현재 main 대조와 남은 한 칸 — Refs #678 (닫지 않음).

## 대조 (main `80db3335`, 코드·시험)
| #678 요구 | 상태 | 근거 |
|---|---|---|
| doctor: 작업 폴더·gateway 상태 드라이브 여유가 기준(10 GiB 또는 5%) 아래면 warn | 있음 | `doctor._disk_rows`(#763·#847), `test_disk_preflight.py` |
| doctor: 더 낮으면(예 2 GiB) **fail** | **없었음 → 이 PR** | `_disk_rows`는 warn만 냈음 |
| runner: task 시작 때 확인, 기준 아래면 멈추고 PI 카드 | 있음 | `Runner._disk_gate`, `runner.disk_fail_free_gb`(기본 2)·`disk_space.py`(#920), `test_disk_start_678.py` 10건 |
| runner: 알려진 입력 크기의 복사 전 확인 | 있음 | #852 `step_inputs.py`, `test_step_inputs_665.py` |
| 큰 원자료 위치 `runner.bulk_data_root` + 직원 브리핑 | 있음 | `daemon._bulk_data_hint`(통제 구역이면 제외), 예시 설정 2곳, `test_disk_start_678.py` |
| 큰 다운로드·압축 해제 **직전** 확인(셸 curl·wget, 크기 미상) | 없음 | 명령 해석이 필요해 이전 배정에서 범위 밖으로 둠 |
| 실제 디스크·운영 PC 검증 | 안 함 | 범위 밖 |

## 한 일 (이 PR)
- `labhq/doctor.py`: `workspace disk`와 `gateway state disk` 행이 여유 `< runner.disk_fail_free_gb`(기본 2 GiB, runner의 시작 기준과 같음)이면 `fail`입니다. 0이면 fail 없음. 임시·직원 홈 드라이브는 warn까지, 조회 실패도 warn 그대로입니다. 안내 문구에 `disk_fail_free_gb`를 추가했습니다.
- `docs/manual.md` doctor 디스크 절에 반영, 시험 2건 추가, 시험 1건의 기대(1 GiB 남은 gateway 상태 드라이브)를 warn→fail로 고치고 이름에서 "still warns"를 "still gets its own row"로 바꿨습니다. 의도(UNC 등 st_dev 0 볼륨도 자기 행을 가짐)는 그대로입니다.
- 효과: `labhq doctor`가 이 두 드라이브가 2 GiB 미만이면 종료 코드 1을 냅니다(기존 fail 행과 같은 처리).

## 테스트 결과
- `test_disk_preflight.py`·`test_doctor.py`·`test_disk_start_678.py` 통과(1건 제외: `test_doctor_warns_about_parent_session_markers_and_pi_skills`, 변경 없이도 이 세션에서 실패하는 환경 문제). doctor·disk·cli·init·up·status·health 키워드 2352건 통과.
- 미검증: 실제 디스크 여유가 부족한 PC에서의 동작.

## 남은 것 (#678 열어 둠)
- 셸 다운로드·압축 해제 직전 확인(크기 미상): 명령 해석과 별도 설계가 필요합니다.
- 실제 디스크·실운영 검증.

## 바꾼 파일
labhq/doctor.py, docs/manual.md, tests/test_disk_preflight.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
