## manual 논문 스킬 절 정정 (source 8105613, storage branch 동반)

- 이미 main에 병합된 #830(a2a7477) receipt 보호를 "아직 main에 반영되지 않았다"고 한 문장을 지웠다.
- `labhq talent` 기록이 효용을 확인해 주는 것처럼 읽히던 문장을 "어느 단계에 붙고 결과에서 참조됐는지"로 고쳤다.
- C3 patch(`talent660-manual0846.patch`)를 `git apply --check`와 내용 검토 뒤 적용했다. 두 문장만 바꿨고 manual은 177,255→177,214자(LF, `wc -m`)다.
- doctor·test 코드와 #678 상태 드라이브 변경은 그대로다. 이 문서 정정은 #846 PR이 아니라 이후 storage PR에만 동반한다. 실제 CI는 미확인이다.
