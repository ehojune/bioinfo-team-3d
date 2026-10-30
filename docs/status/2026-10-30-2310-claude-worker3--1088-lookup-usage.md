## 단계
#1088(② 6차 B1) 1단계: 요청 단위 조회 사용량을 사실값으로 내는 재료. 설계: [#1088 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1088#issuecomment-6521726606). prompt 연결과 manual은 `cso.py`·manual을 쥔 W1 #792(PR #1087) 병합 뒤 rev3에서 한다. 이 브랜치는 PR을 열지 않았다.

## 한 일
1. `labhq/runner/lookup_admission.py`: `LookupAdmission.usage(request_id)`는 읽기 전용으로 `used`·`limit`·`remaining`(음수 없음)·담당별·도구별 건수를 돌려준다. 요청 id가 비었거나 문자열이 아니면 0이 아니라 `None`.
2. `usage_note(admission, request_id)`: prompt 한 문단. 값이 있으면 "이 요청 사용 N/M, 남음 K(재시도·redirect 포함, 담당별), 이 PC runner의 계수이며 요청문에 적힌 횟수와 별개"를 사실로 적는다. 상한이 꺼졌거나(`admission is None`)·요청 id가 없거나·파일을 읽지 못하면 `UNKNOWN`과 이유를 적고 "0이라 가정하지 말라"고 한다.
3. 시험 7건을 `tests/test_live_source_limit_891.py`에 더했다(51/60 사용·담당별, 요청 간 분리, 신원 없음 `None`, 남은 값 0에서 멈춤, 문장 내용, unknown 세 경우, 읽기 오류 unknown, 설정 공장).

## 아직 하지 않은 것(rev3)
`cso.py`의 prompt 4곳(CSO 계획·재계획·단계·선행 본문 검색)에 `usage_note`를 넣는 일과 manual 한·영. 요청 사이 자동 이월은 하지 않는다(총괄 결정).

## 테스트 결과
Windows `tests/test_live_source_limit_891.py` 15 passed.
