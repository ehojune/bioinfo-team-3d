# #1385 r7 — 문헌 전달 경고

- 201개 목록은 처음 200개만 처리하고 잘림을 알린다. 정확히 200개는 경고하지 않는다.
- 원격 전용 조상에서 빠진 문헌 사본을 context·agent.log에 알린다. 실제 산출 누락 거부는 유지한다.
- 수정 전 경계 시험: 2 passed, 1 failed(원격 경고 누락). 수정 후 `test_step_inputs_665.py`·`test_step_inputs_review_791.py`: 45 passed. 공개·diff·status 이름 검사 통과. 최종 CI·같은 head 리뷰는 진행 중이다.
- 합성 manifest·mock adapter로 검증한다. 실제 HPC·외부 조회·PI 인스턴스 실행은 없다.
- 원시 로그: 작업 레인의 임시 폴더에 `1385-r7-before.log`·`1385-r7-tests.log`로 보존한다. 실제 KRAS 확보율은 이번 범위 밖이며 #1380의 재실행 gate를 유지한다.
