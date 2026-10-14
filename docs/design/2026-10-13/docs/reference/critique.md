# labhq 2D 디자인 리뉴얼 사전 검토

**권고:** 승인 판단에 필요한 내용과 버튼을 먼저 정리하고, 폰에서 결정 화면이 곧바로 보이게 한다. 캐릭터와 사무실 분위기는 그 위에 얹는다. 아래는 독립 검토에서 나온 28개 개선 후보다.

## 근거와 한계

- 기준: 로컬 `origin/main`의 `70f5b7225bb0fd4d5c9a74649c69a3a62a14a95f`. remote fetch는 하지 않았다. [source-manifest.json](source-manifest.json)에 코드 snapshot과 SHA-256을 기록했다.
- 지정된 `../shots/shots/INDEX.md`는 **BLOCKED, PNG 0/16**이다. 표의 캡처 이름 뒤 **†는 UNVERIFIED: 예정 파일이며 실제 화면 근거가 없다는 뜻**이다. 파일명은 다시 확인할 화면을 대응시킨 것이다. 캡처를 본 것처럼 판단하지 않았다.
- 코드 근거: **H** = [index.html](source/index.html), **D** = [ui/decide.js](source/ui/decide.js), **T** = [ui/tasks.js](source/ui/tasks.js), **S** = [ui/strip.js](source/ui/strip.js), **N** = [ui/shell.js](source/ui/shell.js). 숫자는 snapshot의 line이다. 다른 검토자의 디자인 의견은 읽지 않았다.
- 로컬 데모와 최신 코드는 H·D·state.js가 다르다. 허용된 새 포트 **54404**에서 지정 web 폴더의 정적 서버를 띄웠으나 브라우저 보안 검토가 접근을 거절했다. 이유는 해당 접근의 사용자 승인 거부다. 우회하지 않았고 서버 종료·포트 닫힘을 확인했다. 실제 instance와 3D는 조작하지 않았다.
- 아래의 문제는 코드에서 확인한 구조·스타일이다. 사용자 영향과 고치는 방향은 디자인 제안이다. 실제 배치·가림·인식 속도·폰 keyboard 동작은 **UNVERIFIED**다. 특히 “폰 입력창이 화면 1/4”이라는 비율은 재측정하지 못했다.

**순서:** P0 = 승인 판단·조작부터 / P1 = 읽기·탐색 / P2 = 표현 정돈. **범위:** S = 한 component의 CSS·문구 / M = 여러 component·interaction / L = 화면 구조·responsive 재배치. 범위는 일정 추정이 아니다.

## 정보 구조·계층

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 01 · P1 | 첫 화면 · `desktop-start.png`†; H:68–69, 355–394 | 사무실과 결정·작업·메신저가 병렬 영역이고, 전체 상황을 요약하는 줄이 없다. | PI가 “내가 처리할 일 / 막힌 일 / 진행 중”을 여러 곳에서 모아야 한다. | 상단에 결정 대기·막힌 요청·진행 요청을 요약하고 각 목록으로 연결한다. 사무실은 팀 상태를 보여주는 역할에 집중한다. | L |
| 02 · P0 | 결정 탭 · `desktop-decision.png`†; H:370–377, 707–714 | 건수는 탭 안 heading에만 있고 탭 버튼에는 없다. title의 수는 approvals만 센다. | 작업판을 보는 동안 로그인 대기·채용 제안 등 다른 처리 항목을 놓치기 쉽다. | 탭에 처리 대기 badge를 붙인다. “필수 결정”과 “제안”은 이름·건수를 나누고 header와 같은 집계 규칙을 쓴다. | M |
| 03 · P1 | whiteboard · `desktop-in-progress.png`†; H:358, 711–714 | 같은 board를 눌러도 결정 유무에 따라 작업판 또는 결정 화면으로 간다. | 익숙한 위치를 눌렀는데 예상과 다른 화면이 열려 탐색을 다시 해야 한다. | board는 작업판으로 고정한다. 별도 “결정 대기 N건” 버튼으로 결정 화면에 간다. | M |

