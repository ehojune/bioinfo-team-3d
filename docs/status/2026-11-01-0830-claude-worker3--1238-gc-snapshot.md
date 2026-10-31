# #1238 — 3.10 `SystemError`: CI stall 진단 스레드가 gc를 훑던 것

| 항목 | 내용 |
|---|---|
| 결론 | 원인은 인터프리터가 아니라 진단 스레드의 `gc.get_objects()`다. 진단은 faulthandler 덤프만 남긴다 |
| 바뀐 것 | `.github/workflows/test.yml`·`scripts/ci_suite.py`의 snapshot에서 gc 순회와 asyncio 대기 task 출력을 뺐다. `scripts/patch_notes.py`는 `*[list]`로 바꿨다. 시험 두 건 |
| 실행한 것 | 재현 스크립트(3.10.21에서 300회 중 54회 실패, list는 0회), 새 시험 2건 통과, 옛 generator 코드로 되돌리면 patch_notes 시험이 SystemError로 실패, CI 정책 시험 293건 통과 |
| 미해결 | 진단에서 asyncio 대기 task의 stack은 더 출력하지 않는다. 필요하면 이벤트 루프 스레드 안에서 모으는 방식으로 따로 다룬다 |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/issues/1238 |
