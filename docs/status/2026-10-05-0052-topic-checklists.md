# topic 점검표와 선행 연구 기준 (#373 #375)

## 단계
P1 — 계획·리뷰·보고서에 topic별 점검표와 선행 연구의 필수·권장 분석을 연결했습니다.

## 한 일
- 공개 데이터 재분석 브리핑은 원 연구 본문에서 Methods 선택을 5줄 안에 요약합니다.
- 문헌 담당은 브리핑과 병렬로 최근 논문 2–4편을 조사하며, 결과는 요청 상태에 저장합니다.
- bulk RNA-seq·microarray·single-cell 점검표를 PLAN 답, 리뷰, 한계와 단독 처리에 전달합니다.
- 일반 lane은 누락 답을 한 번 고친 뒤 경고하고, 연구 lane은 PLAN 검증 오류로 멈춥니다.

## 테스트 결과
- [x] Windows `pytest -q`: 3830 passed, 54 skipped
- [x] Node CJS 22개와 `scripts/check_public.sh` 통과
- [x] manual: 72,630자 → 73,642자

## 기존 테스트 변경
- 연구 PLAN fixture에 새 점검표 답을 넣고, 추가 schema·prompt hash와 재시작 담당 기대를 갱신했습니다.
- 문헌 담당 부재 경고와 Windows 명령 길이에 따라 달라지는 TASK.md 경로 기대를 새 계약에 맞췄습니다.

## 막힌 점
없음.
