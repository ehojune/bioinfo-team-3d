# 실제 답 15개 검사 보정 (2026-10-01, PR #77)

case의 요청과 고정 참고 자료를 직접 대조한 판정이다. 답 안의 자기평가·기존 score는 근거로 쓰지 않았다.
**맞음 13 · 부분 2 · 틀림 0**, 검사 기대값은 **PASS 15**이다. Sonnet 추가 5개는 맞음 4·부분 1이다.

- 맞음 → PASS: 요청한 수치·항목·한계를 충족한다. 설계 설정값을 관측값으로 취급하지 않는다.
- 틀림 → FAIL: 필수 수치 오류, 필수 항목 누락, 같은 항목의 모순된 전체값 주장.
- 부분 → 이 표에서 개별 결정한다. KRAS sol은 필수 ID와 IC50 비교 한계를 충족하므로 PASS지만, 추가 동일성 주장의 출처 범위는 감점한다. PASS는 과학적 주장 전체의 보증이 아니다.
- Plastome Sonnet도 필수 입력·구조·경계 검증 계획·설계 범위를 충족해 PASS다. snapshot 밖 문헌값과 junction 해석은 별도 감점한다.

## 직접 판정

답 링크의 L은 fixture 줄 번호다. 참고 표의 헤더는 행 수에서 제외했다.

