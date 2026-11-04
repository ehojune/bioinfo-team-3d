## 단계
#1043(#1041 항목 2): 상담 refs 하나가 잘못돼도 요청 전체가 실패하던 문제.

## 한 일
1. `Runner._consult_workspace_input`: 없는 파일·경로가 아닌 문구는 `left_out`에 사유와 함께 담고 건너뛴다. 폴더·절대 경로·`..`·드라이브·링크·작업 폴더 밖·facilities 통제 구역 파일은 이전처럼 상담 전체를 거부한다. 링크 검사는 존재 확인 뒤로 옮겼다(`is_link`는 없는 경로에서 예외).
2. `_stage_consult_refs`: 빠진 항목을 기존 `참고 파일 제외: "<ref>" (사유)` 줄로 프롬프트에 남긴다. 전부 빠져도 상담은 진행한다.
3. `tests/test_consult_refs_1041.py`(없는 파일·설명 문구·전부 제외·unsafe 거부·링크 거부), 폴더 ref 거부는 기존 `tests/test_ask.py` 그대로(범위 밖이라 손대지 않음). manual 한·영, Windows inventory.

## 테스트 결과
- Windows `test_consult_refs_1041`·`test_ask`·`test_facilities_staff`·`test_p2_followups_1013`·`test_runner_path_boundary` 76 passed·1 skipped(POSIX 링크), `windows_boundary`·inventory 188 passed.
- 미검증: 실제 ② 요청에서의 재현, WSL.
