## 2026-10-06 · README — 첫 문장·bioinfo-agent 소개와 영문 README

- 결론: PI 요청(10-06). README 첫 문장에 "멀티 에이전트 오케스트레이션 플랫폼"을 합치고, PI가 만든 bioinfo-agent를 링크와 한 줄 설명으로 소개하고, 영문 README를 더한다.
- 바뀐 것: `README.md` 첫 문장·언어 전환 링크·bioinfo-agent 줄. `README.en.md`(전체 번역, 버튼·탭은 실제 한국어 UI 이름에 영문 병기, 웹 UI가 한국어뿐이라고 명시)와 영문 그림 6개(`docs/media/en/`, `docs/media/why/en/`). `scripts/integrations.py`가 영문 배지 블록도 같은 블록에서 만들고 검사하며, 번역표에 없는 한국어 구가 남으면 실패한다(PR #428 리뷰). `scripts/ci_skip.py`는 README.en.md만 바뀐 커밋도 test_integrations만 돌린다.
- 실행한 것: Edge headless로 영문 그림 6개 렌더 확인(넘침 없음), `scripts/integrations.py --check`, `tests/test_integrations.py`·`tests/test_ci_skip.py` 40 passed, `scripts/check_public.sh`.
- 미해결: README의 "단어장·점검표를 N편 논문·교과서·웹 자료로 정립" 문장은 #420 조사 뒤 실제 수로 넣는다.
- 근거: `README.en.md`, `scripts/integrations.py`.
