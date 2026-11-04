## 단계
#809 1차: 영문 매뉴얼.

## 한 일
1. `docs/manual.en.md`를 추가했습니다. `docs/manual.md`와 장·절 순서가 같고(1586줄/1589줄), 코드·명령·설정 키·경로는 원문 그대로입니다. 다른 문서(pi-qa, research_protocol, reference/ 등)로 가는 링크는 그대로 두고 한국어 문서임을 표시했습니다.
2. `README.en.md`의 manual 링크(Further reading 표, 본문 6곳)를 영문판과 영문 anchor로 바꾸고 29번째 줄 문구를 고쳤습니다. 맨 위 배지는 `scripts/integrations.py`가 만들어 `docs/manual.md#연결된-도구` 등을 가리키므로 건드리지 않았습니다.
3. `AGENTS.md`에 "한국어 manual을 고치면 영문판도 같은 PR에서 고친다"를 적었습니다.
4. 영문판의 "연결된 도구" 표는 생성 표(`<!-- integrations -->` 표식)가 아니라 손으로 옮긴 사본입니다. 생성기는 한국어 manual만 갱신하므로 도구가 바뀌면 영문 표도 따로 고쳐야 합니다. 영문판 본문에 그 사실을 적었습니다.

## 테스트 결과
- 영문판 내부 anchor 링크를 제목 slug와 대조: 깨진 anchor 0.
- `python scripts/patch_notes.py check`, `scripts/check_public.sh`는 PR 전에 실행합니다(결과는 PR 본문).
- 미검증: 번역 품질의 원어민 검수, 한국어 manual과 문장 단위 대조(구조·핵심 수치는 대조했으나 전수 대조는 하지 않음).

## 바꾼 파일
docs/manual.en.md(신규), README.en.md, AGENTS.md, patch_notes/entries/claude-worker3--manual-en.yaml, 이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
