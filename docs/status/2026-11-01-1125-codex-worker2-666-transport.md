# #666 VM 없는 transport 초안

- 결론: 기본 off인 합성 요청 검증기를 추가했다. 일반 CLI와 연결하지 않으며 자식을 실행하지 않는다. Refs #666.
- 변경: 엄격한 JSON schema, trusted verifier 경계, run UUID·nonce·전체 메시지 인증, SQLite 일회 소비, env/stdio 허용 목록 재구성. fake verifier는 시험에만 있다.
- 시험: `python -m pytest -q tests/test_protected_transport.py tests/test_boundary_wiring_666.py tests/test_boundary_observer.py` — 108 passed. 최초 Windows 시험에서 SQLite 연결이 남는 결함을 찾아 명시적으로 닫도록 고쳤다. 리뷰 P2는 cwd 변경 뒤 다른 ledger로 replay가 통과하는 시험 실패를 확인하고 절대 경로 고정으로 수정했다.
- 범위: 같은 ledger 재개와 동시 요청 8건 중 1건 수락까지 합성 검증. OS peer 인증·ledger 보호·VM·실제 FD 상속·lifetime/cleanup은 UNVERIFIED.
- 보류: draft/hold 유지. 최종 head CI·실제 봇 리뷰와 W1 독립 검토 뒤 총괄이 병합 여부를 판단한다. #666은 OPEN.
- 계약: [transport 상세](../reference/sandbox-666-transport.md). 기존 untracked 설계·검증 기록은 보존한다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
