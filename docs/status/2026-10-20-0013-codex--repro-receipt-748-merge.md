Fixes #748

공개용 소스의 문법 검사와 실행 상태를 분리하는 receipt를 추가했습니다. Python compile·R parse·bash -n, 코드/lock hash, 도구 판본·상대 cwd·변수 이름(값 제외)·명령·예상 출력·시각·결과를 기록하고 ReportPackage·정리본·bench manifest에 연결합니다. 기본 not_rerun, 입력 누락 blocked_input, 도구 없음 unavailable을 유지합니다.

- origin/main 4787231을 merge했습니다. HANDOFF·reference 목차·portfolio 충돌 3곳에서 양쪽 내용을 보존하고 receipt와 실행 보고서를 함께 연결했습니다. receipt/bench 동작과 기존 P2는 바꾸지 않았습니다.
- HANDOFF 큐에 #767·#765·#766·#768 OPEN을 반영했습니다. #762·#763·#764의 병합·PI 배포는 총괄 인계 근거이며 실제 연구·게시·Nextflow PASS를 뜻하지 않습니다.
- 10-19 실제 11,376,303/14M, 미사용 2,623,697 → 올림 3M+4M으로 10-20 한도 7M 확정을 HANDOFF/reference에 기록했습니다. README.en 한 문장으로 low-level off와 init preset ab/actions confirm을 구분했습니다. HANDOFF 3,780→3,910자, README.en 19,075→19,161자.
- 이번 검증: subprocess를 금지한 mock/static pytest 2 passed, Python 3.10 AST 4파일 PASS. public·diff·patch-notes·notes 검사를 적용했습니다. 기존 suite와 실제 process integration은 다시 돌리지 않았습니다. 이전 기능 검증 168 passed/2 skipped는 통합 전 결과입니다.
- PI 요청·up/down·원자료·실제 분석·다운로드·HPC·기관 접근·실제 게시·Nextflow·설치 미실행. CLI/storage rebuild는 #754 별도 범위입니다. 최종 push 후 CI는 1회 snapshot만 남기고 총괄 driver에게 넘깁니다.

## 남은 지적

- P2 4315249511: `-`로 시작하는 Bash script basename이 option으로 해석될 수 있습니다. 이번 통합 범위에서 수정하지 않았습니다.
- Rscript 실측·실데이터 재실행·수치 동등성은 미검증입니다. 작은 fixture의 실행 PASS를 과학 결과 재현 PASS로 해석하지 않습니다.
- CI 대기·봇 호출·병합·배포는 총괄 driver가 맡습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
