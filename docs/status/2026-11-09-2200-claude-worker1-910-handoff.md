# 2026-11-09 claude-worker1 — #910 r9: 문헌 인계와 redirect 비차감

## 결론
10차에서 문헌 단계가 저장한 초록이 다음 단계로 넘어가지 않던 결함과, redirect가 조회 몫을 쓰던 문제를 최소 수정으로 고쳤습니다(PI 11-09 가지치기 지침, r8 설계는 버림).

## 바뀐 것
- `labhq/runner/step_inputs.py`: 받아들인 앞 단계의 `.tmp/literature/` 보통 파일을 이 단계 `.tmp/literature/`로 복사합니다. outputs로는 옮기지 않습니다(유료 본문 게시 방지). 링크·하위 폴더는 건너뛰고 기존 크기 상한을 씁니다.
- `labhq/runner/lookup_admission.py`: 3xx 응답은 조회 몫에서 되돌립니다. 일시 오류 상한과 호스트 차단기에는 넣지 않습니다.

## 실행한 것
- `pytest tests/test_lookup_admission_910.py tests/test_lookup_host_breaker_1279.py tests/test_live_source_limit_891.py tests/test_precedent_texts_951.py tests/test_external_lookup_flow_676.py`: 통과

## 미해결
- manual 09의 조회 상한 문단("redirect도 한 번씩 셉니다")과 annot 도구 거절 문구는 이 배정 범위 밖이라 고치지 않았습니다(manual 09는 #1331 W2 소유).
- 카드 첫 줄 경고는 #1336에서 따로 처리합니다.
