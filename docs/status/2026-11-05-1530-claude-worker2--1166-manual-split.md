## 단계
#1166 rev3: `docs/manual.md`·`manual.en.md`를 장별 파일로 나눈다. PI GO 2026-11-05.

## 한 일
- 한·영 각 24장을 `docs/manual/01-pi-text` … `24-interpreter-accounting`(`.md`·`.en.md`)으로 옮겼다. 문장은 고치지 않았고 상대 링크만 새 위치에 맞췄다. 장 사이 `#앵커` 링크는 장 파일 링크로 바꿨다.
- 옛 `manual.md`·`manual.en.md`는 머리말과 장 표에 원래 제목 65개를 그대로 둔 목차다. 각 제목 아래 링크가 장 파일로 간다. 그래서 외부의 `manual.md#앵커` 링크가 계속 유효하다(`disk-writes-and-slow-disks`는 `check_public.sh`가 키로 읽는 글자라 제목만 두고 링크에서 앵커를 뺐다).
- `scripts/integrations.py`: 인벤토리 마커가 든 파일을 09장으로 바꿨다. `scripts/patch_notes.py`·`scripts/ci_skip.py`: 장 파일도 매뉴얼 갱신으로 본다.
- 시험: 장을 이어 읽는 `tests/manual_text.py`를 두고 내용을 읽던 시험 6개와 경로 목록 시험 3개, `test_integrations.py`를 고쳤다.
- `AGENTS.md`: 같은 장 동시 수정 금지, 늦게 병합하는 쪽이 rebase.

## 확인
- 장 본문을 이어 붙여 링크 대상만 지우고 원문과 비교: 한·영 모두 일치.
- 장·목차의 상대 링크와 `#앵커`가 모두 실제 파일·제목을 가리킨다(스크립트 검사 0건).
- `scripts/integrations.py --check`, `scripts/check_public.sh` 통과. 관련 시험 1100여 건과 `-k "readme or doc or link or manual or anchor"` 1036건 통과.

## 하지 않은 것
- 앵커 링크를 쓰는 README·HANDOFF·pi-qa·references·reference/*는 고치지 않았다(목차 제목이 받는다).
- 전체 suite는 CI에 맡겼다.
