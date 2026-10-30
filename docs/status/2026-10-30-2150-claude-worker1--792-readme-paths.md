# #792 rev5 공개본 README와 경로 치환

- 결론: 공개본 README의 "한계"는 보고서가 밝힌 실제 한계(최대 3문장)를 싣고, 한 줄짜리 대체문은 없앴다. 그림 설명, 파일 목록, 데이터 유무별 실행 방법, seed 절을 추가했다.
- 경로: `str(p).replace("\\\\", "/")` 같은 코드의 `"\\"`를 UNC 경로로 오인해 `<local-path>`로 바꾸던 정규식을 고쳤다(호스트와 구분자가 있어야 UNC). 실행 기록의 "남은 절대경로" 목록은 개수로 줄였다.
- 끝 문장: 저장소 README는 `report/report.md` 링크가 앞에 있어 "이어지는 내용은 …" 문장을 flat 판본에만 붙인다.
- P2: 작업공간 경로 감지(`inputs`·`outputs`·`manifest.json`)를 대소문자 무시로 바꿨다.
- 바꾼 것: `labhq/portfolio.py`, `labhq/result_storage.py`, `agents/core/editor.yaml`, 시험 2개, README 한·영 각 한 줄.
- 한계: 한계 문장 선택은 키워드 휴리스틱이라 보고서 표현에 따라 빠질 수 있다. 목록 항목의 앞 "- "가 남는 경우가 있다. `route: solo` prompt 연결은 W3의 #1088 뒤로 미뤘다.
- 미검증: 실제 새 실행으로 만든 README, Windows CI.
