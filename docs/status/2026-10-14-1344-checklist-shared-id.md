## 결론

PR #592에서 공유 점검 ID의 분야별 답을 허용하고 bulk deconvolution topic을 추가했습니다. Refs #586.

## 바뀐 것

- `bulk_rna_seq.batch=step:S5`, `single_cell_rna_seq.batch=step:S4`처럼 공유 ID를 분야별 유효 답으로 모두 채우면 검증을 통과합니다.
- 계획 prompt에 공유 ID는 bare ID 하나 또는 모든 `topic.id`로 답한다는 규칙을 넣었습니다.
- `bulk_deconvolution` topic과 참조 프로필, 혼합물 검증, 희귀 세포 한계, 코호트 교차검증, 정규화 일치 점검을 추가했습니다.

## 확인

- 변경 전 회귀 2건 실패, 변경 뒤 2건 통과.
- 관련 pytest: 237 passed, 기존 SyntaxWarning 2건.
- vocab 문서·EDAM subset 검사와 공개 저장소 검사 통과.

## 남은 지적

- #586의 3번(리뷰·보고서 연결)은 지시 범위 밖이라 다루지 않았습니다.
- 전체 pytest, CI, 봇 리뷰, 병합은 후속 단계에 맡깁니다.
