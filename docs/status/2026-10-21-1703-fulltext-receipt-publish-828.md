Refs #828 — 부분 해결이며 이 이슈를 닫지 않습니다.

- tool 원본 receipt의 논문 ID·접근/추출 상태·본문 hash와 방법/QC 줄 범위를 CP1 descriptor에 연결합니다. 승인 뒤 실제 본문·변환 입력과 재사용 fingerprint를 검사합니다. 문자열 full_text·누락/변경·partial/unknown은 자료 요청으로 남깁니다.
- PDF 확보와 추출 완료를 구분합니다. pdftotext 성공도 unknown이며 PDF/HTML의 coverage를 원본에 보존합니다. 지원 JATS 본문 정상경로는 유지하고 coverage v2 이전 기록은 재확인을 요청합니다.
- runtime/tests/manual은 독립 검토본과 8개 Git blob이 같습니다. 해당 검토본의 선택 mock/static92건 PASS를 보존하며 이번 이력 정리에서는 pytest를 재실행하지 않았습니다. 실제 PDF 범위·OCR·기관/PI 요청·문헌 독해·호출·과학적 효용은 UNVERIFIED입니다.
- 잔여: Methods/QC에 필요한 지원 JATS 본문 확보 또는 PDF 범위 검증·OCR·보충자료 artifact 연결이 필요합니다. 후자는 미구현이며 connector 전면 확장은 포함하지 않습니다. CP1 이전 변환과 승인 계획 변경은 추가하지 않았습니다.
- Push·PR 생성·CI 조회·실제 문헌/연구 실행 없이 로컬 공개준비만 했습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
