# #1295 D — 첫 출력을 기다린 뒤 첫 명령을 보낸다

- 원인: D단계 1차 접속이 셸 단계 전에 끊겼습니다. 게이트웨이가 로그인 처리 중 들어온 입력에 접속을 끊는 것으로 보입니다.
- 바뀐 것: `labhq/hpc_session.py`(`first_output_wait_s`·`settle_s`·`diag_dir`, 첫 출력 대기, 실패한 접속의 원문을 로컬 진단 폴더에만 기록), `labhq/hpc_site.py`(프로필 `first_output_wait_s`, 진단 폴더는 저장소 밖일 때만, `remote_exec: false` 검사 완화), `tests/fake_hpc_gateway.py`(`--kick-early-input`), 시험, manual 15 한·영, `docs/reference/hpc-d-stage-1295.md`.
- 시험: 대기 없이 연결하면 끊기고 진단 로그에 원문이 남으며 오류 문장에는 없음, 대기하면 통과, 조용한 게이트웨이는 대기 뒤 진행, 진단 폴더가 없으면 아무것도 쓰지 않음·환경변수, 프로필 값과 저장소 안 진단 폴더 무시, 설정값 검사.
- 한계: 대기 기본값은 0(기다리지 않음)이라 기존 사이트는 그대로입니다. 실제 게이트웨이에서의 효과는 병합 뒤 D단계 2차에서 확인합니다.
