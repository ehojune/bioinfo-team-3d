## 단계
#1090(② 6차 B5) 1단계: 근거 표시가 저장된 선행 본문을 세는 재료. 설계: [#1090 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1090#issuecomment-6521798618). `cso.py` 호출 두 줄과 manual은 `cso.py`·manual을 쥔 W1 #792(PR #1087) 병합 뒤 #1088 rev3 다음에 넣는다. 이 브랜치는 PR을 열지 않았다.

## 한 일
1. `labhq/output_checks.py`: `stored_access(analysis_precedents)`는 `status == "ok"` 기록의 각 논문에서 영수증 `access_level`(LEVELS 안의 값)과 별칭(`paper_id` + `citations`의 PMID·PMCID·DOI), 부분 추출 여부를 `(별칭 집합, 수준, 부분)`으로 돌려준다. 쓸 수 없는 기록(없음·status ≠ ok·모르는 수준·영수증 없음·빈 id)은 비운다.
2. `conclusion_literature_line`·`literature_ledger_line`에 `stored=()` 인자. **인자가 비면 기존과 똑같다**(기존 시험 그대로 통과). 저장 별칭과 겹치는 결론 근거 집합만 병합하고(인용되지 않은 저장 논문은 더하지 않음), 접근 수준은 단계 기록과 저장 기록 중 좋은 쪽이다. 결론 줄은 출처를 남긴다: `…조회 기록 없음 0편(저장된 선행 검색 기록 4편 포함·CP1 전 기록, 그중 부분 추출 2편)`. 접근 원장 줄은 `확보에는 저장된 선행 검색 기록 N편(CP1 전 기록)이 들어 있습니다.`를 덧붙인다.
3. 시험 11건을 `tests/test_output_checks.py`에 더했다. ② 6차 재현: 현행 `본문 0·초록 0·서지만 0·조회 기록 없음 5편`, 저장 기록을 합치면 NEJM의 DOI·PMID 두 인용이 한 논문으로 묶여 `4편: 본문 3·초록 1·조회 기록 없음 0`, 인용 안 된 저장 논문 불산입, 단계 기록과 저장 기록 중 좋은 수준, 쓸 수 없는 기록 7종, 접근 원장 줄.

## 아직 하지 않은 것
`cso.py:5813~5819`의 두 호출에 `stored=output_checks.stored_access(req.get("analysis_precedents"))`를 넘기는 일과 manual 한·영(1200행 부근 세는 규칙 문단). 호출을 연결하기 전에는 실제 CP2 경고가 바뀌지 않는다.

## 테스트 결과
Windows `tests/test_output_checks.py`·`tests/test_research_report.py` 90 passed.

## 2단계(rev3) — 연결
`cso.py` `update_conclusion_depth`의 두 호출에 `stored=output_checks.stored_access(요청의 analysis_precedents)`를 넘기고 manual 한·영 세는 규칙 문단에 한 문장을 더했다. 연결 시험은 `tests/test_research_cp2.py`가 claim 범위 밖이라 PR에 넣지 못했다. 로컬에서는 통과(main의 cso.py로 되돌리면 실패)를 확인했고 패치는 `D:\.labhq\dev\tmp\claude-worker3\1090_wiring_test.patch`에 있다. 실행: `tests/test_output_checks.py` 46 passed.
