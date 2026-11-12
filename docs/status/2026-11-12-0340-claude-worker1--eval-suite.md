# 2026-11-12 claude-worker1 — #1383 평가 묶음 설계와 오프라인 도구

## 결론
#1381 3절 평가 묶음의 설계(`docs/reference/eval-suite.md`)와 실행 없이 도는 도구·fixture를 넣었습니다. 모델 호출·조회·HPC·PI 인스턴스 실행은 없습니다. 실행은 PI가 반증 사례 정답·짝 비교 질문·예산을 정한 뒤 별도 issue로 합니다.

## 바뀐 것
- `scripts/citation_audit.py`: 기록된 DOI·PMID 응답과 묶음 안 원문으로 인용을 ok/minor/major로 매김(`literal_check` 재사용)
- `tests/fixtures/eval_suite/`: 인용 감사 10건, 리뷰 보정 clean·seeded 묶음(오류 10종, 정답 키), 반증 반영 사례 틀 6건(정답 칸 비움)
- `tests/test_citation_audit.py`(10건), `config/ci/windows-boundary-v1.json`에 등록
- `docs/reference/eval-suite.md`

## 실행한 것
- `pytest tests/test_citation_audit.py`: 10 passed
- Windows 목록 검증(`ci_selection.validate_profile`) 통과, `check_public.sh`·`patch_notes.py check` 통과

## 미해결
- PI 결정 셋: 반증 반영 정답, 짝 비교 고정 질문 10개, 예산 상한([#1383 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1383#issuecomment-6804191591))
- 인용 감사는 문장의 증감 방향을 보지 않음(#1374 몫)
