# 2026-11-09 claude-worker3 — #1336 r9: 카드 경고 동작을 manual 15에 반영

## 결론
PR #1345 리뷰의 P1(manual 미반영)을 고쳤다. manual 15 한·영의 "의미 장치 상태" 항목에, 카드에서 장치 상태·경고가 카드 종류 문장 뒤에 온다는 것과 `configured_off`는 경고하지 않는다는 것을 적었다.

## 바뀐 것
- `docs/manual/15-config.md`, `docs/manual/15-config.en.md`

## 실행한 것
- `patch_notes.py check`, `check_public.sh`

## 미해결
- #1345 P2(CP1에 논문 스킬 줄·재배정 경고·이어 가기 정보가 있으면 그 줄이 맨 앞에 붙음)는 W2의 `cso.py` 작업 뒤 r10에서 고친다
