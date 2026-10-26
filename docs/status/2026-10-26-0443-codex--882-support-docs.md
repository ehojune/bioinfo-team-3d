Refs #882. HANDOFF·manual의 지원/복구 안내와 AGENTS·CLAUDE·운영 기준·pi-qa의 역할/창구 안내를 최신 PI 인계에 맞췄습니다.

- [최종 통합 배정](https://github.com/ehojune/bioinfo-team-3d/pull/970#issuecomment-6407311846)에 따라 최신 main을 충돌 없이 반영했습니다. #973·#972·#975 병합과 #965·#653 CLOSED를 안내에 반영했습니다. #606 전체 완료·실제 browser·transport/OS 재시작·새 설치·반복 복구는 미검증이며 #882 OPEN·별도 실행 GO를 유지합니다.
- 이번 통합(LF 기준): HANDOFF 4,603→4,582자, manual 194,255→194,194자. 합계 82자 감소. README 한영판·직원 YAML과 대조했으며 다른 담당자의 async 저장·snapshot 안내를 보존했습니다.
- 현행 안내·변경 링크·UTF-8·integrations·public·diff·patch-notes·notes 검사 PASS. 문서 변경으로 로컬 runtime test는 실행하지 않았습니다.
- 최종 head 필수 CI·알려진 모든 head P1 수정검증·충돌/head guard 확인 전까지 hold입니다. PI 임시 리뷰 생략을 적용하며 새 봇 PASS로 기록하지 않습니다. 코드·설정·설치·배포·연구·HPC 실행은 없습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
