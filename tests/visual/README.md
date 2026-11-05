# 화면 레이아웃 기준선

`tests/web_visual_demo.cjs`가 `labhq/web/index.html?demo=1`을 headless Chrome으로 열어 레이아웃 스냅샷을 이 폴더의 `demo-layout.baseline.json`과 비교합니다. 픽셀은 비교하지 않습니다.

| 항목 | 내용 |
|---|---|
| 화면 | 데스크톱 1440×900, 폰 390×844 |
| 장면 | 시작 · 요청 진행 중 · 결정 대기 · 보고서 검토 (각 장면에서 결정·작업판·메신저 탭과 직원 줄을 펼친 모습) |
| 기록 | 탭·결정 카드·작업판·메신저·직원 카드·사무실의 위치와 크기, 보임 여부, 주요 색, 상태 글자 |
| 허용 오차 | 위치·크기 4px 또는 화면 폭·높이의 1% 중 큰 값, 색 채널 2 |
| 글꼴 | 시험 전용 글꼴로 바꿔 둡니다(글자 폭이 OS와 무관). 글꼴·그림 모양 변화는 잡지 않습니다 |
| 시계 | 페이지 시계를 가상으로 돌려 같은 장면에서 항상 같은 값이 나옵니다 |

Chrome이 없으면 이유를 적고 건너뜁니다. Node 22 미만에는 전역 `WebSocket`이 없어 `tests/support/mini_ws.cjs`의 작은 클라이언트를 씁니다. Chrome 위치는 `LABHQ_TEST_CHROME`으로 지정할 수 있습니다.

## 일부러 바꿀 때

의도한 디자인 변경 뒤 기준선을 새로 만듭니다.

```
node scripts/update_visual_baseline.cjs
```

이전 기준선과 달라진 항목이 출력됩니다. 의도한 변화인지 확인하고 `demo-layout.baseline.json`을 커밋하세요. 실패한 검사를 통과시키려는 용도로는 쓰지 않습니다.

기준선 파일이 실제로 다른 내용으로 다시 쓰여야 종료 코드 0입니다. Chrome이 없어 시험이 건너뛰어졌거나 레이아웃이 이미 기준선과 같아 파일이 그대로면 `Baseline NOT updated`와 이유를 출력하고 1로 끝납니다. 줄바꿈(CRLF/LF)만 다른 것은 바뀐 것으로 치지 않습니다.

## README 데모 움짤

```
node scripts/record_demo_gif.cjs
```

같은 데모를 시작 → 요청 → 직원 작업 → 결정 카드 → 승인 → 보고서 순서로 녹화해 `docs/media/office-25d.gif`(960px, 약 13초)와 정지 화면 `office-25d.png`를 만듭니다. GIF는 표준 라이브러리만 쓰는 `scripts/gif_encode.py`가 조립하며 5MB를 넘으면 실패합니다.
