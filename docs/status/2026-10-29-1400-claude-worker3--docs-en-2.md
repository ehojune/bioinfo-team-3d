## 단계
#809 2차: research_protocol·pi-qa·vocabulary 영문판.

## 한 일
1. `docs/research_protocol.en.md`, `docs/pi-qa.en.md`, `docs/vocabulary.en.md`를 추가했습니다. 장·절 순서와 표 구조는 한국어판과 같고 코드·키·설정 이름은 원문 그대로입니다.
2. `vocabulary.md`는 `scripts/vocab_tables.py`가 만드는 생성 파일입니다. 영문판은 생성기 출력이 아니라 한국어 점검표 250칸을 손으로 옮긴 사본이며 파일 머리에 그렇게 적었습니다. `labhq/vocab/`이 바뀌면 손으로 갱신해야 합니다.
3. `README.en.md`의 Further reading 표와 29번째 줄, `docs/manual.en.md`의 해당 링크(research_protocol·pi-qa·vocabulary)를 영문판과 영문 anchor로 바꿨습니다.
4. `AGENTS.md`의 영문판 동시 갱신 안내에 세 문서를 더했습니다.

## 테스트 결과
- 새 문서의 내부 anchor 대조: 깨진 anchor 0. `test_vocab_tables.py`·`test_integrations.py`·`test_variant_effect_topic.py`·`test_ci_skip_policy.py` 226 passed. `check_public.sh` 통과.
- 미검증: 원어민 검수, 한국어판과의 문장 단위 전수 대조(vocabulary는 250칸 순서 대응과 표본 확인만 했음).

## 바꾼 파일
docs/research_protocol.en.md, docs/pi-qa.en.md, docs/vocabulary.en.md(신규), README.en.md, docs/manual.en.md, AGENTS.md, patch_notes/entries/claude-worker3--docs-en-2.yaml, 이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
