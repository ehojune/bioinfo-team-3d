Fixes #798
PR #821 · 기능 c878148 · 읽기 권한 P1 6b37d70 · main 통합 df2a51c

논문 스킬 변환을 승인된 담당 단계의 dispatch로 옮기고 장착과 결과의 출처 표기를 구분합니다. root core_structure가 찾은 정상 DAG의 논문 입력 Read 거부를 수정했으며 최신 main 8151f23도 명시적으로 merge했습니다.

- CP1은 metadata·담당 단계·선정 hash만 고정합니다. 기존 예산·retry·취소 경계로 지연 build하며 승인 계획·재승인 검사·#807의 deferred 재사용 제외를 유지합니다.
- policy 구성 때 receipt와 owned 문서의 길이·내용 hash·무링크 handle을 재검사해 정확한 파일만 기존 read grant에 연결합니다. inputs 보호·조상 권한·native 쓰기 제한은 유지하며 namespace 충돌 시 장착을 생략합니다. 광역 allowlist·policy 우회 분기는 추가하지 않았습니다.
- 성공한 결과의 정확한 스킬 ID/폴더·문서 hash 참조와 유지된 사본이 있어야 called=true입니다. 장착만으로는 참이 되지 않으며 미참조·실패·증거 부족은 false와 이유를 남깁니다. 이는 출처 표기이며 tool 실행·과학적 효용의 증거는 아닙니다.

충돌 선택: HANDOFF 한 곳에서 main의 #818 queued-halt 검증 설명과 #821 논문 입력 계약을 함께 보존했습니다. manual은 양쪽 내용이 자동 병합됐습니다. #818·#819 코드/검사와 main 생성 목차를 손대지 않고 가져왔습니다.
검증: 통합 후 focused mock/static 63건 PASS, guard 이벤트 0건. main 관련 14파일과 #821 제품/계약 9파일 동일성을 확인했고 Python 3.10 AST/compile 7파일·public·diff·patch-notes·notes 검사도 통과했습니다. 이전 Read P1은 4건 red 뒤 관련 80건/최종 권한·profile 14건으로 검증했습니다.
한계: grant는 policy 구성 시 검증하며 파일 불변을 보장하지 않습니다. 실제 CLI·native sandbox·process/thread·PDF·네트워크·연구·bench·HPC는 실행하지 않았습니다. 최종 head의 Windows/Python 3.10/safety CI·독립 재리뷰는 root/native owner가 담당합니다. CI 조회·대기·봇 호출·PR 병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
