## 단계
#1088(② 6차 B1) 1단계: 요청 단위 조회 사용량을 사실값으로 내는 재료. 설계: [#1088 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1088#issuecomment-6521726606). prompt 연결과 manual은 `cso.py`·manual을 쥔 W1 #792(PR #1087) 병합 뒤 rev3에서 한다. 1단계는 별도 커밋이고 이 PR이 2단계까지 포함한다.

## 한 일
1. `labhq/runner/lookup_admission.py`: `LookupAdmission.usage(request_id)`는 읽기 전용으로 `used`·`limit`·`remaining`(음수 없음)·담당별·도구별 건수를 돌려준다. 요청 id가 비었거나 문자열이 아니면 0이 아니라 `None`.
2. `usage_note(admission, request_id)`: prompt 한 문단. 값이 있으면 "이 요청 사용 N/M, 남음 K(재시도·redirect 포함, 담당별), 이 PC runner의 계수이며 요청문에 적힌 횟수와 별개"를 사실로 적는다. 상한이 꺼졌거나(`admission is None`)·요청 id가 없거나·파일을 읽지 못하면 `UNKNOWN`과 이유를 적고 "0이라 가정하지 말라"고 한다.
3. 시험 7건을 `tests/test_live_source_limit_891.py`에 더했다(51/60 사용·담당별, 요청 간 분리, 신원 없음 `None`, 남은 값 0에서 멈춤, 문장 내용, unknown 세 경우, 읽기 오류 unknown, 설정 공장).

## 2단계(rev3)에서 한 것
`cso.py`의 `_lookup_usage_line(rid)`가 CSO 계획(연구·일반 두 lane)·재계획·선행 연구 검색·모든 단계(`run_one`) prompt 끝에 `usage_note_for`의 문단을 붙인다. 상한이 없으면 아무것도 붙이지 않고, 상한이 있는데 계수를 열지·읽지 못하면 UNKNOWN을 붙인다. manual 한·영(조회 상한 항목)에 적었다. 요청 사이 자동 이월은 하지 않는다(총괄 결정).

## 테스트 결과
Windows `tests/test_live_source_limit_891.py` 20 passed(1단계 7건 + prompt 연결 5건), `test_research_cp2`·`test_output_types_research`·`test_research_report`·`test_cso`·`test_external_wait` 포함 406 passed.
- 미검증: 재계획 prompt는 같은 helper를 쓰지만 통합 시험은 없다(재계획을 일으키는 장면이 따로 필요). 다른 PC의 runner는 계수가 다르다(문구에 "이 PC runner"로 한정).
