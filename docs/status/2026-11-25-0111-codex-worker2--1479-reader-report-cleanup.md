# 독자용 연구 보고서의 경로 가림과 결론 보존

Closes #1492

- [#1494](https://github.com/ehojune/bioinfo-team-3d/pull/1494) 다음에 머지하는 [#1496](https://github.com/ehojune/bioinfo-team-3d/pull/1496) 대체 PR입니다. main 위에 `0e502cab` 변경분을 적용하고 수정 전 트리가 원본과 일치함을 확인했습니다.
- 새 보고서와 과학 부록에서 로컬 경로를 가리고 산출물 등록 경고를 한글로 표시합니다. 결론과 근거 표식, 상대경로로 연결한 표·그림을 보존합니다.
- 확장자 없는 경로 뒤의 여러 단어 주어와 결론을 보존하고 Windows 현재 드라이브 절대경로를 가립니다. [결론 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683906097) · [경로 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683906113)
- 위 두 문제를 수정 전 회귀시험에서 재현했습니다. 수정 후 정상 완료·재개 벤치마크 끝 표식과 취소 보고서의 저장·발행 경로 가림을 포함해 대상 시험 285개가 통과했습니다. [기존 검증](https://github.com/ehojune/bioinfo-team-3d/pull/1496#issuecomment-7139232794)
- 제품 순증 +90줄, 구현 1커밋. #1494 포함 제품 순증 합계 +300줄. `cso.py` 0, `server.py` +1, `daemon.py` 0. 핵심 파일 누계 +19줄, 잔여 81줄.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
