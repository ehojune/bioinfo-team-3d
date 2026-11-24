# 연구 보고서에서 로컬 경로 가림

Closes #1492

- 정상 완료·재개·취소 보고서와 과학 부록의 로컬 경로를 가립니다. 결론과 근거 표식, TeX, 상대 링크를 보존합니다. 산출물 등록 경고는 독자가 읽을 문장으로 바꿉니다.
- [#1496 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683906097) → [#1497](https://github.com/ehojune/bioinfo-team-3d/pull/1497) → 이 PR로 교체합니다. #1497의 `5244ea49`를 한 커밋으로 이식했고, 현재 main과 합친 예상 트리와 일치했습니다.
- 콜론에 바로 붙은 POSIX·Windows 경로를 가리고 HTTPS·FTP URL과 공백이 든 비율의 두 항을 보존합니다. [경로 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1497#discussion_r4684428154) · [비율 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1497#discussion_r4684428169)
- 근거 표식이 없어도 경로 뒤 결론을 보존합니다. 위험비·p값처럼 비교식으로 시작하는 결론도 남깁니다. [결론 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4684694043) · [통계식 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4684788220)
- 슬래시 앞에만 공백이 있는 비율과 비교 기호 없는 AUC·위험비·신뢰구간을 보존합니다. [비율 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4684874217) · [통계값 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4684965937)
- 구분자가 연속된 Windows 경로를 가리고 콜론이 있는 통계값과 `cells /mL` 같은 단위를 남깁니다. [Windows 경로 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685088976) · [통계값 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685088984) · [단위 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685165593)
- 본문과 링크의 로컬 편집기 URI를 가리고 `mg /kg/day` 같은 복합 단위를 남깁니다. [로컬 URI P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685252913) · [복합 단위 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685252922)
- 기준 수가 붙은 기간 단위와 `counts /sample` 같은 표본당 계수를 보존합니다. [기준 수 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685340996) · [기간 단위 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685397864) · [공백 표기 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685464757) · [표본당 계수 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685518383)
- 관련 시험 419개 통과. log2FC·RPKM·TPM과 한 글자 통계 라벨의 수치를 보존합니다. 구두점이 연속된 경로 가림, 정상 완료·재개 벤치마크 끝 표식, 취소 보고서 저장·발행을 검증했습니다. [경로 구두점 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685870326) · [통계 약어 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4685936972) · [통계 라벨 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1499#discussion_r4686002764)
- 제품 순증 +90줄, [#1494](https://github.com/ehojune/bioinfo-team-3d/pull/1494) 포함 +300줄. `cso.py` 0, `server.py` +1, `daemon.py` 0. 세 파일 누계 +19줄, 잔여 81줄.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
