## 결론
Refs #621. PR #631. `alphagenome_variant`가 GRCh38 변이를 권장 scorer로 평가하고 한국어 요약과 원 수치 표를 돌려줍니다.

## 바뀐 것
- `score_variant` + `RECOMMENDED_VARIANT_SCORERS` + `tidy_scores`. 활성도 전용 scorer는 제외하며 절대 차이에는 증가·감소를 붙이지 않습니다.
- 기본·최대 1MB, 변이 10개, allele 1,000 bp. 조직/ontology 조회 후 필터, 답 상위 20행(최대 100), 파일·캐시 상위 1,000행.
- SDK·API v1·ALL_FOLDS·scorer 설정을 기록하고 캐시 키에 포함. 키는 기존 파일에서만 읽습니다. 비상업 API 조건·임상 근거 아님 표시.
- `variant_effect` 점검표·manual·선택 extra·doctor 안내 갱신. 기존 구간 도구 유지.

## 검증
- 새 mock 테스트 21개: main `38c0f37` worktree에서 21 failed → branch에서 통과.
- 관련 pytest 114 passed, 1 skipped(기존 numpy 선택 테스트). SDK·API는 mock, 실제 키·API 사용 없음.
- `scripts/check_public.sh`, `scripts/notes_index.py --validate`, `scripts/integrations.py --check` 통과.
- 전체 suite는 CI. `scripts/patch_notes.py check --base origin/main --head HEAD` 통과.

## 남은 지적
- 실제 API 실측은 지시 범위 밖. 서버 build ID는 SDK가 공개하지 않아 미확인으로 기록합니다.
- SDK가 일부 RPC 오류를 재시도하므로 10개 변이는 최대 50회 RPC 시도가 될 수 있습니다.
- 출처·SDK 계약: [참고 기록](https://github.com/ehojune/bioinfo-team-3d/blob/alphagenome-variant-score/docs/reference/alphagenome-variant.md).
