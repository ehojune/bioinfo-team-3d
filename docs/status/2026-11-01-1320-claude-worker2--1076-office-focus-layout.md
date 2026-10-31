# #1076 rev28: web_office_focus_layout의 Windows 30초 초과

- 결론: 시험 안 확인 다섯 개는 합쳐 약 3초에 끝나는데, CI Windows에서는 같은 스크립트가 28.5초 걸려 30초 상한을 세 번 넘겼다(PR #1221·#1249 등). 로컬은 2.9초다. 시험 쪽 상한을 120초로 늘렸고(Chrome 세션 둘), 원인 후보인 프록시 확인도 껐다.
- 시간이 드는 곳(PR #1249 Windows 로그의 `focus driver` 단계 기록, 실패 시 stderr에 나옴):
  - Chrome 시작 → 디버그 포트 준비까지 3.0초.
  - `navigate-start`(3.2초) 뒤 **11.6초 동안 Chrome이 페이지 서버에 첫 요청을 보내지 않음**(`navigate-waiting`이 5초 시점에 `page_requested:false`). 서버는 요청 뒤 7ms에 응답.
  - 페이지 boot에 6.5초(`boot-start` 14.9초 → `boot-ready` 21.4초).
  - 시험 안 확인 다섯 개는 2.5ms·1.7ms·223ms·2.8s·100ms.
- 원인 제거(rev29): `tests/support/demo_driver.cjs`가 Chrome에 `--no-proxy-server`를 준다(데모는 자기 loopback 서버만 쓴다). 위 11.6초가 Windows 시스템 프록시 확인 때문이라는 가설이 맞는지는 이 PR의 Windows CI와 이후 실행으로 본다. `config/ci/windows-boundary-v1.json`에서 `test_web_3d.py`의 소스 digest를 새 값으로 바꿨다(소스가 바뀌면 Windows 선택이 `stale mixed Windows contract`로 거절한다).
- 바뀐 것: `tests/test_web_3d.py`의 바깥 시간 상한 표에 `web_office_focus_layout.cjs: 120`(스크립트가 Chrome 세션을 둘 순서대로 열고, 각 top-level 시험의 상한이 120초라 같은 값). `tests/web_office_focus_layout.cjs`는 건드리지 않았다(확인 내용 그대로).
- 실행한 것: `tests/test_web_3d.py` 96 passed, 1 skipped(로컬). Windows 느린 러너 재현은 못 했다.
- 제품 웹 코드 원인은 아니라고 본다: 서버 응답은 7ms이고 지연은 Chrome이 요청을 보내기 전과 페이지 boot 구간이다. boot 6.5초가 제품 JS 때문인지 느린 CPU 때문인지는 분리하지 못했다.
- 가설(미검증): Windows 러너에서 시스템 프록시 확인이 Chrome의 첫 요청을 늦춘다. 그래서 위 `--no-proxy-server`를 줬다. 효과는 이후 CI의 `focus driver` 기록(`navigate-waiting`·`server-request`)으로 확인한다.
