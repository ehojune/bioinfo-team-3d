# runner state 소유 획득 경쟁

Refs #618. 서로 다른 설정의 runner owner 조회·기록을 state별 OS lock으로 묶었습니다. 실패한 획득자는 owner를 바꾸지 않으며 기록 실패도 시작을 막습니다. stale owner 복구는 유지합니다.

- 수정 전 경쟁 회귀 1 failed, 수정 후 `pytest tests/test_instance_guard.py -q` 37 passed. 별도 process의 lock 배제도 검사했습니다.
- 범위: 코드·임시 state 검사. 운영 인스턴스·HPC·재부팅 검사는 하지 않았습니다.
- 남은 조건: #618 P2 `broker_port` 사전 충돌 검사. CI·동일 head 봇 리뷰 뒤 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
