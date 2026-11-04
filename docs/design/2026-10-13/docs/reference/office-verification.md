# 사무실 표시 검증 · #546

조회용 기록. [전후 화면](../../office-comparison/README.md) · [브라우저 측정값](office-browser.json).

| 검사 | 결과 |
|---|---|
| Node 회귀 | main `7c1686d`: 6 실패·대비 1 통과 → branch 7 통과 |
| 실제 Chrome | main: 전신 그림 0/11로 실패 → branch: PC·폰 통과 |
| 그림 크기 | PC 72px, 폰 64px. 선택안의 32/64/128 proof 중 64px를 하한으로 채택 |
| 폰 첫 사무실 화면 | CSO·분석가·엔지니어·QC가 헤더와 고정 입력창 사이에 표시됨 |
| 대비 | 실제 글자·배경, light/dark 최저 6.50:1. 그림 윤곽·소품 선 3:1 이상 |
| 동작 | OS reduced-motion에서 실행 중 animation 0, 일반 설정에서 동작 존재 |
| 요청 | 페이지가 보낸 외부 이미지·폰트 요청 0, 가로 넘침 0 |

선택안 1의 다섯 동물을 재사용하고 나머지 roster 동물을 같은 전신 형식으로 그렸다. 시설팀은 `agents/core/facilities.yaml`의 햄스터, 파견직은 병아리와 배지다. 알 수 없는 동물은 중립 물음표다.

첫 화면의 대소문자 ID·늦게 도착한 roster·답 대기 fallback은 유지한다. 결정 대기와 보고서 검토 대기에만 강조색을 쓴다. shared `state.js`와 `lab3d`는 변경하지 않았다. 전체 pytest와 CI 결과는 해당 `docs/status/` 항목과 PR에 남긴다.
