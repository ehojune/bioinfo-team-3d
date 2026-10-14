# labhq 2D 디자인 참고 조사

2026-10-13 · **화면 관찰 미완료 / 적용 방향 제안**

추천 출발점은 **A의 읽기 구조 + B의 사무실 표현 + C의 폰 승인 흐름**이다. 아래 네 방향은 색만 바꾸는 안이 아니라 정보 배치와 캐릭터 비중을 달리한 비교안이다.

세 URL을 웹 조회로 열고 브라우저 화면 접근도 시도했다. 브라우저는 세 도메인 모두 사용자 권한 거절로 차단했다. 우회하지 않았으며 PNG는 생성하지 못했다. Meta 공개 본문만 확인했고 nf-core의 화면·본문은 확보하지 못했다. Neurology 웹 조회는 403이었다. **UNVERIFIED는 미확인 사실 또는 검증 전 가설**을 뜻한다. 아래 제안 수치는 원본 측정값이 아니다.

## 1. 페이지별 관찰 표

### nf-core — plasmodiumdrugres 1.0.0

출처: [지정 페이지](https://nf-co.re/plasmodiumdrugres/1.0.0/). 브라우저 목록에서 `plasmodiumdrugres: Introduction`이라는 tab title만 확인했다.

| 항목 | 관찰과 확인 한계 |
|---|---|
| 글자 계층 | **UNVERIFIED** — 제목/본문 크기 비·weight·font family 측정 불가. |
| 여백·밀도 | **UNVERIFIED** — content width·column 수·행간 확인 불가. |
| 색 역할·개수 | **UNVERIFIED** — background·surface·accent를 구분할 화면 없음. |
| 카드·섹션 | **UNVERIFIED** — Introduction이라는 페이지 이름 외 navigation·card 구조 미확인. |
| 이미지·일러스트 | **UNVERIFIED** — pipeline diagram의 유무나 배치도 추정하지 않음. |
| 상태·meta 정보 | URL의 `1.0.0`은 확인. 화면의 version selector·release badge는 **UNVERIFIED**. |
| 신뢰 장치 | **UNVERIFIED** — citation·maintainer·실행 예제의 노출 여부 미확인. |

**번역 후보:** version이 명확한 URL처럼 labhq도 결과에 실행·수정 시점을 붙인다. 이는 URL에서 얻은 아이디어이며 해당 페이지의 시각 분석 결론은 아니다.

### Meta Research — Muse Wonder

출처: [지정 페이지 공개 본문](https://research.meta.ai/blog/muse-wonder). 아래 확인 사항은 텍스트 추출 결과에 한정한다.

| 항목 | 관찰과 확인 한계 |
|---|---|
| 글자 계층 | 제목 아래 H2·H3로 내용을 나눈다. 실제 크기 비·weight·font family는 **UNVERIFIED**. |
| 여백·밀도 | **UNVERIFIED** — 문단 구조는 확인했지만 화면의 여백과 읽기 폭은 미측정. |
| 색 역할·개수 | **UNVERIFIED** — background·surface·accent의 값과 개수 미확인. |
| 카드·섹션 | Contents와 네 주제 section이 있다. demo 선택 button·select가 추출된다. card 형태·배치는 **UNVERIFIED**. |
| 이미지·일러스트 | 화면 녹화 demo와 video control·caption 항목이 있다. 실제 장면·재생 여부·화면 점유율은 **UNVERIFIED**. |
| 상태·meta 정보 | 재생 위치·caption control·demo loading 문구가 추출된다. sticky 배치나 시각적 강조는 **UNVERIFIED**. |
| 신뢰 장치 | 평가 report로 연결하고 기능 설명에 demo를 붙인다. 근거에 접근하는 경로가 있다는 점은 확인했으며 주장 자체의 타당성은 평가하지 않았다. |

**번역 후보:** labhq에서도 요약 뒤에 실행 결과와 근거를 연다. 사무실 animation은 진행의 보조 표현으로 쓰고 결정의 근거는 card 안에서 읽게 한다.

### Neurology

출처: [지정 홈페이지](https://www.neurology.org/). 웹 조회 403, 브라우저 권한 거절로 본문과 화면을 확보하지 못했다.

| 항목 | 관찰과 확인 한계 |
|---|---|
| 글자 계층 | **UNVERIFIED** — 학술지라는 이유로 serif 사용을 단정하지 않음. |
| 여백·밀도 | **UNVERIFIED** — 기사 목록의 column 수·화면 밀도 미확인. |
| 색 역할·개수 | **UNVERIFIED** — 브랜드 색이나 accent 개수 미확인. |
| 카드·섹션 | **UNVERIFIED** — featured article·issue·목록 배치 미확인. |
| 이미지·일러스트 | **UNVERIFIED** — cover·article image 사용 여부 미확인. |
| 상태·meta 정보 | **UNVERIFIED** — 날짜·article type·access label의 표시 방식 미확인. |
| 신뢰 장치 | **UNVERIFIED** — editorial 정보·DOI·소속의 홈페이지 노출 여부 미확인. |

**번역 후보:** labhq 결과에 작성 agent·시점·근거를 일정한 위치에 둔다. 이는 연구용 UI 제안이며 Neurology에서 관찰했다고 주장하지 않는다.

## 2. 세 페이지 공통 원칙 — 검증 전 후보 8개

**UNVERIFIED:** 세 화면을 비교하지 못했으므로 실제 공통점으로 확정할 수 없다. 아래는 다음 시안에서 시험할 디자인 원칙이다. Meta 본문에서 확인한 구조는 근거 열에 따로 표시했다.

| 원칙 후보 | labhq에서 시험할 방식 | 근거 수준 |
|---|---|---|
| 1. 중요도는 크기·weight·위치로 먼저 구분 | 결정 제목 → 요청 → 근거 → meta 순서 | 디자인 제안 |
| 2. 넓은 여백과 조밀한 목록을 역할별로 배분 | 승인 card에는 여유, 완료 기록은 짧은 row | 디자인 제안 |
| 3. color에는 고정된 의미 부여 | brand accent와 status token 분리 | 디자인 제안 |
| 4. 긴 내용은 section과 펼침으로 나눔 | 요약에서 실행 근거·log로 이동 | Meta의 Contents·section 확인 |
| 5. 시각 자료에는 설명할 대상이 있어야 함 | agent 위치·작업 관계를 사무실에 표현 | Meta의 설명용 demo 확인 |
| 6. meta 정보는 같은 위치에 반복 | 담당·갱신 시각·실행 ID를 card 하단에 정렬 | 디자인 제안 |
| 7. 신뢰는 근거까지의 거리에서 생김 | 결과·artifact·실행 기록을 한 번에 열기 | Meta의 report 연결 확인 |
| 8. 작은 화면에서는 우선순위를 다시 배치 | 결정 먼저, 사무실은 접힌 요약으로 제공 | 디자인 제안; 세 페이지 mobile 미확인 |

## 3. labhq 2D로 옮기는 방법

총괄이 제시한 색 118종·CSS 변수 31개·모서리 10종과 폰 입력창 문제는 **사용자 제공 진단**이다. 이번 노드에서 재계수하거나 demo로 검증하지 않았다.

| 대상 | 번역안 | 그대로 가져오지 않을 것 |
|---|---|---|
| Typography | 본문 16px/line-height 1.55~1.65, 결정 제목 20~24px/600, page title 28~32px/700. 본문 대비 약 1.25~2배. meta 14px부터 비교 | 홍보 페이지의 대형 hero나 작은 회색 caption을 업무 UI 전반에 적용 |
| 여백 | spacing 4·8·12·16·24·32px, card padding 16~24px부터 검토 | 모든 구역을 넓히거나 모든 정보를 card로 감싸기 |
| Color | background 1, surface 2, text 2, border 1, brand accent 1을 기본 역할로 정의. status는 별도 token | 특정 사이트의 브랜드 색·gradient·색 배합 |
| 결정 상태 | 승인 대기=amber+시계+문구, 진행=blue+진행 표시, 완료=green+check, 실패=red+오류 문구. 승인 button은 brand accent | 성공색과 승인 대기색 혼용; 색만으로 상태 구분 |
| Card | “결정할 일 → 권고안 → 근거 요약 → 영향 → 승인/보류” 순서. 긴 log는 펼침 | raw field name `reason`·`detail` 노출; 설명 없는 badge 증가 |
| 신뢰 | 담당 agent·최근 갱신·artifact 링크·결정 이력. stale 상태는 명시 | 장식용 수치, 의미가 불분명한 신뢰 점수, 브랜드 로고 차용 |
| 직원 목록 | 이름 16px, 역할·작업 14px를 출발점으로 설정. 인원이 늘면 list 또는 drawer | 축소한 명함을 한 줄에 계속 추가 |
| Desktop | 결정 목록을 주 읽기 영역에 두고 사무실에는 팀 상태 요약 배치 | 사무실을 보기 위해 결정 내용을 좁은 sidebar에 몰기 |
| Mobile | 결정 → 팀 상태 → 기록 순서. 입력창은 한 줄에서 확장하고 keyboard가 열리면 작업 공간 확보 | desktop 전체를 축소; 상시 다중 행 입력창; 사무실 강제 노출 |
| 일러스트 | 한국어 이름·상태를 그림 밖에 배치. motion은 줄이기 설정을 존중 | 그림 속 작은 글씨, 상태를 animation에만 의존, 3D 확장 |

위 숫자는 시안 시작값이다. font·색·contrast·터치 영역은 실제 화면에서 검증해야 하며 현재 PASS 판정은 없다.

## 4. 시안 방향 4개

모두 독립 제안이다. 원본 사이트의 색·font·layout을 재현하지 않는다. 시안 비교에는 같은 한국어 결정 card와 같은 agent 구성을 사용한다.

| 방향·한 줄 콘셉트 | 무드 | Font·색 성격 | 사무실·캐릭터 | PC / 폰의 중심 |
|---|---|---|---|---|
| **A. 연구 편집실** — 오늘 판단할 일을 한 장의 연구 브리핑처럼 읽는다 | 차분함·명료함 | Noto Sans KR 본문 + Noto Serif KR section 제목만. 종이빛 background·ink text·절제한 자주 accent | 얇은 선의 평면 사무실, 단색 반신 portrait. 이름과 역할은 UI text로 표시 | PC는 결정 목록과 근거 panel / 폰은 한 column 결정 목록 |
| **B. 햇빛 연구실** — 팀이 함께 일하는 기분을 살린다 | 따뜻함·친근함 | Pretendard 400/600/700. 밝은 warm neutral·짙은 text·저채도 cyan accent | 2D cutaway 사무실, 둥근 형태의 작은 전신 캐릭터. 가구와 인물은 저채도, 상태 표시만 선명하게 | PC는 사무실과 결정 영역 병치 / 폰은 작은 팀 strip과 결정 card |
| **C. 연구 관제실** — 대기·진행·완료를 빠르게 훑고 승인한다 | 또렷함·집중 | Noto Sans KR + 실행 ID에만 monospace. 차가운 밝은 gray·graphite·violet accent | 단순한 평면 zone map과 기하학 avatar. 이동 animation을 줄이고 작업 연결을 표현 | PC는 queue와 선택한 결정 / 폰은 결정 상세와 하단 action bar |
| **D. 공동 연구 노트** — 사람과 근거가 함께 남는 연구실 | 소박함·학술적 온기 | Noto Sans KR 본문 + Noto Serif KR 큰 제목. 옅은 stone·charcoal·muted plum accent | 작은 책상 vignette와 종이 오려낸 듯한 bust 캐릭터. 일러스트는 section 시작에 제한 | PC는 현재 결정과 최근 기록 / 폰은 결정 위, timeline 아래 |

- **A의 trade-off:** 읽기에는 유리한 구성이나 사무실의 존재감은 약해질 수 있어 B의 인물 표현과 함께 비교한다.
- **B의 trade-off:** 친근함을 노리는 안이다. 장식이 늘 때 승인 영역이 밀리지 않는지 확인한다.
- **C의 trade-off:** 업무 흐름을 강조하는 안이다. 너무 건조해 보이면 D의 portrait를 조합한다.
- **D의 trade-off:** 기록의 연속성을 강조하는 안이다. 현재 결정이 timeline 아래로 밀리지 않게 고정한다.

### 다음 시안의 비교 기준

| 고정 조건 | 확인할 질문 |
|---|---|
| 같은 대기 결정·완료 작업·실패 작업 표시 | 상태를 색 없이도 구별하는가? |
| 1440px PC와 390px 폰에서 비교, 360px도 점검 | 요청·근거·담당·승인 action을 자연스럽게 찾는가? |
| keyboard가 열린 폰 화면 포함 | 입력창이 결정 내용을 가리지 않는가? |
| 캐릭터 네 계열을 같은 크기로 나란히 표시 | 선화 portrait·둥근 전신·기하학 avatar·종이 bust 중 선호는 무엇인가? |
| 확대·긴 한국어 이름·긴 근거 포함 | text 잘림과 작은 회색 글씨가 다시 생기지 않는가? |

구현 시 각 시안은 단일 HTML에 CSS·JS·SVG를 넣는다. 외부 script는 쓰지 않고 font는 Google Fonts 또는 허용된 Pretendard jsDelivr만 사용한다. font 미수신 때의 system fallback도 둔다. 이 노드에서는 HTML 시안 제작까지 진행하지 않았다.

**남은 확인:** 세 페이지의 PC·mobile screenshot, computed font/size/weight, 주요 색 역할과 개수, section 간격. 확인 후 1절의 UNVERIFIED를 교체하고 2절에서 실제 공통점만 남겨야 한다. 현재 파일은 완전한 시각 분석 보고서가 아니다.

**작업 범위:** 산출물은 이 노드의 `out.md`만 작성했다. labhq 파일 수정·instance 조작·정적 서버 실행·금지 포트 사용은 없었다.
<!-- codex-dag:done:4c6f86b8650b -->
