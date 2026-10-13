## 결론

승인 복구 후보가 없는 environment 실패를 시설팀이 요청·논리 단계당 한 번 진단합니다. 원 실패와 hint는 유지합니다.

## 바뀐 것

- 기존 allowlist 복구 우선. consult·복구 실패·거절·timeout·재실패에는 진단을 추가하지 않습니다.
- ask를 저장해 재시작·재연결 중복을 막고 기존 상한·예산·read-only 제한과 저장된 기한을 적용합니다.
- 서명·오류 발췌·doctor 요약만 사용합니다. 원 요청·계획·세션을 넘기거나 LLM 답변을 실행하지 않습니다.
- 기준 `60ffb1b`, 선행 FACILITY35 #567 포함. CP490 순서는 총괄 결정으로 해제됐습니다.

## 확인

- 새 회귀: 기준 코드에서 1 failed → 구현 후 통과. 관련 테스트 305 passed. 최종 코드 커밋의 새 테스트 31 passed.
- Windows 전체 pytest: 미실행(UNVERIFIED). 다른 레인의 전체 실행 때문에 10분 대기 후 중단했습니다. 동시 전체 실행 금지를 지켜 총괄에게 인계합니다.
- 공개 검사·diff 검사 통과. 레인 전용 임시 폴더 삭제는 자동 승인 검토의 blocked by policy로 차단돼 정리를 인계합니다. manual 121,424→121,751자.

## 남은 지적

- 실제 설치·모델 호출·PI/trial 시운전은 수행하지 않았습니다. 봇 리뷰 요청·판단·병합은 총괄에게 넘깁니다.

Refs #35

🤖 Generated with Codex (gpt-6) for the labhq dev lead
