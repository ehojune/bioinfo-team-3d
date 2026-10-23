## #658 종료하기: 멈춘 '마무리 중' 표시 (PR #895, base 611f8ea4)

- 대조: 이슈 4항목 중 1(끝나면 지움)·4(옛 gateway)는 main에 이미 있다. 2·3을 구현했다.
- 바꾼 것: gateway readiness의 bundle 행에 `elapsed_s`·`stuck`(10분 초과)을 더하고 stuck 행은 `ready`에서 뺀다. CLI `down`·웹 종료창이 단계(취소 기록·보고서 묶음 저장·결과 저장·PI 해석)와 `n분째`를 적고, 멈춘 마무리는 알리되 막지 않는다. 묶음은 `bundle_pending`이 남아 다음 시작에 다시 만든다. manual의 `down` 절 갱신.
- 검사: 새 test 2·문구 test 1 수정·JS 1. 수정 전 코드로 3 python·1 node 실패, 수정 후 관련 124 passed·2 skipped, node fail 0.
- 한계: stuck은 경과 시간 기준(진척 신호 없음). 묶음 단계만 적용, 취소 기록·결과 저장·PI 해석은 시작 시각이 없어 기존대로 막는다. 15분 남은 원래 사례의 원인은 로그가 없어 확인 못 함. 실제 gateway에서 `down`을 누른 확인과 전체 suite는 하지 않았다(CI에 맡김).