| case | arm | 판정 | 검사 | 참고 자료와 대조한 근거·오류·누락 |
|---|---|---|---|---|
| inco-kras-g12c | sol-ultra | 부분 | PASS | [답 L11–17](../tests/fixtures/bench_real/inco-kras-g12c/sol-ultra.md#L11)의 `6OIM`, `MOV`, 1.65 Å, ChEMBL 4개는 [참고 L3–6](../labhq/bench_data/references/kras-g12c.md#L3)과 일치. L35–44는 assay 차이와 미조회 한계를 명시한다. 다만 L19의 “교차 연결”과 L30의 “내부 매핑 불일치 0건”은 snapshot에 없는 AMG 510–sotorasib 동일성까지 내부 검증한 듯 표현한다. 필수 항목 누락·틀린 고정 수치는 없다. |
| inco-kras-g12c | astra-ultra | 맞음 | PASS | [답 L9–14](../tests/fixtures/bench_real/inco-kras-g12c/astra-ultra.md#L9)의 ID·해상도는 [참고 L3–5](../labhq/bench_data/references/kras-g12c.md#L3)와 일치. L22는 물질 동일성 연결이 자료에 없다고 구분한다. L25 “한 순위표에 합쳐서는 안 된다”는 참고 L6의 비교 제한을 충족한다. |
| plastome-structure | sol-ultra | 맞음 | PASS | [답 L24–28](../tests/fixtures/bench_real/plastome-structure/sol-ultra.md#L24)의 종·accession·길이 5행이 [참고 L2–6](../labhq/bench_data/references/plastome.tsv#L2)과 모두 일치. L36–44의 5레코드·4종·평균 154432.6 bp·차이 245 bp를 재계산했다. L72–104는 사분구조·IR 검출·좌표 검산, L115–125는 예상 SSC 17400–18100 bp·IR 25900–26600 bp를 제시한다. L198은 문헌값을 실제 결과와 구분한다. |
| plastome-structure | astra-ultra | 맞음 | PASS | [답 L9–15](../tests/fixtures/bench_real/plastome-structure/astra-ultra.md#L9)의 입력 5행, 길이 합 772163 bp·범위 폭 1440 bp·평균 154432.6 bp는 [참고 L2–6](../labhq/bench_data/references/plastome.tsv#L2)에서 재계산된다. L19–43은 사분구조와 SSC 17–19 kb·IR 한 사본 26–27 kb를 설계 범위로 명시한다. L47–91은 경계 검증과 미실행 한계를 충족한다. |
| geo-gastric-summary | sol-ultra | 맞음 | PASS | [답 L12–24](../tests/fixtures/bench_real/geo-gastric-summary/sol-ultra.md#L12)의 tumor 10·normal 10·EMT NES 양수·9606.ENSP 형식은 [참고 L3–6](../labhq/bench_data/references/geo-gastric.md#L3)과 일치. L133–134의 감소 6개·증가 7개도 전부 일치. L40의 pair당 1+1은 전체 10+10과 모순이 아니다. L284–292에 문헌 요약, L298–479에 미확보 값을 null로 둔 manifest가 있다. 분석 수치를 결과로 지어내지 않았다(L3, L481–483). |
| geo-gastric-summary | astra-ultra | 맞음 | PASS | [답 L5–14](../tests/fixtures/bench_real/geo-gastric-summary/astra-ultra.md#L5)의 10+10·13개 유전자 방향·EMT·STRING은 [참고 L3–6](../labhq/bench_data/references/geo-gastric.md#L3)과 일치. L31의 rank 11·잔차 df 9, L49의 2^10=1024는 명시한 설계 가정 아래 계산된다. L67–90의 문헌 요약·manifest와 L96–104의 미산출 구분이 요청을 충족한다. |
| public-protein-qc | sol-ultra | 맞음 | PASS | [답 L13–20](../tests/fixtures/bench_real/public-protein-qc/sol-ultra.md#L13)의 4행·고유 3·P01116 중복·양수 4/4·189–1863 aa는 [참고 L2–5](../labhq/bench_data/references/protein-manifest.tsv#L2)와 일치. “초과 중복 행 수 1”은 전체 행 수와 다른 항목이다. L26–29도 원표와 같고 L7·35는 다운로드 미실행을 명시한다. |
| public-protein-qc | astra-ultra | 맞음 | PASS | [답 L7–13](../tests/fixtures/bench_real/public-protein-qc/astra-ultra.md#L7)의 4행·고유 3·중복 1종·초과 중복 1행·양수 4/4는 [참고 L2–5](../labhq/bench_data/references/protein-manifest.tsv#L2)와 일치. L19–22에 4행을 보존했고 L24는 선언값 검사와 실제 서열 검증을 구분한다. |
| public-penguins-qc | sol-ultra | 맞음 | PASS | [답 L11–20](../tests/fixtures/bench_real/public-penguins-qc/sol-ultra.md#L11)의 5행·2종·Adelie 3/Gentoo 2·체중 결측 1(20%)·flipper 181–230 mm는 [참고 L2–6](../labhq/bench_data/references/penguins.tsv#L2)에서 재계산된다. L22의 결측 행도 일치. L26은 원본 전체로 일반화하지 않는다. |
| public-penguins-qc | astra-ultra | 맞음 | PASS | [답 L7–14](../tests/fixtures/bench_real/public-penguins-qc/astra-ultra.md#L7)의 전체 5행·2종·체중 결측 1/5·flipper 181–230 mm와 폭 49 mm는 [참고 L2–6](../labhq/bench_data/references/penguins.tsv#L2)와 일치. flipper 결측 0은 체중 결측 1과 모순이 아니다. L16–18은 대치하지 않았고 대표성 한계를 밝혔다. |
| inco-kras-g12c | sonnet-max | 맞음 | PASS | [답 L18–22](../tests/fixtures/bench_real/inco-kras-g12c/sonnet-max.md#L18)의 6OIM·MOV·1.65 Å·ChEMBL 4개가 [참고 L3–5](../labhq/bench_data/references/kras-g12c.md#L3)와 같다. L36·48은 외부 확인 실패와 회상을 구분하고 L54–60은 참고 L6의 assay 비교 제한을 지킨다. 추가 동일성 연결을 조회 결과로 주장하지 않는다. |
| plastome-structure | sonnet-max | 부분 | PASS | [답 L23–27](../tests/fixtures/bench_real/plastome-structure/sonnet-max.md#L23)의 5행·종·accession·길이는 [참고 L2–6](../labhq/bench_data/references/plastome.tsv#L2)과 같다. L14·53–73은 사분구조·IR 탐지·합계 검산·junction 검증을 설계한다. L124–127의 SSC 17400–18100 bp·IR 한 사본 25700–26700 bp는 허용 범위 안이며 L119·145는 미검증 예상값으로 표시한다. 다만 L103–115의 문헌 구조값은 snapshot 밖인데 “확인값”으로 부르고 L64의 JSA를 ndhF–ycf1 쌍으로 단정한다. 이 추가 해석은 고정 자료로 확인되지 않아 부분 판정한다(L148의 문헌 미조회 한계 포함). |
| geo-gastric-summary | sonnet-max | 맞음 | PASS | [답 L31–36](../tests/fixtures/bench_real/geo-gastric-summary/sonnet-max.md#L31)의 tumor/normal 각 n=10·13개 유전자 방향·EMT 양수·9606.ENSP는 [참고 L3–6](../labhq/bench_data/references/geo-gastric.md#L3)과 같다. L46–105의 manifest·L111–138의 paired/GSEA/STRING 계획·L142–160의 문헌 배경과 미산출 구분이 요청을 충족한다. full Hallmark 이름은 EMT 약칭과 같은 대상을 뜻한다. |
| public-protein-qc | sonnet-max | 맞음 | PASS | [답 L14–17](../tests/fixtures/bench_real/public-protein-qc/sonnet-max.md#L14)의 입력과 L23–28의 4행·unique 3·중복 1종·양수 4/4가 [참고 L2–5](../labhq/bench_data/references/protein-manifest.tsv#L2)와 같다. L40의 P01116 중복과 BRCA1 1863 aa도 일치한다. L54·56은 선언 길이의 전수 양수와 실제 서열 미검증을 구분한다. L55의 중복 제거는 권고이며 실행하지 않았다. |
| public-penguins-qc | sonnet-max | 맞음 | PASS | [답 L27–38](../tests/fixtures/bench_real/public-penguins-qc/sonnet-max.md#L27)의 5행·species 종류 수 2·체중 결측 1/5=20%, L48–51·68의 flipper 181–230 mm는 [참고 L2–6](../labhq/bench_data/references/penguins.tsv#L2)에서 재계산된다. L59–69의 원본 전체 344행·3종은 발췌본 집계와 다른 문맥이며 대표성 한계를 명시한다. 결측 대치·삭제는 하지 않았다. |

## 검사 기준과 한계

- 수치는 Markdown 강조·inline code를 걷어낸 뒤 항목별 문맥에서 추출한다. 전체 행 수와 species별·중복 행 수, 체중 결측과 다른 열의 결측을 분리한다.
- 표의 숫자 셀에는 헤더의 항목명·명시된 bp/kb 단위를 붙여 검사한다. IR 두 사본 합계는 제외한다. `species 종류 수`, `n=`, full Hallmark 이름, `전수 양수`를 허용하며 accession 뒤 연도·검증 단계 번호는 길이로 읽지 않는다. 원본 전체의 괄호 설명은 발췌본 species 집계에서 제외한다.
- 같은 문맥의 모든 값이 맞아야 한다. 전체값 하나를 맞힌 뒤 다른 전체값을 덧붙이면 FAIL이다. 결측 개수·분모·백분율도 함께 대조한다. GEO의 `각 pair` 문장은 `detail`로 소비하되 이것만 있으면 PASS하지 않는다.
- Plastome 참고 표에는 IR·SSC 정답 범위가 없다. mock의 25–27/17–18 kb를 유일한 정답으로 쓰던 검사를 **IR 한 사본 25–27 kb, SSC 17–19 kb 안의 순서 있는 설계 범위**로 보정했다. bp/kb 환산, 역전·음수·범위 이탈을 검사하며 IR 두 사본 합계는 제외한다. 이는 이 bench의 설계 허용 범위이지 과 전체의 생물학적 한계가 아니다.
- 예상 규모의 외부 대조: [Sato 1999, 초록](https://pubmed.ncbi.nlm.nih.gov/10574454/)의 SSC 17780·IR 26264 bp와 [Zhu 2021, Abstract](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0248556)의 SSC 17786·IR 26208 bp를 직접 확인했다. sol이 인용한 IR 26035–26459 bp도 [2022 비교 연구, IR junction characteristics](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0263310)와 일치한다.
- 정규식은 임의의 문장 의미나 실행 여부를 증명하지 않는다. 외부 문헌·분석 방법 전체의 심사는 이 검사 범위 밖이다. 필수 항목 삭제, 실제 답의 수치 변조, 정답 뒤 모순 추가, 기존 변형·오답을 회귀 테스트한다.

## Fixture 출처·공개 점검

두 arm은 요청한 시간대(2026-09-30 19:2x–20:05 UTC)의 case별 최신 run에서 `answer.md`를 복사했다. 10개 모두 공개 snapshot·설계·공개 문헌만 포함한다. 로컬 절대경로·실행 계정·토큰은 발견되지 않아 가림 없이 원본 bytes를 보존했다. 원본과 SHA-256 10/10 일치를 확인했다. 실행 로그와 run/score 파일은 복사하지 않았다.

| case | run (UTC) |
|---|---|
| inco-kras-g12c | 20260930T193024.259750Z |
| plastome-structure | 20260930T193317.938356Z |
| geo-gastric-summary | 20260930T194618.740824Z |
| public-protein-qc | 20260930T200045.272283Z |
| public-penguins-qc | 20260930T200328.053468Z |

Sonnet은 요청 시간대의 각 case에서 `sonnet-max/answer.md`가 있는 최신 run을 읽었다. 공개 snapshot·설계 보고서이며 절대경로·계정·토큰 없이 원본 bytes를 보존했다(SHA-256 5/5 일치). 권한 실패 문장도 수정하지 않았다. 결과 폴더에는 쓰지 않았다.

| case | Sonnet run (UTC) |
|---|---|
| inco-kras-g12c | 20260930T201941.223731Z |
| plastome-structure | 20260930T202811.185415Z |
| geo-gastric-summary | 20260930T202400.040120Z |
| public-protein-qc | 20260930T201743.514311Z |
| public-penguins-qc | 20260930T201610.773481Z |
