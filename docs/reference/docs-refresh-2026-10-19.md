# 문서 일치 점검 (2026-10-19)

조회용 근거입니다. 코드 기준은 main `f2b4b582`이며 PI 결정은 [#760 최신 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6263295610)까지 반영했습니다. 현재 순서는 [HANDOFF](../../HANDOFF.md)를 봅니다.

| 확인한 사실 | 근거·반영 |
|---|---|
| 기본 역할 13개: Claude Code 8·Codex 5 | `agents/core/*.yaml`을 Registry·`scripts/integrations.py`와 같은 템플릿 제외 규칙으로 집계. README 한영판·manual·HANDOFF 수정 |
| 시설팀·편집자는 Codex 보조 역할 | `facilities.yaml`·`editor.yaml`은 read-only. manual의 빠진 editor 행 추가. 실제 등록 수·로그인 성공과 구분 |
| 제품 내부 agent 확장은 제한됨 | `labhq/adapters/claude_code.py`, `codex.py`, manual의 경계. recruiter의 Paper2Agent 변환만 Claude `Agent` 예외 |
| 새 설정과 기존 설치의 기본값은 구분 | `labhq/init_defaults.py::REFERENCE_BEHAVIOUR`, `init_wizard.py`. 영문 README의 init·demo·solo·경로 차단 설명을 코드/한글판과 맞춤 |
| 현재 개발 총괄·PI 창구 | Codex 고정·Codex/Claude 위임, #760의 4시간 보고. AGENTS·CLAUDE·HANDOFF에 반영. 제품 CSO/직원 YAML은 변경 없음 |
| 최신 PI 답변 | 10월 19일 9M·20일 2M. #732는 표준 분석 bioinfo-agent/nf-core, 단순 작업 직접, 복잡한 신규 pipeline 직접 코딩 뒤 PI yes일 때만 기여 PR |
| 구현·CI와 실운전은 별개 | 공개 소형 장치 실운전은 아직 미실행. #739는 dry-run·준비 게이트 뒤. 다음 PI 확인은 소형 주제 선택·bench 준비 게이트 |

네 카드의 이미지·표 구조와 핵심 한계는 유지하고 상세 인용·비교 수치를 [README 근거](readme-evidence.md)에 옮겼습니다. 옛 HANDOFF 큐·결정은 [보존본](handoff-history-2026-10-19.md)으로 옮기고 당시 상태임을 표시했습니다. 매뉴얼은 직원표·파일 구조·배정 기준·현재 채널과 과거 결정 링크를 고쳤습니다.

## 길이

UTF-8을 읽어 LF로 정규화한 Unicode 문자 수입니다. 줄바꿈 포함, byte 수와 다릅니다.

| 파일 | 이전 | 이후 | 차이 |
|---|---:|---:|---:|
| `README.md` | 16,073 | 11,726 | -4,347 |
| `README.en.md` | 23,311 | 19,075 | -4,236 |
| `AGENTS.md` | 1,794 | 1,599 | -195 |
| `HANDOFF.md` | 10,443 | 3,549 | -6,894 |
| `docs/manual.md` | 160,046 | 160,629 | +583 |
| `docs/pi-qa.md` | 7,247 | 7,727 | +480 |
| `docs/reference/README.md` | 542 | 800 | +258 |
| `CLAUDE.md` | 1,545 | 1,511 | -34 |
| 주요 3문서 (README·AGENTS·HANDOFF) | 28,310 | 16,874 | -11,436 |
| 영문판 포함 4문서 | 51,621 | 35,949 | -15,672 |

별도 조회 자료: `handoff-history-2026-10-19.md` 5,863자 · `readme-evidence.md` 13,266자. 이 점검 메모와 PR 상태 기록은 위 합계에서 제외했습니다. 주요 3문서는 약 15,000자 목표 근처인 16,874자로 줄였습니다.

## 문체 측정

주요 3문서의 badge 줄을 뺀 한글 포함 줄에서 쉼표/줄은 0.879→0.548입니다(표·코드 설명 포함). `되어진다`·`가지고 있다`·`에 있어서`는 전후 0건, `할 수 있다`·`것이다`는 각 1→0건입니다. 이 수치만으로 품질을 판정하지 않습니다.
