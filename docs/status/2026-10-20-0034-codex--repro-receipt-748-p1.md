Fixes #748

재현 receipt를 ReportPackage·정리본·bench manifest에 연결합니다. Python/R/bash 문법과 실행 상태를 분리하고 코드/lock hash·도구·명령·변수 이름(값 제외)을 기록합니다. 기본 not_rerun·입력 누락 blocked_input·도구 없음 unavailable을 유지합니다.

- P1 4315296432: 저장 메서드가 없는 기존 Hub 계약도 benchmark 결과를 유지합니다. 실제 Hub의 save_request는 계속 호출합니다.
- P1 4315630166: terminal 이후 비동기 bundle 완료를 요청의 남은 제한 시간 안에서 기다리고 최신 request로 수집합니다. 완료 전 빈 receipt를 저장하지 않으며 대기 시간 초과는 실패로 남깁니다.
- P1 4315630179: 반환된 rid를 수집용 복사본에 보완합니다. 원래 request와 공개 후보 필터는 유지합니다.
- 신규 회귀 5건이 수정 전 head 5a69d19에서 실패하고 수정 뒤 통과했습니다. 관련 pytest 43 passed(지정 lanes Python·기존 D dependency site), public·diff·patch-notes·notes 검사를 적용했습니다. main은 현재 branch의 조상으로 추가 충돌이 없습니다.
- mock Hub/Runner/Server·작은 manifest/source fixture만 사용했습니다. PI 요청·프로세스 종료·실제 분석·원자료·다운로드·기관·HPC·실제 게시·설치 미실행입니다. CI는 push 후 조회하며 재리뷰·병합·배포는 총괄 driver가 맡습니다.

## 남은 지적

- P2 4315249511: Bash 선행 `-` basename의 option 해석. 이번 P1 수정에서 건드리지 않았습니다.
- P2 4315630188: 복수 script의 공유 예상 출력으로 실행 판정이 순서에 의존할 수 있습니다. 이번 P1 수정에서 건드리지 않았습니다.
- Rscript 실측·실데이터 재실행·수치 동등성 미검증. fixture PASS는 과학 결과 재현 PASS가 아닙니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