## 타이포그래피

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 04 · P0 | 결정 본문 · `desktop-decision.png`†; H:181–189 | 결정 종류·소속 요청·남은 시간은 12px, 이유는 13px로 작게 묶인다. | 승인 전에 반드시 읽을 정보가 부가 정보처럼 보인다. | 종류·요청·결론을 16–18px, 판단 근거를 15–16px로 올린다. 시간·영향은 별도 줄로 둔다. 12px는 비핵심 metadata에만 쓴다. | M |
| 05 · P1 | 직원 strip · `desktop-start.png`†; H:258–260; S:39–53 | 역할·도구·provenance·gauge label이 11px이고 대부분 한 줄 ellipsis다. | 담당자와 현재 작업을 확인하려고 매번 상세 sheet를 열게 된다. | 기본 카드에는 이름·역할·현재 상태를 14px 이상으로 남긴다. 도구·provenance는 상세로 옮기되 외부 전송 여부는 짧은 badge로 유지한다. | M |
| 06 · P2 | 브랜드·이름·메신저 · `desktop-messenger.png`†; H:23, 54, 75, 108, 233 | Jua가 브랜드뿐 아니라 board 제목·직원 이름·메시지 발신자에도 쓰인다. | 장식용 글씨와 업무용 제목의 경계가 흐려진다. | UI에는 한 Sans family와 일정한 weight 체계를 쓴다. Jua 사용은 브랜드·짧은 사무실 label로 제한한 안과 비교한다. | S |

## 색·상태 의미

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 07 · P0 | 결정 카드 · `desktop-decision.png`†; H:178, 192, 716 | 대부분의 승인과 로그인 대기가 같은 amber 카드다. | 결정 요청과 운영 문제의 중요도·처리 방식이 비슷하게 보인다. | 기본 surface는 중립색으로 두고 종류 label을 붙인다. amber는 주의·기한 임박, red는 실패·중단에 쓴다. 카드 종류와 긴급도는 별도 축으로 표현한다. | M |
| 08 · P1 | 작업판·HPC · `desktop-tasks.png`†, `desktop-hpc.png`†; H:241–251; T:3–4 | 작업판 failed는 amber border, HPC failed는 red다. 실패와 리뷰가 같은 column에 들어간다. | 탭을 옮기면 실패의 의미를 다시 해석해야 한다. | failed의 icon·label·색을 공통화한다. 확인 column 안에서도 “실패 / 리뷰 반영”을 분리한다. 기존 상태값은 그대로 보존한다. | M |
| 09 · P1 | UI 색과 art 색 · `desktop-start.png`†; H:14–42, 481–506 | CSS literal 색과 SVG art 색이 한 파일에 함께 관리되고, 일부 UI 색도 hardcoded다. | theme별 보정과 새 상태 추가 때 같은 의미의 색이 갈라질 수 있다. | surface·text·border·action·status token을 묶고 art palette는 따로 둔다. 캐릭터의 털 색까지 상태색으로 통일하지 않는다. | M |

## 간격·정렬

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 10 · P1 | PC column 비율 · `desktop-decision.png`†; H:69, 168, 178 | 사무실 column 비중이 더 크고 결정 카드 안에는 36px avatar column도 들어간다. | 긴 계획·근거·세 버튼에 쓸 본문 폭이 줄어든다. 실제 줄바꿈 정도는 UNVERIFIED다. | 결정 중심 안에서는 업무 panel 폭을 먼저 확보한다. avatar는 heading 옆에 두고 본문·입력·버튼은 카드 전체 폭을 쓴다. | L |
| 11 · P0 | 로그인 대기 카드 · `desktop-decision.png`†; H:178, 715–728 | `.ap`은 36px + 본문 grid인데 로그인 대기 카드는 body 하나만 넣는다. 첫 36px track에 배치될 구조다. | 로그인 안내·command·재시도 버튼이 좁게 배치될 위험이 있다. 실제 렌더링은 UNVERIFIED다. | avatar 없는 카드 variant를 만들고 body가 전체 column을 차지하게 한다. 긴 command는 독립 block으로 둔다. | S |

