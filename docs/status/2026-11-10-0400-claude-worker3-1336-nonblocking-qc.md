# 2026-11-10 claude-worker3 — #1336 r12·r13: 게시 검사 비차단화

## 결론
한국어 반드시-0 표현(번역투·줄임 전문어)이 남아도 결과 게시를 막지 않고 `korean_qc` 경고로 기록한다. 게시가 실패하면 GitHub 업로드 전의 로컬 준비 실패와 GitHub 응답 실패를 다른 문구로 안내한다.

## 바뀐 것
- `labhq/portfolio.py`: 보고서·README의 `check_document` 실패 시 `ValueError` 두 곳 제거. 결과는 `result_storage`가 이미 `saved["korean_qc"]`에 남김
- `labhq/result_storage.py`: `prepare_failed_text` 추가. 업로드 시작 전이고 GitHub 오류가 아닌 실패는 "이 PC에서 정리본을 만들지 못했다, 토큰 문제가 아니다"와 `--redraft` 안내
- `tests/test_result_storage_errors_1336.py`(새, Windows inventory 등록): 로컬 준비 실패 문구, GitHub 실패 문구 유지, 반드시-0 표현이 있어도 게시되고 WARN으로 기록됨
- `korean_qc.py`는 원래 WARN만 돌려줘서 고치지 않음
- manual 01(문체 검사는 경고만)·12(준비 실패 `--redraft`, 업로드 실패 `--republish`) 한·영 (봇 리뷰 P1)

## 실행한 것
- portfolio·result_storage·korean_qc 관련 시험 41개 파일: 1072 passed, 4 skipped

## 미해결
- 없음. 전체 suite는 CI
