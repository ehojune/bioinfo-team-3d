# 2026-10-07 · PR #462

## 결론

43 topic 모두 근거 논문 30편 이상을 채웠습니다. 총 1255편에서 1286편으로 늘었습니다.

## 바뀐 것

| topic | 이전 | 현재 |
|---|---:|---:|
| viral_genomics | 20 | 30 |
| metaproteomics | 23 | 30 |
| microarray_expression | 24 | 30 |
| amplicon_sequencing | 25 | 30 |
| metagenome_assembly | 28 | 30 |
| metatranscriptomics | 29 | 30 |

- 직접 관련된 방법·프로토콜·벤치마크 논문 31편을 추가했습니다. 기존 1255행과 8개 열 형식은 유지했습니다.
- README·manual·topic 요약표를 갱신하고, v0.9 로드맵의 논문 목표와 HANDOFF 작업 큐를 완료로 갱신했습니다.
- 추가 PMID의 검증표는 `docs/reference/topic_papers_added_2026-10-07.verified.tsv`에 남겼습니다.

## 실행한 것

- 지정된 `verify_sources.py`: 31행 한 묶음, 31/31 ok. 새 저자·연도·저널·제목은 NCBI esummary metadata에서 가져왔습니다.
- 독립 QA: 원본 필드·상대 순서 보존, 신규 서지 metadata와 문서 수치 일치.
- 수치 대조: PMID 중복 0, 최소 30·최대 82편, 여러 topic에 걸친 논문 429편.
- `scripts/vocab_tables.py --check`, `scripts/check_public.sh` 통과.
- Windows 전체 pytest: 4453 passed, 56 skipped. 단일 basetemp 종료 삭제 확인. 첫 실행의 ledger 기록 실패 1건은 단독 재실행과 전체 재실행에서 통과했습니다.

## 남은 지적

- P2: 영문 README의 옛 수치는 후속 #463으로 넘깁니다.

🤖 Generated with Codex for the labhq dev lead
