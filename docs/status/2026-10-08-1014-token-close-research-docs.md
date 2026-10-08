# 웹 token 오류와 연구 문서 어긋남 수정 (PI 점검 R23 R26)

- 결론: PR #504에서 잘못된 token은 브라우저에 1008로 전달되고, 두 웹 화면은 재시도를 멈춘 뒤 token 입력창을 보인다. 연구 문서와 artifact 거부 사유도 현재 동작과 맞췄다.
- 바뀐 것: `/ws/client` 인증 close 순서, 2.5D·3D 공통 close 판정, CSO 우선 blocking 답변, 연구 verifier·CP2·Codex auto 문구, 연구 lane 다섯 소제목.
- 실행한 것: 실제 uvicorn 1008 회귀, 관련 pytest 374 passed·1 skipped, Windows 전체 pytest 4790 passed·59 skipped, `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_web_3d.py`, `tests/web_state.cjs`, `tests/test_cso.py`, `tests/test_research_cp2.py`, `docs/manual.md`, `docs/research_protocol.md`.
