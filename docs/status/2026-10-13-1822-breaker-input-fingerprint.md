## 결론

앞 450자가 같고 끝이 다른 도구 호출 8개가 같은 preview로 잘려 폭주 경고를 내던 오탐을 고쳤다(#59 ②).

## 바뀐 것

- Claude·Codex·Antigravity의 여섯 tool 경로가 preview 전에 도구 이름과 canonical 입력 앞 4,096자를 SHA-256으로 계산한다.
- JSON 키 순서를 정렬하고 문자열 공백을 보존한다. 추가 원본 전송은 없으며 입력 미보고·잘린 legacy 이벤트는 반복 횟수를 끊는다.
- 단일 trip·연속 오류 판정·shadow 동작을 유지한다. manual 안전 장치·한계 절을 갱신했다(118,441 → 118,899자).
- 기준 `afbcd2e`: 선행 #532 직원 제어·#548 실행 상태 표시를 보존했다.

## 실행한 것

- 새 오탐 회귀 6건: 기준 main에서 6 failed, 수정 후 통과.
- 관련 pytest: 114 passed, 1 skipped. JSON 키 순서·공백·입력 미보고·4,096자 경계·multi-line error·runner shadow 유지·실측 스트림 포함.
- Windows 전체 pytest 1회: 5,948 passed, 66 skipped, 2 failed. Codex fixture는 새 hash 필드 기대값을 추가한 뒤 위 관련 검사에서 통과. 나머지는 semantics 내부 테스트의 `disabled.json` PermissionError이며 단독 재검사에서 1 passed로 재현되지 않았다.
- 공개 검사·diff 검사 통과.

## 미해결

- canonical 입력 앞 4,096자 이후 차이는 기존 정책대로 구별하지 못한다.
- 최종 Windows 전체 통과 판정은 CI 결과를 확인한다. 로컬 전체 1회 결과를 통과로 세지 않았다.
- 레인 임시 폴더 삭제는 실행 환경의 정책으로 차단돼 정리를 인계한다.
- 리뷰 판단·병합은 개발 총괄에게 인계한다.

Refs #59

🤖 Generated with Codex (gpt-6) for the labhq dev lead
