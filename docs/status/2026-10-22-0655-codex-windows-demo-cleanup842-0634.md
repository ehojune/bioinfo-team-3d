## #842 — demo cleanup 5초 예산 배분 (source 21afaa2, 앞선 진단 95346f0)

- 구조 원인(확인): 5초 전부를 graceful `Browser.close` 대기에 쓰고, 마감 콜백에서야 소유 child를 `kill()`한 뒤 같은 콜백에서 바로 실패했다. 그래서 kill 결과를 관측할 시간이 0이었다(C3 `lookup-842-cleanup-design-0650.md`).
- 수정: 총 5초 마감은 그대로다. graceful 3초가 지나도 끝나지 않았으면 소유 ChildProcess와 CDP socket만 한 번 닫는다. 그 뒤 남은 2초 안에 실제 child stdio `close`·socket `close`·server close가 모두 와야만 완료한다.
  - kill 반환값, `killed`, 예외 삼킴으로는 성공하지 않는다.
  - exit만 오고 stdio가 열린 #832 형태는 실패다.
  - 정상 graceful 경로의 kill은 0회, forced 경로는 1회다. 마감 콜백은 kill을 다시 하지 않는다.
  - 늦은 관측(`late-*`)은 유지한다.
- 검사:
  - fake 계약(`web_visual_tooling.cjs`): forced 뒤 마감 전 close가 오면 PASS(이전 구조에는 3초 phase가 없어 FAIL). kill이 아무것도 반환하지 않거나, true를 반환하거나, 예외를 던져도 close가 없으면 FAIL. exit만 있고 stdio가 열려 있으면 FAIL. graceful이면 kill 0. phase timer 해제.
  - 실제 Chrome 회귀 1개(`web_office_focus_layout.cjs`의 세 번째 test): 테스트 전용 `testWithholdBrowserClose`로 forced 경로를 타고, `['forced','closed']`를 관측해야 성공한다. 기존 두 demo와 기능 단언은 그대로다.
  - 로컬: 정적 CI 계약 199 passed, JS 정적 괄호 균형, public·diff 검사 통과. guard 유지, blocked 0.
- 미실행·미확인:
  - 실제 Node·Chrome·Windows 결과
  - Chrome이 graceful로 닫히지 않는 내부 원인
  - forced close가 Windows에서 2초 안에 오는지. 하위 프로세스가 pipe를 쥐면 계속 실패한다.
  - 새 회귀가 Chrome 시작 1회와 graceful 3초를 더해 focus 파일의 30초 상한 여유를 줄이는 정도
- #842는 열어 둔다.
