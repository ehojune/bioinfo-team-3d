## 단계
네 데몬 readiness의 부분 해결. Refs #877.

## 한 일
- 선택형 `contained_verified`에 state 밖 운영자 계약 pin과 실행 중 verifier 바이트 검증을 더했습니다.
- owner PID·생성 시각·이미지, fresh pipe challenge, 현재 네 역할의 정확한 Job 포함과 독립 마감을 묶습니다.
- 기존 detached 판정과 인증·연결 실패는 유지합니다. 독립 정적 검토에서 운영자 trust 경계 안의 P1은 해소됐습니다.

## 테스트 결과
- fake/static 회귀: 180 passed. 실제 process 생성·종료와 연구 요청은 없었습니다.
- 별도 후보 owner RPC의 fake 검사: 4 passed. 공개 제품의 runtime 검증을 대신하지 않습니다.
- Python 3.10 AST, public, diff 검사 PASS. CI는 PR에서 확인합니다.
- UI 변경 없음.

## 바꾼 파일
- `labhq/runtime_readiness.py`, `tests/test_contained_readiness.py`
- `docs/manual.md`, `docs/reference/contained-runtime.md`, 변경 기록

## 막힌 점
현재 B는 NO-GO입니다. 새 verifier 배포·owner RPC 전환·실제 doctor 검사는 남았습니다. 직원 adapter CLI·손자 Job 포함과 owner crash·실제 90분 종료 receipt도 별도 게이트입니다. WMI·service 우회는 미증명입니다. 네 데몬 판정으로 요청 전체 종료나 #877 완료를 주장하지 않습니다.

## PI·Claude에게 물을 것
추가 PI 승인을 요청하지 않습니다. 실제 전환과 GO 판정은 총괄의 증거 검토 뒤 진행합니다.

## 확인
- [x] `scripts/check_public.sh` 통과
- [x] 같은 보고를 `docs/status/`에 기록
- [x] 실제 B·연구 실행 없음

🤖 Co-Authored-By: Codex (gpt-6-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
