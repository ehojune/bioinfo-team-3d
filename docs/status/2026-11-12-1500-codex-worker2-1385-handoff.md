# #1385 r7 — 문헌 전달 경고

- 201개 목록은 처음 200개만 처리하고 잘림을 알린다. 정확히 200개는 경고하지 않는다.
- 원격 전용 조상에서 빠진 문헌 사본이나 목록 검사 실패를 context·agent.log에 알린다. 문헌 없음·빈 폴더·inbox만 있으면 경고하지 않는다. 실제 산출 누락 거부는 유지한다.
- 수정 전 경계 시험: 2 passed, 1 failed(원격 경고 누락). 리뷰 P2 오탐 재현: 3 failed, 1 passed. 수정 후 `test_step_inputs_665.py`·`test_step_inputs_review_791.py`: 48 passed. 공개·diff·status 이름 검사 통과. 최종 CI·같은 head 리뷰까지 PR #1410은 hold다.
- 합성 manifest·mock adapter로 검증한다. 실제 HPC·외부 조회·PI 인스턴스 실행은 없다.
- 원시 로그: 작업 레인의 임시 폴더에 `1385-r7-before.log`·`1385-r7-tests.log`·`1410-p2-before.log`·`1410-p2-after.log`로 보존한다. 실제 KRAS 확보율은 이번 범위 밖이며 #1380의 재실행 gate를 유지한다.
