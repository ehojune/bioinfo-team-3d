# 시설팀 지원 요구 (#983)

`runner.facilities_support`는 profile의 지원 요구이며 직원을 추가하거나 끄지 않습니다. 기본 `unspecified`; 생략한 기존 설정도 그대로 보존합니다. init `--force`는 기존 명시 값을 복사합니다. 오타·null은 설정 오류입니다. 구버전으로 downgrade하면 이 gate를 보장하지 않습니다.

| doctor 관찰 | required | optional | unspecified |
|---|---|---|---|
| 부재·만료 (`absent`) | FAIL | WARN·CSO 대체 | WARN |
| 실행 파일/version·개별 preflight·명시적 비인증 실패 (`blocked`) | FAIL | WARN | WARN |
| login 미지원·timeout·판별 불가 (`unverified`) | FAIL | WARN | WARN |
| dry-run probe 생략 (`unverified`) | FAIL | WARN | WARN |
| mock·force_engine=mock (`mock_only`) | FAIL | WARN | WARN |
| 실행 파일/version·개별 preflight·login 확인 (`ready`) | OK | OK | WARN |

- 활성 custom contract·만료/archived·중복 ID는 doctor의 기존 읽기 전용 roster 규칙을 씁니다. `force_engine` 적용 뒤 시설팀의 증거만 합칩니다. 다른 직원 FAIL은 유지합니다.
- 추가 probe는 없습니다. 기존 version 출력과 login status 결과만 사용합니다. login 성공은 exit 0과 인식한 인증 상태(JSON `loggedIn: true`, `Logged in using …` 등)가 필요합니다. 원문 인증 출력은 지원 행에 넣지 않습니다.
- required 미충족은 기존 fail 집계·configuration readiness·doctor/init exit 1에 연결됩니다. required인 기존 설정의 init dry-run도 exit 1이며 파일은 쓰지 않습니다.
- `ready`와 CSO 대체 표시는 실제 응답·provider 잔여·승인된 수정·원 단계 복구를 증명하지 않습니다. 실제 설치판 복구는 **UNVERIFIED**이며 #983은 OPEN입니다. runtime admission과 기존 login warning 정책은 바꾸지 않습니다.

검증: `tests/test_facilities_support_983.py`, `tests/test_init_wizard.py`, `tests/test_doctor.py`, `tests/test_facilities_fallback_983.py`. 모두 격리 mock/static 근거입니다.
