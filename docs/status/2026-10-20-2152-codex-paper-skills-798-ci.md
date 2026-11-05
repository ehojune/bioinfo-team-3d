Fixes #798
PR #821 · CI 보정 a9d0e56

논문 스킬은 CP1에서 metadata·선정 hash만 확인하고 승인된 담당 단계에서 생성합니다. 검증된 문서만 읽기 전용 grant에 연결하며, 성공한 결과의 정확한 ID·내용 hash 참조가 있어야 called=true입니다. 승인·재승인·조상 입력 보호·지연 스킬 재사용 제외 계약을 유지합니다.

- Linux 3.12 job 118620897833의 네 실패는 이전 prompt snapshot 두 건과 추출이 없어진 준비 단계에서 중단을 기대하던 두 건이었습니다.
- Prompt는 승인 후 생성 문장만 반영했고 그 문장을 되돌리면 이전 두 digest와 일치함을 검사합니다. schema·output_types off/on assertion은 유지합니다.
- 준비 callback 미호출 뒤 승인 hash가 있는 CSO step에서 runner shutdown·asyncio cancellation을 주입합니다. 두 예외의 전파, 연구 단계 미dispatch, 계획·승인 hash 보존을 검사합니다. 제품 예외 처리는 이미 맞아 수정하지 않았습니다.
- 검증: 원 head에서 네 실패 재현 → 같은 네 node PASS(로컬 1.55초), 관련 mock/static 17건 PASS(4.06초). Python 3.10 AST/compile 3파일·public·diff·patch-notes·notes 검사도 통과했습니다. 전체 실패 trace는 레인 로컬 로그에 보존했습니다.
- 한계: 실제 process/thread·CLI·PDF·네트워크·PI·연구·bench·HPC는 미실행입니다. Windows/Python 3.10 실행·최종 head CI·독립 재리뷰는 controller가 맡습니다. CI 조회·대기·봇 호출·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
