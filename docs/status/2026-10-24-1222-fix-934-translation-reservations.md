Refs #934

통역의 task별 예약을 dispatch 전에 보존하고 request bound와 content-free projection에 포함합니다. 동일 task의 연구 hold와 한 번만 합산하며 orphan 예약은 누락 task로 표시합니다. 기존 aggregate에서 task별 예약을 복원하지 않습니다.

- 관련 회귀: `pytest tests/test_pi_language.py tests/test_task_attempt_projection.py tests/test_resume_cp1_654.py -q` → 134 PASS.
- admission 회귀: `pytest tests/test_cso.py -k 'budget or reserv' -q` → 24 PASS.
- `scripts/check_public.sh` PASS. CI·봇 review는 PR 최종 head에서 확인합니다.
- 설치판 반영·통합 재고정은 별도 조정 대상입니다. 실제 최초 취소 분기는 UNVERIFIED입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
