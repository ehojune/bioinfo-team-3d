## #670 논문 스킬: 초록뿐인 논문 생략·메타분석 스킬의 추출 단계 부착 (base f65dcf5c)

- 대조: 본문 receipt 없이 스킬을 만들지 않는 것은 #830으로 이미 있었다. 이번에 사유를 밝히고(초록만 확인/접근 확인 못 함 + 계획 참고로만) 메타분석 부착을 더했다.
- 바꾼 것: `labhq/recruit/paper_skills.py`의 `no_fulltext_reason`·`cited_access`·`extraction_steps`. 메타분석 제목이면 계획의 `lit_scout` 단계를 `step_ids`·`agent_ids`에 더하고 `extraction_step_ids`를 남긴다. manual 논문 스킬 절, Windows 경계 목록에 새 test 등록.
- 검사: 새 test 5건(수정 전 수집 실패, 수정 후 통과), 논문 스킬 관련 187 passed.
- 한계: 메타분석 판정은 제목 문구(영·한)만. 초록만 확인 판정은 선행 연구 인용의 `access_level=` 문구에 의존. #906(PR #909)이 같은 파일을 바꾸므로 병합 순서에 따라 rebase한다. 실제 모델 계획에서의 동작은 확인하지 않았다.
