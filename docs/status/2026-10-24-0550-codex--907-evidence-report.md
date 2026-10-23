@codex-tech-lead 보고서와 정리본에서 주장별 원문 위치·접근 상태·최초 실증 출처를 함께 확인합니다. Closes #907.

- 기존 claim anchor·출처 링크를 유지하고 미해결 주장·원문 미확보·재인용·순환·보충자료 누락과 Methods/QC 위치를 표시합니다. 결과 승인(CP2)이 거부한 원출처의 재인용은 synthesis·anchor에서도 차단합니다.
- #908·#909와 main #916의 인계를 재사용합니다. 원문 접근 기록의 nullable 보충자료 판정과 OA/PDF metadata를 보존하며 synthesis 입력은 기존 context 길이 안에 둡니다.
- 단일 fake 요청의 report→portfolio와 논의 아래 근거 표 단일 표시를 포함해 관련 pytest 358 passed. Python 3.10 AST·Windows inventory·public·diff 검사 통과. report Node 6 passed. 실제 HTTP/PDF·기관 접속·연구·HPC는 실행하지 않았으며 실제 원문 독해 품질은 미검증입니다.
- manual 해당 절은 LF 기준 185,493→185,741자(+248). 상세 필드·검증 경계는 `docs/reference/claim-source-display-904.md`(843자)에 둡니다.

필수 CI와 같은 최종 head의 완료된 bot review·미해결 P1=0을 확인한 뒤 병합합니다. #904·#826은 닫지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
