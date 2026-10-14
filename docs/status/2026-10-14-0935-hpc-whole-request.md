## 결론

요청 시작에 HPC/이 PC를 고르고 모든 처리 단계를 그 위치에서 이어 간다.

## 바뀐 것

- 브리핑 뒤·CP1 전 실행 위치 카드. 온라인 scheduler·내장 HPC 직원이 없으면 로컬.
- HPC는 같은 runner·요청 작업 폴더에서 순서대로 잡 실행. 혼합 계획·산출 경로 충돌·잡 없는 완료는 거부.
- 로컬은 HPC 도구와 broker 제출 차단. 선택을 요청·CP1·보고서 부록에 기록.
- 매뉴얼 갱신. 중단된 미커밋 작업을 보존하고 최신 main을 반영했다.

## 확인

- 기준 main `d56253a`: 새 회귀 18 failed, 1 skipped. branch 관련 검사: 295 passed/1 skipped + 205 passed/20 skipped. 단독 라우팅 보완 뒤 38 passed/1 skipped.
- 로컬 Windows 전체 pytest: 다른 레인의 연속 실행으로 차례를 확보하지 못해 미실행. 완료 조건이 남아 draft로 인계한다.
- CI: PENDING.
- 공개 검사·diff 검사 통과. 패치노트·목차는 기록 커밋에서 확인한다.

## 남은 지적

- 실제 HPC·공유 계정 실행은 서버 복구 뒤 확인. mock은 같은 폴더의 앞 단계 산출 접근을 검증하며 POSIX 권한은 Linux CI에서 확인한다.
- 초기 입력·상위 폴더 접근 권한은 운영자 준비가 필요하다. 계획·제출 검사는 직원 CLI의 sandbox가 아니다.
- 실제 HPC·PI/trial 실행·설정 수정·리뷰 요청·인라인 답글·병합은 하지 않았다.

Refs #430

🤖 Generated with Codex (gpt-6) for the labhq dev lead
