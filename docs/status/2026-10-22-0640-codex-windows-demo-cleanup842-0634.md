## #842 — Windows demo cleanup 실패의 다음 근거 (source 95346f0, base 99f1c49)

- #841 run 39677646617 job 119491647712에서 확정된 사실:
  - focus layout 하위 5검사는 ok였다.
  - `Browser.close` 5초 뒤에도 소유 Chrome의 `exit`가 관측되지 않았다. `stderr_closed=false`, active는 PipeWrap×3, ProcessWrap, Timeout이었다. 그래서 `demo cleanup incomplete`였다.
  - 부모 test는 11.87초에 끝났지만 node는 30초 상한까지 끝나지 않았다.
  - `websocket` pending은 graceful 경로에서 Chrome close를 기다리는 설계에서 나온 것이다. 별도 원인이 아니다.
  - #832 실패는 이와 달랐다. exit는 관측됐지만(ProcessWrap 없음) stdio pipe가 열린 채였다.
- 미확인:
  - Chrome이 5초 안에 끝나지 않은 원인
  - kill 뒤 exit·stdio close가 실제로 왔는지
  - 남은 18초가 어디에 쓰였는지(두 번째 demo 시작이었는지, 남은 pipe가 node를 붙잡았는지). 두 번째 test의 TAP는 없다.
- 그래서 수정은 미루고 근거만 더했다.
  - `closeDemoResources`가 마감 시점에 아직 오지 않은 소유 Chrome `exit`·stdio `close`·CDP socket `close`만 fallback kill 전에 관측하고, `late-*` 행에 `after_deadline_ms`를 stderr로 남긴다.
  - `web_office_focus_layout.cjs`는 두 demo의 시작 단계 시각을 stderr로 남긴다.
  - 늦은 이벤트는 실패를 뒤집지 않고 profile을 지우지 않으며 closed를 표시하지 않는다. timer나 handle도 늘리지 않는다.
  - 5초·30초·job 10분·parallel 7분·필수 26·Windows 선택은 그대로다.
- 검사: `web_visual_tooling.cjs`에 fake 계약 2건을 더했다(실행은 CI). 정적 검사 `test_ci_windows10m` 계약 2줄을 더했다. 로컬 CI 정책 199 passed, JS 괄호·문자열 정적 균형 HEAD와 새 파일 모두 balanced, public·diff 검사를 통과했다. guard는 유지했고 blocked는 0이다.
- 미실행: 실제 Node·Chrome·Windows. #842는 다음 Windows 실패 기록이나 원인 확인 전까지 열어 둔다. #833과 같은 원인이라고 단정하지 않는다.
