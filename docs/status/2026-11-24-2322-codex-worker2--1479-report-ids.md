## #1479 PR 5 · 보고서 식별자 검사

Closes #1492

- `labhq verify`는 기존 실시간 출처 확인 설정에서 본문·과학 부록·원장의 유전자 기호, GO ID, DOI를 검사합니다. 추가 모델 호출은 없습니다. 유전자는 `HGNC symbol: TP53`처럼 종을 구분해 표시합니다. [HGNC](https://www.genenames.org/help/rest/) · [QuickGO](https://www.ebi.ac.uk/QuickGO/api/) · [DOI Handbook](https://www.doi.org/doi-handbook/html/)
- 등록 기관이 확인한 미등록과 통신 오류·한도 초과를 구분합니다. 결과는 verify JSON과 감사 번들의 `claims.json`에 남습니다. 기존 원장·해시·본문 대조를 유지합니다.
- 보고서는 로컬 경로를 가리고 산출물 등록 경고를 한글로 표시합니다. 근거 표시와 표·그림 링크는 유지합니다.
- 리뷰에서 지적한 원장 DOI URL을 조회하도록 고쳤습니다. 경로 바로 뒤에 붙은 근거 표식도 유지합니다. [리뷰 P2](https://github.com/ehojune/bioinfo-team-3d/pull/1493#discussion_r4681495822)
- 공백이 든 PC 경로는 통째로 가립니다. 뒤 문장과 근거 표식, 재현 묶음의 상대경로 치환을 확인했습니다. [리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1493#discussion_r4681606390)
- 새 보고서는 절대경로 링크에서 표시 문구만 남깁니다. 아포스트로피·쉼표·괄호가 든 경로도 가립니다. [링크 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1493#discussion_r4681795888) · [경로 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1493#discussion_r4681796027)

## 확인

- 보고서·verify·PDF·재현 묶음·게시 관련 시험 674 passed, 4 skipped. 공개 ID를 이용한 실조회에서 등록 3건과 미등록 2건을 구분했습니다. 검증용 클라이언트는 Windows 인증서 저장소를 사용했습니다. 운영 설정과 과거 요청의 웹 화면은 미검증입니다.
- `scripts/check_public.sh` 통과.

| 순증 | cso.py | server.py | daemon.py | 핵심 파일 누계 | 남은 줄 |
|---|---:|---:|---:|---:|---:|
| PR 5 | 0 | 0 | 0 | +18 | 82 |

제품 코드 순증 279줄. ID 검사 모듈 163줄.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
