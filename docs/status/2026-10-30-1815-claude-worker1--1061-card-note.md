# #1061 후속: 식별자만 고정한 참조의 "내용 미확인" 표시

- 결론: W3의 PR #1062가 DOI·PMID 참조를 식별자 영수증(`unreceipted`)으로 고정해 이어 가기 재사용을 허용했다. 그 재사용을 PI가 알고 고르도록 두 곳에 표시를 더했다.
- 바꾼 것(`labhq/orchestrator/cso.py`): `unreceipted_references(contract, reused)`가 이전 라운드의 단계별 입력 영수증에서 재사용한 단계들의 `unreceipted` 식별자를 모은다. (1) 이어 가기 CP1 카드(재사용: … 줄 뒤)에 "재사용한 단계가 쓴 DOI·PMID 참조 N건은 식별자만 고정했고 내용은 확인하지 않았습니다." (2) CP2 보고의 이어 가기 줄에 "N DOI/PMID reference(s) of the reused steps are pinned by identifier only; their content was not checked (…)". 표시할 참조가 없으면 카드 문구는 전과 같다(기존 시험의 접두 단정 유지).
- 시험: `tests/test_continuation_797.py` 3건(재사용한 단계 증명만 모음, 카드·보고에 한 줄, 참조가 없으면 변화 없음). 관련 시험 310여 건 통과.
- 미검증: 실제 DOI 참조 요청의 CP2 revise 이어 가기에서 카드 문구가 PI에게 어떻게 보이는지(웹 화면). 카드 요약은 길이 상한이 있어 참조가 많은 요청에서 뒷부분이 잘릴 수 있다.
