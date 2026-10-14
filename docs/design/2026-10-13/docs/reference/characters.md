# labhq 직원 캐릭터 시안

[비교 페이지 열기](characters/index.html) · [전체 PNG](characters/characters-proof.png) · [크기 비교 PNG](characters/size-proof.png)

**현재 스타일 + 새 시안 4종**을 만들었다. 각 줄은 CSO=부엉이, 분석가=라쿤, QC=고슴도치, 엔지니어=문어, 문헌 조사=여우로 같다. 거북이는 과학 리뷰어라 이번 비교에서 제외했다.

| 안 | 특징 | 비교할 용도 |
|---|---|---|
| 00 지금의 연구실 | 원본 SVG·책상·light 색 유지. motion 정지. | 현재 모습 기준 |
| 01 익숙함을 또렷하게 | 큰 얼굴과 직무 소품을 넣은 작은 전신. | 기존 사무실을 다듬는 안 |
| 02 한눈에 알아보는 팀 | 굵은 선과 큰 실루엣. 얼굴 중심의 flat. | 폰 직원 목록의 우선 비교안 |
| 03 곁에 있는 동료 | 둥근 전신과 gradient로 만든 입체감. | 따뜻한 사무실 일러스트 |
| 04 연구 노트의 초상 | 단색 ink·얇은 선·종이빛 배경. | reference 문서의 ‘연구 편집실’ 무드 |

**조합 제안:** 직원 목록은 02, 넓은 사무실은 01 또는 03. 차분한 화면을 원하면 04를 비교한다. 이는 디자인 판단이며 PI 선호·식별성 검증 결과는 아니다.

- 각 스타일 아래 일하는 중·대기·결정 기다림·완료를 한 줄로 표시했다. 대기는 `idle`이며 `queued`·HPC 대기와 구분한다.
- 신규 상태는 blue/gray/amber/green에 기호·한국어 문구를 함께 쓴다. 00은 원본 상태 장식을 보존했다.
- HTML 한 파일에 CSS·JS·SVG를 넣었다. 외부 font·script 없음. 32/64/128px 전환, 흑백 보기, 여러 후보 선택을 제공한다. 폰은 직원·상태 줄을 좌우로 이동하도록 작성했다.
- 검사 통과: 스타일 5줄, 직원 25명, 상태 20개, inline SVG 61개, XML parsing, ID 중복 없음, paint 참조 유효. PNG 2개를 직접 확인했다.
- PNG는 **정적 proof sheet**다. HTML screenshot이 아니며 03의 gradient는 근사 rendering이다.
- **UNVERIFIED:** PC·폰 HTML rendering과 control 동작. demo browser 접근은 자동 승인 검토에서 권한 거절로 차단됐다. 우회하지 않았다.
- 원본 기준: 로컬 `origin/main`의 `70f5b7225bb0`. 제공된 reference는 화면 검증 전 제안이며 shots 폴더에는 PNG가 없었다.
- 원본 web 폴더 쓰기·instance 조작·3D 변경 없음. 임시 정적 서버 port 62735는 종료를 확인했다. 금지 포트는 사용하지 않았다.

구현 근거와 전체 동물 목록: [조회용 reference](docs/reference/README.md). 다음 단계는 PI 후보 선택 후 PC·폰 browser 확인이다.
<!-- codex-dag:done:c857f984ba5d -->
