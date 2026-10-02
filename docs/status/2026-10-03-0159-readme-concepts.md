## 2026-10-03 · readme-concepts — README 핵심 개념·버전 로드맵

- 결론: 새 독자는 README §0-2 표 하나로 본문 용어를 따라가고, 로드맵과 작업 큐는 PI 버전 순서(v0.25 → v1.25)를 따른다.
- 바뀐 것: README에 핵심 개념 25행 표(§0-2)와 버전 표(§11)를 넣었다. HANDOFF 작업 큐에서 병합된 PR을 빼고 버전 순으로 다시 묶었으며 #273·#275를 PI 결정으로 옮겼다.
- 실행한 것: `scripts/check_public.sh`, `patch_notes.py check`, `notes_index.py --validate`, `tests/test_integrations.py`.
- 미해결: 개인 경로 차단(PR #324)이 병합되면 핵심 개념 표에 `policy.private_paths` 행을 더한다. P3 HPC·거버넌스·bench 시점은 PI 결정 대기.
- 근거: `README.md`, `HANDOFF.md`, `docs/research_protocol.md`.
