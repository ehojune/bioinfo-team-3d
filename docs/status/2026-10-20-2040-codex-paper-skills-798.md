Fixes #798
PR #821 · 기능 커밋 c878148

논문 스킬의 무거운 변환이 계획 승인(CP1)을 막지 않도록 승인된 담당 단계의 dispatch로 옮겼습니다. 최종 기록은 장착과 결과의 실제 출처 표기를 구분합니다.

- CP1은 제공된 DOI/PMID·full_text 접근 metadata·담당 단계와 선정 hash만 고정합니다. build는 기존 예산·retry·취소 경계를 사용하며 승인 계획을 바꾸지 않습니다.
- 지정 정규직의 owned `inputs/paper_skills/<skill_id>.md`와 engine 사본을 hash 검증 후 주입하고 실행 뒤 제거합니다. 성공한 결과의 정확한 ID/폴더·문서 hash 참조와 유지된 사본이 있어야 `called=true`입니다. 참조 없음·변경·실패·증거 부족은 false와 이유로 남깁니다. 이는 출처 표기이며 tool 실행·과학적 효용을 증명하지 않습니다.
- build·예산 대기 중 승인/선정·재사용 proof drift는 중단·재승인을 요구합니다. 교정/slot repair도 같은 경계를 적용하고 기존 provenance에 receipt를 보존합니다. 내용 proof 없는 지연 빌드 노드와 종속 노드는 CP1 재사용 후보에서 제외하며 #807의 독립 노드·기존 고정 hash 검증은 유지합니다.

검증: CP1 선행 build/불명확 called 계약과 교정 승인·slot repair 예외의 red를 보존했습니다. 관련 3개 파일의 mock/static 66건, Python 3.10 AST/compile, public·diff·patch-notes·notes 검사를 통과했습니다. 실제 PDF·네트워크·연구·process/thread 실행은 하지 않았습니다.

변경: paper_skills·CSO·runner workspace/daemon·continuation proof 한 조건, 관련 tests와 manual/reference/HANDOFF·기록.
한계: 실제 사용은 결과의 출처 표기로만 판정합니다. 실제 전환 지연·과학적 효용·Windows/Python 3.10 런타임은 미검증이며 CI·새 head 리뷰는 controller가 맡습니다. CI/리뷰 대기·봇 호출·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
