# 펼침·결정 이력 확인 근거

조회용 기록입니다. 사용법은 [매뉴얼](../manual.md#웹-사무실)에 있습니다.

| 확인 | 근거 |
|---|---|
| 펼침은 받은 데이터만 사용 | `web/state.js`의 `receivedText`; `web/ui/decide.js`의 `expandableText`. 전문 조회 API를 추가하지 않음 |
| 서버 제한 표시 | `util.py:event_preview`, 엔진 adapter 5종, `runner/hpc_jobs.py`의 script preview. 기존 글자·줄 제한 유지 |
| 결정 원본 보존 | 숨김은 localStorage의 `labhq_hidden_decisions:<모드>:<인스턴스>`에 ID만 기록. gateway 저장·감사 API 변경 없음 |
| 데모 store 격리 | `cli.py:_demo`는 `mkdtemp(prefix="labhq-demo-")`와 `_demo_settings`를 사용. gateway·runner 경로를 임시 폴더로 지정 |
| 실제 이력 출처 | `gateway/server.py:approval_history`는 해당 Hub store의 `approval_decision`만 반환. 브라우저는 이력을 localStorage에서 읽지 않음 |
| 브라우저 데모 분리 | `web/index.html`의 `MODE` 분기에서만 `Demo.start()` 실행. 새 페이지는 새 reducer state를 생성 |
| 재현 테스트 | `test_demo_decisions_never_enter_live_history`: 환경변수가 실제 state를 가리켜도 demo 기록 1건·live 기록 0건. 실제 인스턴스 조회·변경 없음 |

`demo timeout auto-approve`는 `cli.py:_demo_auto_approve`에서 나옵니다. CLI 데모도 gateway 화면을 쓰므로 화면의 LIVE 표지만으로 실제 인스턴스라고 판단하면 안 됩니다. 코드상 유입 경로는 없으며, 스크린샷이 어느 인스턴스인지까지는 확인하지 않았습니다.
