## 단계
P2 — PI 시운전 전 실행 경로 결함 4건

## 한 일

| 항목 | main 재현 | 수정 |
|---|---|---|
| 7 · 직원 질문 hard stop | 평범한 format·표본 drop·결과 overwrite 질문이 destructive로 PI에게 감 | 모든 직원에 정책 목록을 적용하고 실제 삭제·파괴만 고유 PI gate로 유지 |
| 8 · 환경 설치 오판 | PowerShell 대입 6형태와 문서의 Bash PYTHONPATH 2형태가 거부됨 | installer 없는 대입·환경 접두사는 건너뛰고, 대입에 숨긴 installer는 계속 fail closed |
| 10 · PI 카드 대기 경고 | 열린 MCP 승인 호출에도 중단·재시도 경고가 표시됨 | PI 카드 대기는 debug로만 기록하고 종료·취소 경고를 생략 |
| 11 · 같은 폴더 재실행 산출 | retry·결정 재실행·review revise가 앞 `unreported_outputs`를 잃음 | 같은 workdir의 결과를 합치고 새 정식 outputs와 겹친 항목은 제외 |

`docs/manual.md`는 191,351 → 192,125 bytes(+774)로 네 동작만 보완했습니다.

## 테스트 결과
- [x] 추가 회귀의 main 상태: 23 failed, 6 passed
- [x] 수정 뒤 같은 회귀: 29 passed
- [x] 관련 pytest 5개 파일: 798 passed, 1 skipped
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5468 passed, 67 skipped
- [x] Node CJS 32개, `scripts/check_public.sh`, `git diff --check`: 통과
- [ ] GitHub Actions: 기록 commit push 뒤 확인
- [x] UI 변경 없음

## 바꾼 파일
- `labhq/models.py`, `labhq/orchestrator/cso.py`, `labhq/environment_install.py`, `labhq/adapters/base.py`
- `tests/test_ask.py`, `tests/test_bioinfo_pipeline_pr.py`, `tests/test_environment_install.py`, `tests/test_stall_watch.py`, `tests/test_cso.py`
- `docs/manual.md`

## 막힌 점
없음

## PI·Claude에게 물을 것
없음

## 확인
- [x] `scripts/check_public.sh` 통과
- [x] `docs/status/`와 branch patch note 추가
- [x] 체크포인트 아님

Refs #435

## 남은 지적
없음

🤖 Generated with Codex (gpt-5) for the labhq dev lead