## 컴포넌트 일관성(카드·버튼·칩·탭)

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 12 · P2 | 카드·sheet·입력 · `desktop-staff-detail.png`†; H:72, 168, 178, 206, 249, 257–258, 317 | border-radius 선언값이 10종이며 component 크기와 대응하는 공통 규칙이 없다. | 같은 급의 카드도 다른 계열처럼 보이고 새 component마다 기준을 다시 정한다. | UI radius를 small·medium·large 3단계로 정한다. circle과 사무실 그림의 모서리는 art 예외로 둔다. padding도 공통 scale로 맞춘다. | M |
| 13 · P0 | 승인·수정·거절·취소 · `desktop-decision.png`†; H:206–209, 828; D:499–502 | 수정·거절·요청 취소가 기본 `.btn` 표현을 공유한다. 상한 이후 “수정 요청”은 요청 종료를 뜻한다. | 수정하려던 PI가 요청을 끝내는 결과를 충분히 구별하지 못할 수 있다. | action hierarchy를 primary·secondary·destructive로 정한다. 종료 결과가 있는 action에는 결과 문구를 붙이고 해당 경우만 짧은 확인을 둔다. | M |
| 14 · P1 | board chip · `desktop-in-progress.png`†; H:82–87, 688–689 | chip에는 단계 ID와 직원명만 있고 상태는 색·취소선으로 표현한다. instruction은 title에만 있다. | 색을 구별하기 어렵거나 hover가 없는 폰에서는 단계의 일과 상태를 읽기 어렵다. | “s3 · QC · 진행 중”처럼 짧은 작업명·상태 label을 함께 쓴다. 전체 instruction은 click으로 열리는 상세에 둔다. | M |

## 폰 사용성

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 15 · P0 | 폰 첫 화면·결정 이동 · `phone-start.png`†, `phone-decision.png`†; H:355–394, 299–303; N:1–7 | 단일 column에서는 사무실 DOM이 결정 panel보다 먼저 온다. 탭 전환은 panel hidden만 바꾼다. | 하단 결정 탭을 눌러도 결정 내용이 현재 viewport에 오지 않을 가능성이 있다. 실제 scroll 위치는 UNVERIFIED다. | 폰은 선택한 업무 화면을 우선 보여준다. 사무실은 별도 접기·보기로 둔다. 탭 전환 때 heading 위치와 focus를 함께 맞춘다. | L |
| 16 · P0 | 폰 composer · `phone-composer.png`†; H:300–313, 413–429 | fixed tab bar 위에 입력·대상·팀 여부·작업 종류·참고가 wrap하는 fixed composer가 있다. | 승인 본문과 하단 action에 쓸 세로 공간이 줄어든다. keyboard와 실제 가림은 UNVERIFIED다. | 기본 상태는 “새 요청” 한 줄로 접는다. 작성할 때 sheet를 열고 대상·작업 종류는 선택 영역에 둔다. keyboard·safe-area를 포함해 화면을 다시 검증한다. | L |
| 17 · P1 | 폰 작업판 · `phone-tasks.png`†; H:246–250; T:20–26, 70–76 | 4개 column이 각각 최소 145px이고, 접힌 task에는 ID·상태만 보인다. | 좌우로 이동하며 내용까지 펼쳐야 어떤 작업이 막혔는지 알게 된다. | 폰에서는 상태 filter + 세로 task list를 기본으로 쓴다. 접힌 행에도 작업명·담당자·막힌 이유를 짧게 남긴다. PC kanban은 유지한다. | L |

