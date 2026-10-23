## #678 task 시작 때 디스크 여유 확인과 bulk_data_root (base c075b416)

- 바꾼 것: `Runner._disk_gate`가 task를 시작하기 전에 작업 드라이브 여유를 읽는다. `runner.disk_fail_free_gb`(기본 2 GiB, 0이면 끔) 아래이면 시작하지 않고 `disk_space` 카드(PI 결정 대기)로 묻는다: 승인하면 시작, 거절·무응답이면 이유를 남기고 시작하지 않는다. 경고 기준(10 GiB·5%) 아래이면 작업판 경고만. 드라이브를 읽지 못하면, 환경 자동 수정(`facilities_fix`)이면 막지 않는다. 선택 설정 `runner.bulk_data_root`가 있으면 `_bulk_data_hint`가 직원 브리핑(task context)에 "큰 원자료는 이 폴더 아래에" 한 줄과 여유 공간을 넣는다. 통제 구역에 걸치면 브리핑에 쓰지 않고 경고를 남긴다. 카드 종류 `disk_space`를 gateway PI 결정 목록·웹 라벨·`pi_text`에 추가. manual·예시 설정 두 사본 갱신.
- 정책: `bulk_data_root`는 작업 폴더 밖이라 새 쓰기 허가를 주지 않는다. 쓰기는 기존 승인 판정을 그대로 거친다(정책 엔진은 바꾸지 않았다).
- 검사: 새 test 8건(수정 전 7건 실패)·웹 1건. 관련 455 passed.
- 한계: 범위 밖으로 둔 것 — 큰 다운로드·압축 해제 직전 검사, doctor의 fail 단계(2 GiB). 실제 디스크·실운영 확인 없음(여유 값은 monkeypatch). `tests/test_doctor.py::test_doctor_warns_about_parent_session_markers_and_pi_skills`는 이 세션의 환경 변수 때문에 로컬에서 실패하며 이 변경과 무관하다.
