Fixes #798
PR #821 · 기능 c878148 · 읽기 권한 P1 수정 6b37d70

논문 스킬 변환을 승인된 담당 단계의 dispatch로 옮기고 장착과 결과의 출처 표기를 구분합니다. root core_structure 정적 검토가 찾은 정상 DAG의 논문 입력 Read 거부도 수정했습니다.

- CP1은 metadata·담당 단계·선정 hash만 고정합니다. 기존 예산·retry·취소 경계로 지연 build하며 승인 계획·재승인 검사·#807의 deferred 재사용 제외를 유지합니다.
- policy 구성 때 runner receipt와 owned 문서의 길이·내용 hash·무링크 handle을 재검사해 정확한 파일 하나만 기존 read grant에 연결합니다. inputs 보호·조상 권한·native 쓰기 제한은 유지하고 namespace 충돌 시 스킬 장착을 생략합니다. 광역 allowlist·policy 우회 분기는 추가하지 않았습니다.
- 성공한 결과의 정확한 스킬 ID/폴더·문서 hash 참조와 유지된 사본이 있어야 called=true입니다. 장착만으로는 참이 되지 않으며 미참조·실패·증거 부족은 false와 이유를 남깁니다. 이는 출처 표기이며 tool 실행·과학적 효용의 증거는 아닙니다.

검증: daemon/정책 조합 Read 거부 4건 red 뒤 관련 mock/static 80건 통과. 최종 권한·native profile 14건도 통과했고 마지막 guard 추가 이벤트는 0건입니다. 정확한 문서 읽기, Write/Edit 거부, 다른 입력/private/링크 우회 거부, 같은 크기 내용 변경·누락·링크 교체의 grant 제외, 조상 namespace 보존을 확인했습니다. Python 3.10 AST/compile·public·diff·patch-notes·notes 검사도 통과했습니다.

변경: paper_skills·CSO·runner workspace/daemon·continuation proof 한 조건, 관련 tests와 manual/reference/HANDOFF·기록. 이번 P1 제품 변경은 workspace/daemon에 한정합니다.
한계: grant는 policy 구성 시 검증하며 파일 불변을 보장하지 않습니다. 실제 CLI·native sandbox·process/thread·PDF·네트워크·연구·bench·HPC는 실행하지 않았습니다. Windows/Python 3.10 런타임·CI·독립 재리뷰는 controller가 담당합니다. CI/봇 대기·호출·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
