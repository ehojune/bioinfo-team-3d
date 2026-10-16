## 결론

직원이 논문 본문을 읽는 기록된 경로를 만들었습니다. `labhq_annot.fulltext`가 Europe PMC 오픈 액세스 본문을 받아 `outputs/literature/`에 쓰고, 문헌·생물·QC·리뷰 직원 지시가 그 경로와 `access_level` 칸을 씁니다. Refs #697.

## 바뀐 것

- `labhq/tools/annot_literature.py`: PMID·PMCID·DOI를 Europe PMC에서 찾고, 오픈 액세스 사본은 JATS를 Markdown(절·표·그림 설명·참고문헌)으로 바꿔 씁니다. 사본이 없으면 초록을 씁니다. 결과에 `access_level`(full_text·abstract·metadata)과 라이선스가 붙고, 조회 기록은 다른 annot 도구처럼 `outputs/annotation_queries.jsonl`에 남습니다. 본문은 기록 파일에 다시 넣지 않고 경로와 sha256만 둡니다.
- lit_scout·biologist: 결론에 쓰는 논문은 `fulltext`로 본문을 읽고, CAPTCHA 화면은 "열지 못함"으로 적고 다시 열지 않으며, 유료 논문은 우회하지 않고, 표마다 `access_level` 칸을 둡니다.
- qc_reviewer: 초록에서 뽑은 효과 크기·수·구간과 `access_level` 칸 없는 표를 WARN, 한계 절에 수준별 논문 수. sci_reviewer: 초록만 근거인 효과 크기를 약한 근거로 봅니다.

## 확인

- 새 test 10개와 직원 지시 test, annot·registry·읽기 전용 profile test 132 passed, 1 skipped.
- 실제 Europe PMC 응답(PMC13248341)으로 변환을 확인했습니다: 41KB, 표 2개, 참고문헌 46개.
- 고정 prompt hash는 CSO prompt를 건드리지 않아 바뀌지 않습니다.

## 남은 것

- 보고서 경고에 결정적 검사(초록 수준 근거 수)를 붙이는 일은 #679·#686 레인이 `cso.py`를 고친 뒤 후속으로 합니다.
- 출판사가 호스팅하는 CC 논문은 `publisher_copy` 링크 한 번 시도까지만 합니다. Unpaywall 같은 다른 오픈 액세스 경로는 후속 후보입니다.
