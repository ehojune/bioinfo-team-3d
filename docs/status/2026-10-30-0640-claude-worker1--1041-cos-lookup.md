# #1041 항목 1: 비서실장 문헌 조회 도구

- 결론: 기본 비서실장에 `labhq_annot`의 문헌 7종만 붙였다. #910 1차 실패는 프롬프트가 원문·DOI 확인을 요구하는데 roster에 조회 도구가 없어 직원이 임시 HTTP 스크립트로 우회하다 막힌 것이다.
- 바꾼 것: `agents/core/chief_of_staff.yaml` — `builtin_mcp: [approval, annot]`, 변이·영역 도구 9개(`vep`·`gnomad`·`clinvar`·`chipatlas`·`chipatlas_targets`·`encode`·`gtex`·`alphagenome`·`alphagenome_variant`)는 `disallowed_tools`로 차단, 프롬프트는 `labhq_annot fulltext`·`crossref_doi`를 쓰라고 명시. 시험: `tests/test_annot_literature.py`(구성 고정), `tests/test_annot.py`(annot 직원 집합).
- 실행한 것: 위 두 파일의 관련 시험 통과.
- 미검증: 실제 Claude Code 세션에서 `mcp__labhq_annot__<tool>` 형식의 `disallowedTools`가 의도대로 변이 도구만 막는지는 W2의 #910 2차에서 확인한다. 이 PR 병합 전에는 W2가 같은 내용을 설정에 직접 넣어 둔다.
- 범위 밖: 항목 2(상담 ref 검사, `runner/daemon.py`).
