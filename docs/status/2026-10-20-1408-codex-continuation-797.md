Fixes #797
Refs #672, #680
PR #807. 기능 `232690b`.

CP1 카드 전에 재사용 후보의 선언 입력·의존 산출 hash·과거 ledger/task/agent·선언 도구/engine 설정을 검증합니다. 검증한 snapshot을 계획 hash와 승인 receipt에 묶고 카드에 사본을 표시합니다. 승인 뒤·재개·dispatch 직전 증거가 바뀌면 중단하여 새 CP1을 요구하며, 재사용 단계를 조용히 재실행하지 않습니다.

- 노드 사양은 Pydantic 기본값으로 정규화합니다. 독립 노드/갈래 수정은 영향 없는 후보를 보존합니다. 과학적 brief·scope·가설·topic·가정·protocol·pack의 차이는 문구 유사도로 무시하지 않습니다.
- 내용 증거 없는 외부 입력, 과거 도구 설정이 없는 기록, manifest 없는 디렉터리는 승인 전에 재사용을 거절합니다. 기존 zone/path/link/hash 안전 walker·승인/예산 경계와 #796 input-fit·지표·Shadow 분리를 유지합니다.
- 기존 소스를 메모리에서 실행해 기본값/독립 갈래/과학 목적 회귀 3건 red를 재현했습니다. 관련 mock/static **72 passed**; 실제 child hash를 쓴 소형 디렉터리 fixture 보강 뒤 **1 passed**. 첫 계획·CP1 수정·CP2 수정·재개, 승인 후/dispatch 직전 drift·unknown·#796 분리를 검사했습니다. Thread 시작 금지·to_thread 인라인 mock·기존 guard 환경 유지, 전체 실패/성공 로그 보존.
- Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. manual LF 문자 수 166,243→166,380(+137); 상세 계약은 `docs/reference/continuation-reuse-797.md`입니다.
- 파일 변경을 잠그지 않으며 실제 설치된 도구 판본은 별도 검증 대상입니다. process/thread/signal·실제 Hub·PI/연구/원자료/HPC/bench·전체 suite·Windows/3.10 실행은 로컬 미실행입니다. 과학 효과·#672/#680 전체 해결을 주장하지 않습니다. #798/Shadow 확장 미착수; 새 head CI·리뷰·P1=0·병합·배포는 controller 담당입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
