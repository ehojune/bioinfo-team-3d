## 결론

손상된 중첩 count가 있어도 semantics report는 정상 요청을 요약하고 제외한 행을 `broken_lines`에 센다.

## 바뀐 것

- `types`·`declarations`와 report가 더하는 count의 구조·0 이상 정수를 읽기 경계에서 검사한다. 손상 값을 0으로 바꾸지 않는다.
- 이전 선택 필드 부재, worker의 `declarations: null`, 소수 지연·비용을 보존한다. JSON·텍스트 CLI와 arm·판본·input-fit 합계를 검증했다.
- 매뉴얼 해당 절 갱신(117,845 → 118,032자). selector·worker·A/B 배정·breaker·저장 로그·활성 설정 변경 없음.
- 착수 기준 `e219c68`: triage 기준 이후 #547·#532와 생성 목차 반영.

## 실행한 것

- 합성 JSONL의 손상 count 두 종류: 새 CLI 회귀 4건이 main에서 실패하고 수정 후 통과.
- report pytest: 275 passed. Windows 전체 pytest: 5861 passed, 66 skipped, 692 warnings(8분 47초). skipped는 통과에 포함하지 않았다.
- 레인 전용 임시 폴더로 TEMP·TMP·TMPDIR·state·basetemp 격리. 공개 검사·diff 검사 통과.

## 미해결

- CI는 PR head 결과로 확인한다. 리뷰 요청·병합은 총괄에게 인계한다.
- 로컬 임시 폴더 삭제가 도구 정책으로 차단돼 정리를 인계한다.
- 합성 재현이며 운영 로그 손상을 확인한 결과는 아니다.

Refs #150

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
