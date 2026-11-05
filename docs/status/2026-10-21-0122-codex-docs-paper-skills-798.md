Refs #798, #821

논문 스킬 문서를 병합된 동작에 맞췄습니다(8aba20b).

- CP1 metadata 확인 → 승인된 단계에서 생성 → 검증 문서의 읽기 전용 주입 → 성공 결과의 정확한 ID·폴더/문서 hash 참조를 설명합니다. 승인·취소 경계와 참조 receipt/실제 tool 호출·과학 효용의 구분을 유지합니다.
- README 한영판은 CI 통과 대상을 병합된 기능으로 한정합니다. 실제 연구 동작·효용은 미검증입니다. 운영 큐·예산·작업자 정책은 독자 문서에 추가하지 않았습니다.
- LF 문자 수: README 11,684→11,686, README.en 17,598→17,627, manual 175,212→175,153. 독자 문서 합계 204,494→204,466(-28); reference 2,188→2,218(+30).
- 병합 코드 대조·로컬 링크/heading·diff·public·patch-notes·notes 검사. 제품·테스트·AGENTS·HANDOFF·CI 파일과 생성 목차는 변경하지 않았으며 새 테스트·pytest·실제 연구/bench/native fixture를 실행하지 않았습니다.
- 독립 검토 P1=0 뒤 읽기 권한 설명을 검증된 주입 문서로 한정했습니다.
- 로컬 커밋만 준비했습니다. #793 CI 정책 적용 전 불필요한 전체 Windows 실행을 막기 위해 push·PR 생성은 총괄이 나중에 진행합니다. 이번 작업에서 CI 조회·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
