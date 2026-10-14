# labhq 2D 시안 2종

| 방향 | 요약 | HTML |
|---|---|---|
| A · 연구 편집실 | Noto Sans KR + Noto Serif KR. 종이빛 바탕과 plum accent, 선화 반신 portrait. 결정과 근거를 읽는 흐름에 집중. | [research-editorial.html](mockups/research-editorial.html) |
| B · 햇빛 연구실 | Pretendard. warm neutral과 저채도 cyan accent, 둥근 전신 캐릭터와 2D 사무실. 팀의 존재감과 승인 흐름을 함께 표현. | [sunlit-lab.html](mockups/sunlit-lab.html) |

두 파일 모두 단일 HTML입니다. 각 페이지에 글꼴·색 token 표, 핵심 변화 5줄, Desktop 메인, 결정 카드 확대, 작업판, 폭 390px Phone을 배치했습니다. 넓은 화면에서는 Desktop과 Phone을 병치하고 Desktop 아래에 확대 카드와 작업판을 놓았습니다.

공통 내용은 진행 중 요청 1건, 직원 6명, HPC 제출·후속 계획 승인 카드 2장, 작업 5단계입니다. 직원 이름·HPC 자원·작업 목록은 최신 `origin/main`의 CD276(B7-H3) 데모를 따릅니다. 두 승인 카드의 동시 배치는 비교용 구성이며 실제 데모의 한 시점을 재현한 화면은 아닙니다.

## 검증과 남은 확인

- **PASS:** HTML 구조, 두 안의 데이터 일치, 외부 script 0개, 허용된 font stylesheet만 사용, `reason`·`detail` 문구 노출 없음.
- **PASS:** 선언한 본문·메타·button·상태 token의 대비 10쌍씩 모두 4.5:1 이상. 전체 rendered 화면의 접근성 검증을 뜻하지 않습니다.
- **UNVERIFIED:** 기존 캡처 폴더에 PNG가 없었습니다. 실제 화면 관찰, font 로딩, 줄바꿈·잘림, 390px rendered 화면과 keyboard 동작은 미검증입니다.
- **PNG 없음:** headless Chrome이 Windows IPC `Access denied`로 종료됐습니다. 같은 방식의 추가 캡처는 생략했습니다.
- 읽기 전용 web 정적 서버는 빈 포트 **64770**, 시안 서버는 **55470**을 사용했습니다. 두 서버의 종료와 포트 닫힘을 확인했습니다. 금지 포트·labhq instance·3D는 다루지 않았습니다. 저장한 파일은 모두 이 노드 안에 있습니다.

근거: [레퍼런스 방향](../refs/out.md), [캡처 상태](../shots/shots/INDEX.md), [구조·대비 검증](validation.json), [서버·캡처 기록](capture-audit.json), [Chrome 오류](research-editorial.png.chrome.log).

원본 확인: `origin/main` = `70f5b7225bb0fd4d5c9a74649c69a3a62a14a95f`; `git show origin/main:labhq/web/index.html`·`state.js` 읽기만 수행.

HTML을 직접 열어 비교하면 됩니다. 조합 후보는 **A의 읽기 흐름 + B의 캐릭터**입니다. 선호 평가는 PI 선택 전까지 UNVERIFIED입니다.
<!-- codex-dag:done:19424766e9a2 -->
