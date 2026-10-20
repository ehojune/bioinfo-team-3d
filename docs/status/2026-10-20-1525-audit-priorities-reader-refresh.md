## 단계
읽는 사람별 문서 정비(PI 요청) — 문서만 변경, PR #801

## 한 일
- main(#800·#803·#805·#806)을 합치고 HANDOFF 충돌을 현재 우선순위로 풀었습니다. #805의 manual 독자 분리·다이어그램·수치와 #806 hook 안내는 그대로입니다.
- README 한영판: PR 번호·감사 공지·PI 창구 행을 빼고 "v0.5 뒤 기능은 실제 연구 요청으로 재확인 전"이라는 안정적 한계를 적었습니다. 영문판은 한국어판 구성에 맞췄습니다.
- HANDOFF: 접수 중지, 병합 전 열린 PR 7개, 보류 항목, 접수 재개 뒤 순서만 남겼습니다. token 수치는 빼고 운영 기준·#760으로 보냈습니다.
- AGENTS·CLAUDE는 개발 절차만, 점검 주기·동시 투입·한도·접수 중지는 `docs/reference/lead-operations.md`로 옮겼습니다.
- manual: #796 문단을 PR #803 병합 사실로 고치고, 어휘 수를 코드 기준 125개(topic 45, EDAM 연결 79)로 맞췄습니다.
- 감사 우선순위 reference에 10-20 진행을 날짜 붙여 더했습니다(기존 표는 보존).

## 확인
- 코드 대조: `agents/core/*.yaml`(Claude Code 8·Codex 5, 리뷰어 Codex gpt-6-astra), `labhq/vocab/output_types.yaml`(125 key)·`edam_subset.yaml`(79 연결)·`topic_checklists.yaml`(45 분야·135 항목), `labhq/init_defaults.py`(`semantics.mode: ab`).
- `integrations.py --check`·`vocab_tables.py --check`·`notes_index.py --validate`·`patch_notes.py check`·`check_public.sh`·`git diff --check` 통과. 수정 문서 8개의 상대 링크·manual anchor 0건 깨짐.
- `tests/test_integrations.py`·`tests/test_project_link_spellings.py` 39 passed. CLI를 하위 프로세스로 띄우는 2개는 mock guard가 막았고 같은 검사(`integrations.py --check`)를 직접 통과했습니다.
- 글자 수(main → 후): 개발 문서(AGENTS·CLAUDE·HANDOFF) 8,368 → 6,845, README 한영판 포함 39,255 → 36,124.

## 남은 것
- 열린 PR #766·#790·#791·#793·#802·#807·#812가 병합되면 manual 해당 절과 HANDOFF 표를 그때 갱신해야 합니다.
- README 한영판은 badge·이중 언어 때문에 목표 15k보다 큽니다. badge 블록은 생성물이라 줄이지 않았습니다.
