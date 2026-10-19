## 결론

PR #765에서 현재 cgroup v2부터 보이는 mount 루트까지의 최소 memory.max를 usable_memory_bytes()에 반영했습니다. Fixes #726.

## 바뀐 것

- membership·mount root·octal escape를 해석하고 mount 경계 안에서 상위 한도를 읽습니다.
- unlimited·누락·잘못된 파일을 건너뛰며 기존 v1·고정 경로 fallback과 Windows 동작을 유지합니다.
- manual 161,473→161,615자. 상세 근거: `docs/reference/cgroup-memory-726.md`.

## 확인·남은 것

- 수정 전 fixture 11 failed·10 passed → 최종 관련 pytest 30 passed. Python 3.10 문법 통과.
- 실제 cgroup 변경·프로세스 종료·HPC·운영 인스턴스 검증은 미실행. 보이지 않는 ancestor는 추정하지 않습니다.
- 전체 suite는 CI에 맡깁니다. CI는 최종 head에서 한 번만 조회하며 봇·병합·배포는 driver가 맡습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
