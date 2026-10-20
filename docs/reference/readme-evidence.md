# README 근거·한계 상세

조회용 기록입니다. 현재 안내는 [한국어 README](../../README.md)와 [English README](../../README.en.md)에 있습니다. 2026-10-19 문서 정리에서 네 카드와 비교표의 상세 근거를 옮겼습니다. 아래 시운전·bench 결과는 각 기록의 시점에 한하며 새 실운전 PASS를 뜻하지 않습니다.

## 카드 1

**틀린 근거를 미리 거른다**

AI가 인용을 지어내거나 잘못 읽는 실수(hallucination)를 보고서 전에 잡으려고 검사를 두 겹 둡니다. 과학 리뷰어(GPT-6-Astra)가 PubMed 원문을 열어 인용을 대조하고, 결론을 바꾸는 지적(P1)이 남으면 요청은 통과하지 못합니다. [The Virtual Biotech](https://github.com/harrisongzhang/TheVirtualBiotech)(Science 2026)에서 가져온 claim 장부와 산출 sha256 기록(MIT)으로 `labhq verify`가 보고서 근거 앵커와 hash를 다시 검사합니다.

근거: [11차 시운전](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5969847101)(GSE19804): 문헌 단계가 원 논문의 SEMA5A 방향을 거꾸로 적고 Yang 2018 허브 목록을 빠뜨린 것을 리뷰어가 PubMed 원문과 대조해 잡았고, 요청은 보고서를 내기 전에 멈췄습니다 · [예시 GSE10072](../examples/gse10072/README.md): `labhq verify`로 산출 58개 hash 재확인 · [#339](https://github.com/ehojune/bioinfo-team-3d/pull/339)(verify)

한계: 근거 앵커와 PI 근거 승인(CP2)은 기본 꺼짐인 연구 lane에서만 돕니다. `verify`는 파일이 바뀌지 않았는지만 보며, 내용이 옳다는 증명은 아닙니다. 리뷰가 놓친 비율은 재지 않았습니다.

## 카드 2

**논문에서 출발한다**

계획 전에 선행 연구 직원이 같은 주제의 논문·리뷰 몇 편에서 꼭 할 분석을 뽑아 점검표에 넣고, 보고서에는 인용을 붙인 '선행 연구 기준' 절을 둡니다. 팀에 없는 방법이 필요하면 PI 승인 뒤 [Paper2Agent](https://github.com/jmiao24/Paper2Agent)로 그 논문의 코드를 도구로 쥔 기한부 **파견직**을 들일 수 있습니다.

근거: [#395](https://github.com/ehojune/bioinfo-team-3d/pull/395)(선행 연구 단계) · [매뉴얼: 선행 연구 기준](../manual.md#topic-점검표와-선행-연구-기준) · [매뉴얼: 파견직 제도](../manual.md#파견직-제도-paper2agent)

한계: 선행 연구 단계는 시험 요청 4건에서만 돌았습니다. 파견직은 mock 시험만 통과했고 실제 논문으로 채용까지 간 적이 없으며, Paper2Agent skill은 따로 설치해야 합니다.

## 카드 3

**일을 넘겨도 기준은 같다**

CSO는 계획마다 분야 이름표(topic 키)를 적습니다. 키는 PI가 검토한 어휘(125키, 그중 79키는 [EDAM](https://github.com/edamontology/edamontology) 온톨로지와 연결)의 topic 45개에서 고릅니다. topic은 논문 63편·교과서 4권·웹 자료 66건을 조사해 넓혔고([#420](https://github.com/ehojune/bioinfo-team-3d/issues/420)), 43개 topic에 근거 논문을 30편 이상(모두 1286편) 붙였습니다([#446](https://github.com/ehojune/bioinfo-team-3d/pull/446), [목록](topic_papers.md)). 코드는 그 키로 점검표를 골라 계획·리뷰·단독 처리에 같은 목록을 넘기고, 가정으로 답한 항목은 보고서 '한계'에 남깁니다. 연구 lane(기본 꺼짐)의 분야 규칙 pack도 같은 키로 붙습니다.

근거: [#390](https://github.com/ehojune/bioinfo-team-3d/pull/390)(topic 필수, 어휘 100키) · [#369](https://github.com/ehojune/bioinfo-team-3d/issues/369)(계획 문장 한 줄로 분야 규칙이 빠지던 우회 → topic 판정으로 닫음)

한계: 점검표는 45개 분야, 135항목입니다([#402](https://github.com/ehojune/bioinfo-team-3d/pull/402), [#420](https://github.com/ehojune/bioinfo-team-3d/issues/420)). 처음 세 분야 밖의 42개 분야는 실제 요청에서 돌지 않았습니다. 누락이 줄었는지는 재지 않았습니다. 이름표 사이 관계를 쓰는 의미 모델은 기본 꺼짐인 [그림자 단계](../../README.md#어노테이션시맨틱온톨로지)이고, 단순 SQL 기준선보다 낫다는 결과는 없습니다.

## 카드 4

**끊겨도 잇고, 묶어서 남긴다**

요청·단계·승인을 SQLite에 저장합니다. PC가 멈춰도 PI 재개 카드 한 장이면 끝난 단계는 건너뛰고 멈춘 단계부터 잇고, 구독 한도·로그인 만료에는 요청을 세워 두었다가 풀리면 다시 갑니다. 요청이 끝나면 hash로 확인한 산출과 분석 코드를 한 폴더([요청 묶음](../manual.md#요청-묶음))에 모으고, 단계 사이 경로는 상대경로로 바꿉니다.

근거: [v0.25 리허설](../status/2026-10-04-0458-release-v025.md) 요청 2: PC가 멈춰 labhq가 모두 꺼진 뒤 이어 가기 카드 한 장으로 다시 이어서 리뷰 통과(accept)까지 갔습니다 · [벤치 A 2차](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5975156477): labhq 재현성 2 → 4점([#384](https://github.com/ehojune/bioinfo-team-3d/pull/384)), Astra 단독은 같은 보고서가 두 채점에서 4점·3점 · [#389](https://github.com/ehojune/bioinfo-team-3d/pull/389)(요청 묶음)

한계: 한도·로그인 대기는 10-05 실제 로그인 만료로 요청 4건이 실패한 뒤 고쳤고, test로만 확인했습니다. 재로그인은 사람이 합니다. 요청 묶음은 같은 PC runner의 산출만 모읍니다. `documented`는 기록만, `replayable`은 스크립트·환경·경로 조건 충족, `rerun_verified`는 별도 재실행 receipt까지 있는 경우입니다.

## 비교 근거

| | labhq | Claude Code·Codex 단독 | 연구 workbench |
|---|---|---|---|
| 리뷰 | ✅ 다른 회사 리뷰어\* | ❌ 같은 모델 자기 검토 | ⚠️ 켜면 별도 맥락 검토 |
| 분석 기준 | ✅ 선행 논문·분야 점검표 | ⚠️ 모델 재량 | ⚠️ 고른 skill 따라 |
| 근거 추적 | ✅ 산출 hash·앵커 재검사 | ⚠️ 남긴 스크립트·로그 | ✅ 산출 출처·재실행 비교 |
| 승인 | ✅ 웹·폰 카드 | ⚠️ 세션 안에서 | ✅ 승인 모드·폰 원격 |
| 중단 뒤 | ✅ 멈춘 단계부터 | ⚠️ 세션만 재개 | ✅ 원격 잡 복구 |
| 문헌 관리 | ⚠️ 검색·인용만 | ⚠️ 붙인 도구 따라 | ✅ 서재·PDF·선별 |
| 속도·비용 | ❌ 큰 과제 144분·$31 | ✅ 같은 과제 19분 | — |

연구 workbench 칸은 [open-science](https://github.com/aipoch/open-science) v0.35.0 README·ROADMAP을 읽고 적었고, 앱은 돌려 보지 않았습니다(2026-10, [#316](https://github.com/ehojune/bioinfo-team-3d/issues/316)). 속도·비용은 재지 않아 —로 둡니다. labhq 점검표가 누락을 줄이는지는 아직 재지 않았습니다.
\* 기본 과학 리뷰어는 Codex/GPT-6-Astra입니다. Claude 분석 단계에는 다른 회사 리뷰가 되지만 Codex 분석 단계에는 같은 회사 리뷰입니다.
단독 칸의 속도·근거 추적은 벤치 기준선(GPT-6-Astra 단독)에서 잰 값입니다. 채점은 GPT-6.1-Sol이 출처를 가리고 과제마다 한 번 했습니다([#373](https://github.com/ehojune/bioinfo-team-3d/issues/373)).

**labhq가 맞는 경우**: 결과를 근거째 남에게 넘겨야 하거나, 몇 시간짜리 분석을 맡겨 두고 결정만 폰으로 하고 싶을 때. v0.25 리허설에서 accept된 4건은 설치·예산·이어 가기를 뺀 PI 카드가 요청당 0~1장이었습니다([리허설](../status/2026-10-04-0458-release-v025.md)).

**아직 지는 곳**: 같은 과제를 눈가림 채점하면 정확성은 같았지만 총점은 단독 세션이 앞섰습니다(10-05 벤치 C: 큰 과제 22 대 28, 작은 과제 20:24·22:23·20:23). 진 곳은 재현 자료가 덜 갖춰진 점과 보고서가 긴 점입니다(큰 과제 본문 약 12,600자 대 3,100자)([#373](https://github.com/ehojune/bioinfo-team-3d/issues/373), [#423](https://github.com/ehojune/bioinfo-team-3d/issues/423)). 작은 요청은 [#388](https://github.com/ehojune/bioinfo-team-3d/pull/388)부터 강한 모델 한 명이 바로 끝내고, 이 경로를 탄 과제는 격차가 줄었습니다(2→1점, 6→3점). 이때는 기본으로 리뷰를 거치지 않습니다. 이 경로는 `orchestrator.solo_agent`를 설정해야 켜지고, 기본은 꺼져 있어 작은 요청도 팀으로 갑니다. 문헌 서재·PDF 관리가 중심이면 workbench가 맞습니다.

## English evidence

The evidence below supports the English README; trial and benchmark results retain their original dates.

### Card 1

**Catch bad evidence early**

Two layers of checks catch AI mistakes, such as inventing or misreading a citation (hallucination), before the report. The science reviewer (GPT-6-Astra) opens the PubMed source and checks each citation against it, and a request cannot pass while a finding that would change the conclusion (P1) remains. Using the claim ledger and output sha256 records (MIT) taken from [The Virtual Biotech](https://github.com/harrisongzhang/TheVirtualBiotech) (Science 2026), `labhq verify` re-checks the report's evidence anchors and hashes.

Evidence: [11th trial run](https://github.com/ehojune/bioinfo-team-3d/issues/298#issuecomment-5969847101) (GSE19804): the reviewer checked against the PubMed source and caught that the literature step had reversed the direction of SEMA5A in the original paper and missed the Yang 2018 hub list, so the request stopped before it produced a report · [Example GSE10072](../examples/gse10072/README.md): `labhq verify` re-checked the hashes of 58 outputs · [#339](https://github.com/ehojune/bioinfo-team-3d/pull/339) (verify)

Limits: Evidence anchors and PI evidence approval (CP2) run only in the research lane, which is off by default. `verify` only checks that files are unchanged; it does not prove the content is correct. The share of errors the review misses has not been measured.

### Card 2

**Start from papers**

Before planning, the prior-research staff pull the analyses that must be done from a few papers and reviews on the same topic and put them in the checklist, and the report gets a "prior-research baseline" section with citations. If the team lacks a method, after PI approval you can bring in a term-limited **contractor** who holds that paper's code as a tool, using [Paper2Agent](https://github.com/jmiao24/Paper2Agent).

Evidence: [#395](https://github.com/ehojune/bioinfo-team-3d/pull/395) (prior-research step) · [Manual: prior-research baseline](../manual.md#topic-%EC%A0%90%EA%B2%80%ED%91%9C%EC%99%80-%EC%84%A0%ED%96%89-%EC%97%B0%EA%B5%AC-%EA%B8%B0%EC%A4%80) · [Manual: contractor system](../manual.md#%ED%8C%8C%EA%B2%AC%EC%A7%81-%EC%A0%9C%EB%8F%84-paper2agent)

Limits: The prior-research step has run on only 4 test requests. The contractor flow has passed only mock tests and has never gone as far as hiring from a real paper, and the Paper2Agent skill must be installed separately.

### Card 3

**Same bar at every handoff**

The CSO writes a domain tag (topic key) on every plan. The key is picked from the 45 topics of a PI-reviewed vocabulary (125 keys, 79 of them linked to the [EDAM](https://github.com/edamontology/edamontology) ontology). The topics were widened from a survey of 63 papers, 4 textbooks and 66 web sources ([#420](https://github.com/ehojune/bioinfo-team-3d/issues/420)), and 43 topics have at least 30 supporting papers, 1286 in all ([#446](https://github.com/ehojune/bioinfo-team-3d/pull/446), [list](topic_papers.md)). Code uses that key to choose a checklist and passes the same list to planning, review, and single-staff runs, and items answered by assumption are left in the report's "Limitations". The research lane's (off by default) domain rule packs attach by the same key.

Evidence: [#390](https://github.com/ehojune/bioinfo-team-3d/pull/390) (topic required, 100-key vocabulary) · [#369](https://github.com/ehojune/bioinfo-team-3d/issues/369) (a bypass where one line in the plan text dropped the domain rules, closed with a topic check)

Limits: The checklist covers 45 domains and 135 items ([#402](https://github.com/ehojune/bioinfo-team-3d/pull/402), [#420](https://github.com/ehojune/bioinfo-team-3d/issues/420)). The 42 domains beyond the first three have not run on real requests. Whether omissions decreased has not been measured. The semantic model, which uses relations between tags, is a [shadow stage](../../README.en.md#annotation-semantics-ontology) that is off by default, and no result shows it beats a plain SQL baseline.

### Card 4

**Resumable and bundled**

Requests, steps, and approvals are stored in SQLite. If the PC stops, one PI resume card skips the finished steps and continues from the step that stopped; on a subscription limit or login expiry, the request is held and goes again once that clears. When a request ends, hash-verified outputs and the analysis code are gathered in one folder ([request bundle](../manual.md#%EC%9A%94%EC%B2%AD-%EB%AC%B6%EC%9D%8C)), and paths between steps are rewritten as relative paths.

Evidence: [v0.25 rehearsal](../status/2026-10-04-0458-release-v025.md) request 2: after the PC stopped and labhq was fully shut down, one resume card brought it back and it went on to a review pass (accept) · [Bench A, round 2](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5975156477): labhq reproducibility 2 → 4 points ([#384](https://github.com/ehojune/bioinfo-team-3d/pull/384)), while Astra alone got 4 and 3 points from two scorings of the same report · [#389](https://github.com/ehojune/bioinfo-team-3d/pull/389) (request bundle)

Limits: Waiting on limits and login was fixed after a real login expiry on 10-05 failed 4 requests, and was confirmed only by tests. Re-login is done by a person. The request bundle gathers only outputs from the same PC runner. `documented` records provenance; `replayable` meets script, environment, and path requirements; `rerun_verified` also requires a separate rerun receipt.

### Comparison evidence

| | labhq | Claude Code / Codex alone | Research workbench |
|---|---|---|---|
| Review | ✅ Other-vendor reviewer\* | ❌ Same model checks itself | ⚠️ Separate-context check when on |
| Standard | ✅ Prior papers, domain checklists | ⚠️ Model's discretion | ⚠️ Depends on chosen skill |
| Evidence trail | ✅ Output hash, anchor re-check | ⚠️ Scripts and logs left behind | ✅ Output provenance, rerun compare |
| Approval | ✅ Web / phone cards | ⚠️ Inside the session | ✅ Approval modes, phone remote |
| After a stop | ✅ From the stopped step | ⚠️ Session resume only | ✅ Remote job recovery |
| Literature | ⚠️ Search and citation only | ⚠️ Depends on attached tools | ✅ Library, PDF, screening |
| Speed / cost | ❌ Big task 144 min, $31 | ✅ Same task 19 min | — |

The research workbench column was filled in from the [open-science](https://github.com/aipoch/open-science) v0.35.0 README and ROADMAP; the app was not run (2026-10, [#316](https://github.com/ehojune/bioinfo-team-3d/issues/316)). Speed and cost were not measured, so they are marked —. Whether labhq's checklists reduce omissions has not been measured yet.
\* The default science reviewer is Codex/GPT-6-Astra: a different vendor for Claude analysis steps and the same vendor for Codex analysis steps.
The standalone column's speed and evidence-trail values were measured on the bench baseline (GPT-6-Astra alone). Scoring was done once per task by GPT-6.1-Sol with sources hidden ([#373](https://github.com/ehojune/bioinfo-team-3d/issues/373)).

**When labhq fits**: when you must hand results to someone else together with their evidence, or want to leave a multi-hour analysis running and make only the decisions from your phone. In the v0.25 rehearsal, the 4 accepted requests had 0 to 1 PI cards per request, not counting install, budget, and resume cards ([rehearsal](../status/2026-10-04-0458-release-v025.md)).

**Where it still loses**: in the 10-05 bench C blind scoring, accuracy tied but total scores favored the standalone session: 22 to 28 for the big task and 20:24, 22:23, 20:23 for the small tasks. Reproduction materials and long reports were the main gaps (about 12,600 vs 3,100 body characters on the big task; [#373](https://github.com/ehojune/bioinfo-team-3d/issues/373), [#423](https://github.com/ehojune/bioinfo-team-3d/issues/423)). The optional [solo route](https://github.com/ehojune/bioinfo-team-3d/pull/388) narrowed the gap on tasks that used it, but skips review by default. It requires `orchestrator.solo_agent`; without it, small requests still use the team. A workbench fits work centered on a literature library and PDF management.
