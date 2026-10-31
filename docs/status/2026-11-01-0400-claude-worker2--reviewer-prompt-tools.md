# #1224: 읽기 전용 실행의 프롬프트가 실제 도구와 맞는다

- 결론: 읽기 전용 프로필(`read_only_profile`)이 프롬프트 끝에 `## Tools in this read-only run`과 이번 실행에서 쓸 수 있는 도구 목록을 한 번 붙이고, 그 밖에 프롬프트가 언급한 도구(PubMed·bioRxiv, 다른 `labhq_annot` 도구, 셸)는 쓸 수 없다고 알린다. 직원마다 프롬프트를 고치지 않고 한 곳에서 닫는다(사전 조사: #1224 댓글, `lit_scout`·`biologist`·`analyst` 등도 같은 어긋남이 있었다).
- 바뀐 것: `labhq/adapters/read_only.py`(`available_tools`·`with_tool_notice`, profile의 profile에는 한 번만 붙음), `agents/core/sci_reviewer.yaml`(PubMed·bioRxiv 도구가 없다는 것과 "확인 못 한 인용은 not verified"로 표시하라는 문장), `tests/test_read_only_profile.py`(모든 core 직원의 노티스 도구 목록이 프로필의 실제 도구와 같다, 멱등, claude는 웹·annot 없음, codex는 웹만), `tests/test_agent_prompts.py`(sci_reviewer 프롬프트가 프로필에 없는 도구를 말하지 않는다).
- 리뷰 반영(rev3): 직원 파일이 `disallowed_tools`로 통째로 막은 도구(`Write` 등)는 "Available now"에서 빼고, 일부만 막은 도구(`Read(./data/**)`)는 남긴다. manual 한·영에 읽기 전용 실행의 도구 안내 단락을 적었다.
- 실행한 것: `test_read_only_profile`·`test_agent_prompts`·`test_annot_literature` 122 passed. read_only·followup·consult를 건드리는 시험 3009 passed, 49 skipped(수정 전 main 기준 동일 파일군).
- 미해결/미검증: 실제 모델이 노티스를 따르는지는 실행하지 않았다. 노티스는 영어 고정 문구라 한국어 프롬프트 직원도 같은 문장을 받는다.
