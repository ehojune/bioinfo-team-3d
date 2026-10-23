@codex-tech-lead 보고서와 정리본에서 주장별 원문 위치·접근 상태·최초 실증 출처를 함께 확인합니다. Closes #907.

- 기존 claim anchor·출처 링크를 유지하고 미해결 주장·원문 미확보·재인용·순환·보충자료 누락과 Methods/QC 위치를 표시합니다. 결과 승인(CP2)이 거부한 원출처의 재인용은 synthesis·anchor에서도 차단하며 이전 형식 문헌 요약의 분모도 유지합니다.
- #908·#909와 main #916의 인계를 재사용합니다. 원문 접근 기록의 nullable 보충자료 판정과 OA/PDF metadata를 보존하며 synthesis 입력은 기존 context 길이 안에 둡니다.
- 정리본은 기존 internal/restricted scope 판정으로 내부 단계·하위 단계의 원문 metadata를 제외합니다. 귀속 불명확 시 요청 전체에 보수적인 규칙을 적용하고 기존 report 표도 다시 생성합니다. 원본 ledger는 유지합니다. P1 수정: `726b4d03`.
- CP2 거부 원출처의 재인용은 결론 근거 논문 집계와 hash 검증에서도 제외합니다. 공유 helper가 근거별 lookup·CP2·lineage를 판정합니다. 독립적인 승인 근거가 함께 있으면 claim은 유지하고 그 근거만 집계하며 승인 근거의 hash는 필수입니다. 수정: `db0f1d17`, `a8db61b0`.
- 단일 fake 요청의 report→portfolio, 근거 표 단일 표시·legacy 호환성·내부 metadata 차단·결론 분모를 포함한 관련 pytest 142 passed. 합성 노출 회귀 8 failed→8 passed, 혼합 근거 오집계도 재현·수정했습니다. Python 3.10 AST·Windows inventory·public·diff 검사 통과. 실제 HTTP/PDF·기관 접속·연구·HPC는 실행하지 않았으며 실제 원문 독해 품질은 미검증입니다.
- 최신 main #920을 통합했습니다. manual 해당 절은 최신 main 대비 LF 기준 186,475→186,764자(+289). 상세 필드·검증 경계는 `docs/reference/claim-source-display-904.md`(954자)에 둡니다.

필수 CI와 같은 최종 head의 완료된 bot review·미해결 P1=0을 확인한 뒤 병합합니다. #904·#826은 닫지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
