## 결론

요청별 그림자 비교 보기를 추가하고, 다른 설정 파일로 실행 중인 서버를 `up`이 정상으로 받아들이던 결함을 고쳤다.

## 바뀐 것

- `semantics report --request <id>`: 저장된 그림자와 현재 단계·보고 산출·승인 결정을 한국어 평문/JSON으로 비교한다. 없거나 손상된 기록은 알리고 재계산하지 않는다.
- 일반 요청도 ab 기록 대상이다. 재시작·재개도 같은 종료 hook을 쓴다. 실제 누락은 다른 checkout의 off 설정으로 실행한 정황이며, 오늘 병합된 보고 집계 수정과는 무관하다.
- `up`은 서버가 응답하더라도 실행 설정 불일치를 검사한다. 살아 있는 다른 설정의 PID 기록도 보존한다.

## 실행한 것

- main 11845c0에서 최초 새 회귀 7건 실패. 수정 후 관련 327 passed, 1 skipped; 기능 제거·최종 요청별 회귀 15 passed.
- CI c637146 ([run 38374699725](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38374699725)): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 생략. Windows 전체 5,952 passed·34 skipped.
- 공개·패치노트·목차 검사 통과. manual 의미 모델 절 갱신(118,283 → 118,701자).

## 미해결

- 로컬 전체 pytest는 다른 레인의 전체 검사가 연속 실행돼 시작하지 못했다. 직렬 실행 대기를 종료하고 draft로 인계한다.
- 올바른 ab 설정의 기록 생성은 main에서도 정상이다. 'main에서 기록 생성 자체가 실패'하는 결함은 재현하지 못했다. 설정 충돌과 요청별 보기 부재를 재현했다.
- 임시 테스트 폴더 삭제가 실행 환경의 자동 승인 검토에서 거부됐다(blocked by policy). 우회 삭제하지 않았다.
- PI 설정·상태·인스턴스를 바꾸지 않았다. 실행 설정 선택은 총괄이 확인해야 한다. 과거 미관측 그림자는 복원하지 않는다. 웹 칸은 추가하지 않았다.

Refs #559

🤖 Generated with Codex (gpt-6) for the labhq dev lead
