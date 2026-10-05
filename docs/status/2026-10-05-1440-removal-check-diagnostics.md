## 2026-10-05 · 운영 — 그림자 제거 검사의 실패 진단

- 결론: `test_semantics_shadow_remove`가 Windows에서 가끔 실패하는데(오늘 6번 중 1번 등), 실패 메시지가 안쪽 pytest 출력의 마지막 3,000자뿐이라 faulthandler 스택만 보이고 어느 test인지 알 수 없었다. 이제 실패한 test 줄과 덤프 머리 줄을 먼저 보인다. 자식 출력은 UTF-8인데 cp949로 읽어 reader thread가 깨지던 것도 고쳤다.
- 바뀐 것: `scripts/semantics_shadow_remove.py`(subprocess 세 곳 `encoding="utf-8", errors="replace"`, `_ok` 메시지).
- 실행한 것: 새 test(긴 꼬리 위에 실패 줄과 덤프 머리)와 `tests/test_semantics_shadow_remove.py` 전체 통과, `scripts/check_public.sh`.
- 미해결: 간헐 실패의 원인 자체는 다음 실패 때 이 메시지로 찾는다.
- 근거: `scripts/semantics_shadow_remove.py`.
