## 2026-10-05 · 운영 — 패치노트 검사가 숫자로 읽힌 sha를 알려 줌

- 결론: 손으로 쓴 행의 짧은 sha가 숫자로만 되어 있으면(`7999708`) YAML이 정수로 읽어 검사가 "커밋이 없다"고만 했다(PR #410). 이제 그 행을 짚어 따옴표로 감싸라고 알려 준다.
- 바뀐 것: `scripts/patch_notes.py` `entry_shas()`. `rows` 명령은 원래 `yaml.safe_dump`으로 따옴표를 붙이므로 그대로다.
- 실행한 것: 새 test가 수정 전 실패, 수정 뒤 통과. `tests/test_patch_notes.py` 17 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: `scripts/patch_notes.py`.
