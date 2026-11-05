# PR #647 — WSL 미설정 doctor 출력 정리

Refs #612.

## 결론

WSL을 설정하지 않은 Windows 설치의 `doctor`는 선택 기능이 꺼졌다는 한 줄만 표시합니다.

## 바뀐 것

- WSL 활성화 상태와 직접 실행한 `setup-wsl --check`는 개별 점검을 유지
- Claude 로그인 안내에 해당 WSL runner의 실제 `claude-staff` 경로 표시
- 새 회귀 2건으로 미설정·활성화 출력을 고정

## 확인

- 새 회귀: main 코드 2 failed → branch 2 passed
- `test_init_wizard.py`, `test_doctor.py`, WSL 관련 test: 187 passed
- `scripts/check_public.sh` 통과

## 남은 것

리뷰 요청과 병합은 총괄이 이어받습니다.
