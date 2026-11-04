# 2D 디자인 선택 기록 · 2026-10-13

PI 선택은 **E + 캐릭터 1(작은 전신 + 직무 소품)**이다. [결정과 구현 순서 · #546](https://github.com/ehojune/bioinfo-team-3d/issues/546).

| 자료 | 위치 |
|---|---|
| 비교 갤러리 | [index.html](index.html) |
| 선택한 E · 토큰 표 | [연구 저널 지면](mockups/E-claude-journal.html) |
| A–D · 보관용 | [A](mockups/research-editorial.html) · [B](mockups/sunlit-lab.html) · [C](mockups/control-room.html) · [D](mockups/shared-notebook.html) |
| 캐릭터 0–4 · 1 선택 | [비교](characters/index.html) · [크기 검증](characters/size-proof.png) |
| 비평·참고 분석 · 필요할 때만 조회 | [자료 안내](docs/reference/README.md) |
| Claude가 관찰한 기존 화면 16장 | [shots](shots/) |
| 이 PR의 전후 화면 | [comparison](comparison/) |
| 사무실·캐릭터 전후 | [office-comparison](office-comparison/) |
| 화면 비교 test·데모 움짤 | [tests/visual](../../../tests/visual/README.md) |

다른 시안과 비평은 보관용이다. 당시의 `UNVERIFIED`·캡처 실패 기록을 유지했고 개인 PC 경로만 가렸다. 외부 폰트·스크립트 URL은 원본 그대로다. 과거 보고서의 미생성 파일 링크는 당시 예정 기록이며 이 보관본에 포함되지 않는다.

첫 PR은 색·글꼴·간격 토큰과 공통 부품까지만 적용한다. 결정 카드 재배치 → 작업판·메신저·시트 → 사무실·캐릭터 1 → 화면 비교 test·데모 움짤 순서로 이어 간다.

다섯 번째 PR로 화면 레이아웃 비교 test와 README 데모 움짤(`docs/media/office-25d.gif`)을 넣었다. 디자인을 바꾸면 기준선을 `node scripts/update_visual_baseline.cjs`로, 움짤을 `node scripts/record_demo_gif.cjs`로 다시 만든다.
