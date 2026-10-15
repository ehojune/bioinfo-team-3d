## 결론

Fixes #644. 실행 중 task가 있으면 직원의 담당 runner를 유지하고 모두 끝난 뒤 새 task부터 준비된 WSL에 배정합니다. WSL 시작만 실패한 `up`은 경고 한 줄과 exit 0으로 끝납니다.

## 바뀐 것

- `Hub.effective_roster()`를 routing·등록 hosted_agents·generation 판정·불확실 전송·dispatch 재연결·카드 복구·채용 조회의 단일 출처로 사용합니다.
- 미완료 task의 저장된 runner ID로 담당을 고정합니다. 전송 불확실 timeout도 담당을 풀지 않으며 새 task를 다른 runner로 보내지 않습니다.
- grep(`git grep -n -e runner_agents -e last_runner_rosters -- labhq`) 결과는 server.py 8곳뿐입니다: 초기화 262·306, 재시작 offline 알림용 ID 목록 314, 계산 함수 2080·2082·2107, 입력 저장 2136·2138. 계산 함수 밖에 원본 roster를 읽어 담당을 정하는 경로는 없습니다.

## 확인

- origin/main `5c0cfc2`: 새 회귀 4 failed. 수정본: 같은 4 passed(native 실행 중 대체 runner 연결·재연결, 새 task도 native 유지, 모든 task 완료 후 WSL, 원본 접근 AST 검사, WSL 오류 경고/exit 0).
- 관련 pytest 278 passed/1 skipped. 채용 경로 변경 후 추가 pytest 103 passed(새 회귀 4건 포함).
- 기존 불확실 전송 test는 reconnect timeout만 0.01초로 줄이고 담당 유지·미완료 단언을 추가했습니다. 채용 test의 mock은 유효 roster API로 바꿨습니다.
- `scripts/check_public.sh` 통과.
- clone 전용 venv·격리 state·mock socket/WSL 사용. 전체 pytest는 CI에 맡깁니다.

## 남은 지적

- 없음. CI 결과는 push 후 확인합니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