## 결정 카드(승인 흐름)

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 18 · P0 | 결정 목록 정렬 · `desktop-decision.png`†; D:456–467, 515–519 | 목록은 created_at 역순이고 기한·멈춘 단계 수는 카드 안 metadata다. | 곧 만료되는 기존 결정이 새 제안 아래로 밀릴 수 있다. | 기한 임박·요청을 막는 결정·제안을 구분한다. 정렬 기준을 보이게 하고 입력 중인 카드는 재정렬하지 않는다. | M |
| 19 · P0 | CP1·CP2 카드 · `phone-decision.png`†; H:183–189; D:81–89, 305–365, 478–485 | 구조화된 계획·근거와 원장 접기는 이미 있지만, 공통 판단 요약 영역은 없다. 소속 요청도 12px다. | 긴 자료를 읽고 어떤 요청의 무엇을 승인하는지 다시 정리해야 한다. | 위에는 요청명·승인 대상·범위·비용·누락/쟁점, 아래에는 전체 근거를 둔다. CP1은 계획, CP2는 근거 판단임을 명시한다. hash와 원장은 계속 열 수 있게 둔다. | M |
| 20 · P0 | 결정 전송·결과 · `phone-decision.png`†; H:977–1006, 332 | click한 버튼만 disabled로 바뀌고 결과는 일시적인 toast로 안내한다. | 통신 중 다른 action을 누르거나 승인 처리 여부를 다시 확인하려 할 수 있다. | 카드 전체에 “처리 중”을 표시하고 모든 action을 잠근다. 실패는 카드 안에서 재시도, 성공은 결정 이력·해당 요청의 다음 상태로 이어준다. | M |
| 21 · P0 | 질문·수정 메모 · `phone-decision.png`†; D:224–254, 392–393, 486–490; H:983–996 | 메모와 직접 답하기 입력의 안내는 placeholder 중심이다. 미응답·메모 누락은 click 이후 toast로 알린다. | 입력하면 안내가 사라지고 어느 질문을 고쳐야 하는지 찾기 어렵다. | persistent label에 필수·선택을 표시한다. 질문별 미입력 안내와 수정 메모의 목적을 입력 옆에 둔다. 첫 오류 field로 focus를 옮긴다. | M |

## 일러스트·캐릭터

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 22 · P2 | 사무실 motion · `desktop-in-progress.png`†; H:132–165 | 일하는 캐릭터·손·code·rack·대기 board가 반복 animation을 쓴다. reduced-motion 대응은 이미 있다. | 긴 승인 근거를 읽는 동안 주변 motion이 주의를 나눌 가능성이 있다. 실제 방해 정도는 UNVERIFIED다. | 평소 motion은 약하게, 상태 변화 때만 짧게 강조한다. “motion 줄이기” 선택을 제공하고 기존 OS 설정 대응을 유지한다. | M |
| 23 · P2 | avatar·직원 정체성 · `desktop-staff-detail.png`†; H:180, 481–496, 1104 | 역할마다 동물이 다르고 같은 art가 28·36·84px 등 여러 크기에 쓰인다. 작은 크기의 식별성은 UNVERIFIED다. | 역할을 동물로만 기억하면 새 직원·파견직을 구별하기 어렵다. | 이름·역할 label을 항상 함께 쓴다. 24·36px용 단순화 art와 상세용 art를 나눈다. 동물·사람·추상 mascot 시안을 같은 크기로 비교한다. | M |

## 접근성(대비·포커스·터치 크기)

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 24 · P0 | 승인 버튼 contrast · `desktop-decision.png`†; H:17, 30, 38, 208 | CSS의 white text / green background contrast는 light 3.40:1·dark 2.37:1이다. 15px 일반 text의 AA 4.5:1에 못 미친다. | 가장 중요한 action label을 읽기 어렵다. | action background·text token을 theme마다 분리해 4.5:1 이상으로 맞춘다. status green을 버튼 fill에 그대로 쓰지 않는다. | S |
| 25 · P1 | 작은 control · `phone-staff-detail.png`†, `phone-composer.png`†; H:205–211, 281–282, 305, 331 | 기본 버튼 40px, 일부 control 30–38px다. close·숨기기 등도 크기 규칙이 다르다. | 폰에서 재시도·상세 조작 중 옆 control을 누를 여지가 커진다. | 주요 touch target을 44×44 CSS px 이상으로 정하고 작은 icon 주위에도 hit area를 준다. 이는 제품 목표이며 AA 최소치와 구별한다. | M |
| 26 · P1 | 탭·focus 이동 · `phone-messenger.png`†; H:47, 370–375; N:1–18 | focus ring은 있으나 일반 button에 aria-selected만 붙인다. tab·tabpanel 연결과 방향키 처리가 없다. | keyboard·screen reader에서 선택한 화면과 이동 규칙을 파악하기 어렵다. | tablist/tab/tabpanel 관계와 roving tabindex·방향키를 함께 구현한다. 전환 후 선택 상태와 heading 위치를 예측 가능하게 유지한다. | M |

## 한국어 문구

