# labhq 2D 시안 C · D

| 방향 | 구성과 선택 기준 | HTML |
|---|---|---|
| **C. 연구 관제실** | 차가운 gray·violet, 6개 zone, 기하학 avatar. 담당과 승인 대기를 빠르게 훑는 안. | [control-room.html](mockups/control-room.html) |
| **D. 공동 연구 노트** | stone·muted plum, 작은 책상 vignette, 종이 bust, 단계 기록. 함께 연구하는 분위기를 살리는 안. | [shared-notebook.html](mockups/shared-notebook.html) |

- 두 파일 모두 자기완결형 HTML. 파일을 직접 열면 된다. 외부 script와 image는 없으며 Google Fonts만 선택적으로 불러온다. 연결이 없어도 system font로 표시한다.
- 상단에 콘셉트·글꼴/색 token 표·핵심 변화 5개. 아래에 데스크톱, 결정 카드 2종 확대, 작업판, **390 × 960px 폰**을 배치했다.
- 비교 보드 전체 폭은 1800px. 좁은 창에서는 가로로 스크롤한다. 폰은 보드 안의 독립된 390px 화면이다.
- A의 편집형 브리핑과 달리 C는 zone과 상태에 집중한다. D는 작은 공동 책상과 기록의 연속성을 강조해 B의 큰 cutaway 사무실과 구별했다.
- 조합안: C의 결정 패널·작업판에 D의 캐릭터를 적용한다.

## 데이터와 근거

- 방향: [레퍼런스 분석](../refs/out.md)의 셋째·넷째 안. 해당 문서의 원본 사이트 시각 관찰은 일부 **UNVERIFIED**이므로 관찰 사실로 확대하지 않았다.
- 코드: `origin/main` **70f5b7225bb0fd4d5c9a74649c69a3a62a14a95f**, `labhq/web/index.html`의 `Demo`와 `state.js`의 승인/상태 표기.
- 공통 데이터: 폐선암 scRNA-seq의 CD276 고발현 세포유형 탐색 요청 1개, 데모 roster에서 고른 직원 6명, 작업 단계 5개. HPC 카드는 `fetch_atlas`, 4 cores, 32G, 6시간, 최대 24 core-h, 337k cells를 사용했다.
- **비교용 합성 snapshot**이다. 계획 승인 카드는 시안용으로 추가했으며 단계 상태·직원 상태·“방금” 표기도 비교용이다. 데모의 특정 순간을 그대로 재현한 화면이 아니다. 두 HTML의 내용은 같다.

## 확인 결과와 한계

- Static checks 통과: 화면 구역 4개, 직원 6명, 확대 카드 2개, 작업 단계 5개, 핵심 변화 5개, 영어 field name 미노출, 외부 script 없음.
- 본문·보조 글자·승인 버튼·상태의 지정 token 조합 대비: C 최소 **5.81:1**, D 최소 **5.56:1**. 전체 accessibility 검증을 뜻하지 않는다. [검사 결과](evidence/verification.json)
- **PNG 미생성 / rendered layout UNVERIFIED.** 기존 [캡처 목록](../shots/shots/INDEX.md)도 PNG 0개였다. 이번 headless Chrome은 `spawn EPERM`, 연결된 browser 조회는 timeout으로 끝났다. 글꼴 로딩·실제 줄바꿈·overflow·폰 실기기 화면은 확인하지 못했다.
- 원본 데모 정적 서버는 빈 포트 **55586**에서 HTTP 200 확인 후 종료했다. 포트가 닫힌 것도 확인했다. 금지 포트 및 labhq instance는 조작하지 않았다. 원본 web 폴더 수정 없음. [실행 기록](evidence/original-capture.json)

산출물은 모두 `mock-b/` 안에 있다. `build_mockups.py`는 HTML 생성용, `verify.py`는 static check용, `capture.cjs`는 브라우저 실행이 허용된 환경의 캡처용이다.
<!-- codex-dag:done:ee4d0bf9f7b3 -->
