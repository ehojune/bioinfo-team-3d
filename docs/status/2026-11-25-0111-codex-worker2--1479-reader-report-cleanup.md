# 연구 보고서에서 로컬 경로 가림

Closes #1492

- 정상 완료·재개·취소 보고서와 과학 부록의 로컬 경로를 가립니다. 결론과 근거 표식, TeX, 상대 링크를 보존합니다. 산출물 등록 경고는 독자가 읽을 문장으로 바꿉니다.
- [#1496 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683906097) → [#1497](https://github.com/ehojune/bioinfo-team-3d/pull/1497) → 이 PR로 교체합니다. #1497의 `5244ea49`를 한 커밋으로 이식했고, 현재 main과 합친 예상 트리와 일치했습니다.
- 콜론에 바로 붙은 POSIX·Windows 경로를 가리고 HTTPS·FTP URL과 공백이 든 비율의 두 항을 보존합니다. [경로 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1497#discussion_r4684428154) · [비율 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1497#discussion_r4684428169)
- 관련 시험 346개 통과. 정상 완료·재개 시 벤치마크 끝 표식, 취소 시 보고서 저장·발행을 확인했습니다.
- 제품 순증 +90줄, [#1494](https://github.com/ehojune/bioinfo-team-3d/pull/1494) 포함 +300줄. `cso.py` 0, `server.py` +1, `daemon.py` 0. 세 파일 누계 +19줄, 잔여 81줄.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
