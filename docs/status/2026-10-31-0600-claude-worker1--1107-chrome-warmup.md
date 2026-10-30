# #1107 Chrome 예열 step

- 결론: Windows job이 critical 단계(첫 브라우저 시험) 앞에서 `scripts/ci_chrome_warmup.py`로 Chrome을 시험과 같은 플래그·빈 프로필로 한 번 띄워, DevTools 포트와 첫 페이지 target까지 걸린 시간을 로그에 한 줄로 남기고 끝낸다. cold start 검증을 이 step이 맡는다. 상한은 드라이버 기본 `START_MS`와 같은 60초(step은 2분)이고 실패하면 "Chrome warm-up failed"로 이름 붙어 끝난다. Chrome이 없는 곳에서는 알림만 내고 통과한다.
- 시험의 30초 시작 상한(`STARTUP_BUDGET_MS`)과 critical step의 3분 상한은 바꾸지 않았다(이제 데워진 Chrome의 시작을 확인한다). 시험이 그 값을 지키는지, 예열 상한이 `START_MS`와 같은지 `tests/test_ci_chrome_warmup.py`가 확인한다.
- 정책 변경(총괄 결정 B안, #1107): `docs/reference/ci-reuse.md`의 "사전 기동 없음" 원칙을 "첫 Chrome의 cold start는 예열 step이 60초로 검증, 시험은 데워진 Chrome의 30초"로 고쳤고, 같은 문서에 남은 옛 상한(job 14분·pool 9분)을 18분·13분으로 바로잡았다.
- 근거: 같은 job에서 첫 Chrome인 `web_resizable_panels`만 35~51초 꼬리가 있었다(#1107 댓글 6533222880).
- 바꾼 것: `scripts/ci_chrome_warmup.py`(새), `tests/test_ci_chrome_warmup.py`(새, 8건), `.github/workflows/test.yml`(step 하나), `config/ci/windows-boundary-v1.json`(`known_tests` 한 줄).
- 하지 않은 것: 통과한 run에서 시험의 시작 구간을 남기는 일. 시험 프로세스의 출력은 pytest가 실패할 때만 보이고, step 요약은 API로 읽을 수 없다. 대신 예열 step의 한 줄이 같은 정보를 로그에 남긴다.
- 한계: 예열이 시험의 cold start를 실제로 없애는지는 미검증이다. job 시간이 최대 2분 늘 수 있고 job 상한(18분)은 그대로다.
- 효과 확인: PR 병합 뒤 Windows run 몇 개의 예열 시간과 `web_resizable_panels` 소요(`--durations`)를 #1107에 모은다.
