## 단계
#1076 rev43: 간헐 실패 집계표와 demo driver 진단. 실패 원인을 가르는 정보를 남기는 단계이고 원인 수정이 아니다.

## 한 일
- `docs/reference/ci-1076-flake-tally.md`: 11-04 이후 11건 집계, 로컬 재현 시도, 원인 후보·범위.
- `tests/support/demo_driver.cjs`: deadline 행에 대기 중이던 `until`/`waitReal`, 응답 없는 CDP 호출, 마지막 응답 이후 시간, 요청 수를 추가. cleanup 진단에 경과·핸들별 대기 시간을 추가. timeout·단언·순서는 그대로.

## 확인
- demo driver를 쓰는 `test_office_in_node` 5종(office_browser·focus_layout·resizable_panels·visual_demo·visual_tooling) 5 passed, 약 36초(Windows 로컬).
- 일부러 deadline에 걸리게 한 임시 스크립트에서 새 필드가 찍히고 cleanup이 닫히는 것을 확인했다(저장소 밖 스크립트).

## 하지 않은 것
- 새 회귀 시험은 넣지 않았다. 시험 파일(`tests/web_visual_tooling.cjs`)이 claim 밖이다.
- 간헐 실패 원인은 아직 모른다. 다음 실패 로그로 판단한다.