| 번호 | 화면·요소 | 문제 | 왜 문제인가(사용자 영향) | 고치는 방향 | 범위 S·M·L |
|---|---|---|---|---|---|
| 27 · P0 | 결정 field label · `desktop-decision.png`†; D:305–365 | 알려지지 않은 key는 그대로 label이 되고 string detail은 `detail`로 표시된다. facilities_fix의 `reason` 등에도 한국어 mapping이 없다. | 이유·영향보다 데이터 구조를 먼저 해독해야 한다. | `reason`→“이유”, `detail`→“상세 내용”처럼 읽는 목적에 맞는 label을 쓴다. 미등록 key는 “추가 정보” 안에 원문으로 보존한다. 기술 용어는 영어 그대로 둔다. | S |
| 28 · P1 | 상태·시간 문구 · `desktop-hpc.png`†, `phone-in-progress.png`†; S:4–8; D:467 | “HPC 수면”, “내 답 기다림”, “하류 N단계 멈춤”이 업무 상태와 의인화·관계 설명을 섞는다. | 누가 무엇을 기다리고 다음에 무엇을 해야 하는지 추측해야 한다. | “HPC 완료 대기”, “PI 답변 대기”, “뒤의 N단계가 기다립니다”처럼 원인과 다음 행동을 쓴다. CSO·HPC·CP1·CP2 등의 기술 용어는 유지한다. | S |

## 검증 수치와 다음 시안의 비교 축

[code-measurements.json](code-measurements.json): CSS variable 이름 **31개**, border-radius 선언값 **10종**. comment·ID selector를 제외한 서로 다른 색 literal은 **CSS 71개 / CSS+art 114개**다. `#fff`와 `#ffffff`는 별개 literal이며 SVG data URL 내부 색은 포함하지 않는다. 총괄의 “118종”과는 집계 조건이 달라 그대로 확정하지 않는다.

회색 `#5A6B7B`는 white panel에서 **5.49:1**, 승인 카드 배경에서 **4.85:1**이다. 작은 회색 글씨를 전부 contrast 실패로 묶지 않고 size·계층·색을 각각 고친다. 버튼 계산은 CSS 명시값 기준이며 브라우저 전체 accessibility PASS를 뜻하지 않는다. 일반 text의 4.5:1 기준은 [W3C SC 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), AA touch target의 최소 24×24 및 예외는 [W3C SC 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)에 따른다. 이 검토의 44×44는 폰 사용을 위한 별도 목표다.

아래는 후속 시안의 비교 방향이다. 시안 제작·선택은 이번 산출 범위에 포함하지 않는다.

| 비교안 | 페이지 방향 | 캐릭터 방향 |
|---|---|---|
| A · 연구 노트 | 큰 제목·넓은 본문·선명한 section으로 근거를 읽는다. | 단순한 동물 silhouette + 역할 label |
| B · 결정 데스크 | 결정 queue·막힌 요청·현재 작업을 먼저 보여준다. | 작은 사람 avatar + 역할별 소품 |
| C · 조용한 사무실 | 2D 공간을 유지하고 결정 때 업무 panel을 넓힌다. | 종이·도구를 모티프로 한 추상 mascot |

세 안은 같은 요청·결정 내용으로 PC 1440×900과 폰 390×844에서 비교하고 섞을 수 있게 한다. 공통 기준은 요청 식별, 근거 열기, 승인·수정 결과 구별, keyboard를 띄운 상태의 action 접근이다. 외부 font는 Google Fonts 또는 허용된 Pretendard CDN만 쓰고 외부 script 없이 구성한다.

PI의 참고 링크는 [nf-co.re](https://nf-co.re/plasmodiumdrugres/1.0.0/), [Meta](https://research.meta.ai/blog/muse-wonder), [Neurology](https://www.neurology.org/)다. 이번 도구 접근에서는 Meta의 text·heading 구조만 확인했고 nf-co.re는 접근 실패, Neurology는 403이었다. 세 페이지의 **시각적 분위기 비교는 UNVERIFIED**다. 위 A/B/C는 자체 제안이며 특정 페이지의 palette나 layout을 가져온 결과가 아니다.

<!-- codex-dag:done:305cd1e0b889 -->
