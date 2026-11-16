# #1413 — Codex 직원의 큰 결과 저장 안내

## 결과
- 공통 직원 지시에 큰 결과의 페이지별 파일 저장과 큰 셸 인자 금지를 추가했다.
- 검색 원응답 자동 저장이나 결과 검증 기능은 추가하지 않았다.

## 확인
- `python -m pytest tests/test_adapters_fake_cli.py -q`: 13 passed, 1 skipped.
- fake CLI로 지시 전달을 확인했다. 실제 직원의 준수는 미검증이다.
- 모델 호출, 재실행, 배포, HPC 실행은 하지 않았다.
