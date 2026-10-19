# labhq 인수인계 — 개발 총괄에게

공개 저장소 [ehojune/bioinfo-team-3d](https://github.com/ehojune/bioinfo-team-3d) · 패키지/CLI `labhq` · 현재 버전 v0.5.
2026-10-19 PI 결정으로 **Codex가 총괄을 고정 담당**하며 Codex·Claude subagent에 위임합니다. 현재 창구는 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760), 이전 기록은 #298 → #435입니다.

## 시작과 종료

1. `AGENTS.md`, `STATUS.md` 상단, 열린 PR, #760의 최근 댓글을 읽습니다. 설치 PC의 로컬 인계 문서가 있으면 그 문서가 지정한 범위만 추가로 읽습니다.
2. 아래 큐를 따릅니다. test는 바꾼 곳만, 전체 suite는 CI에 맡깁니다. 실제 설정·PI 인스턴스·원자료는 업무 범위와 승인 없이 건드리지 않습니다.
3. 끝날 때 PR과 `docs/status/`에 결과를 쓰고 이 문서의 큐·PI 결정을 갱신합니다. 로컬 진행 노트와 gitignore 대상 `HARVEST.md`도 갱신하되 커밋하지 않습니다.

큰 변경 때와 **매 PI 보고 전** README 한영판·manual·HANDOFF를 코드·직원 YAML·현재 PR과 대조합니다. 과거 큐와 결정 근거는 [참고 기록](docs/reference/README.md)에 두고 본문에는 현재 권고만 남깁니다.

## 작업 큐 (2026-10-20 통합 점검)

[#760의 이관 요약](https://github.com/ehojune/bioinfo-team-3d/issues/760) 기준입니다. PR의 최신 head·CI·리뷰는 작업 직전에 다시 확인합니다.

| 순서 | 할 일 | 완료 조건·현재 한계 |
|---|---|---|
| 완료 | #762(`923a4fa`)·#764(`9d9e973`)·#763(`7b4d866`) 병합·PI 배포 | 총괄 인계: doctor 준비 예, status 요청 0. 새 연구·정리본 게시·Nextflow PASS 근거는 아님 |
| 진행 | receipt PR #767 OPEN | P1 3건을 bundle 완료 대기·request ID·Hub 저장 계약 수정으로 대응, 신규 회귀 5건·관련 43건 통과. push 후 CI는 driver 확인. 공유 리포팅·storage는 #748 뒤 #754 rebuild 순서 |
| 병렬 | cgroup #765·guard #766·PID 기록 #768 OPEN | 구현·검증 진행 상태이며 실제 연구·게시·Nextflow PASS로 쓰지 않음 |
| 2 | 공개 소형 주제 2개를 제시하고 PI 선택 뒤 장치 실운전 | P2A 생성→호출→산출, 의미 모델·그림자 요청·ontology·checklist·EDAM·pack, 문헌/PDF, 보고서·묶음·정리본을 요청별로 대조. **아직 미실행** |
| 3 | bench dry-run → 준비 게이트 검토 요청 | 동일 입력·판본·PI 답·예산과 arm별 state/workspace 분리, 장치 작동 근거, 예상 시간·비용을 확인 |
| 4 | 준비 게이트 완료 뒤 [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739) | labhq 혼합 roster와 Astra·Sol·Opus **단독 세션**을 같은 요청으로 비교. CSO만 교체하는 실험과 구분 |
| 5 | Astra의 README·코드·구조 전체 검토 | 비교 결과와 실제 결함을 근거로 후속 개선 |

v0.5 선언(2026-10-08)은 당시 실제 연구 요청 2건과 `labhq verify` exit 0의 근거입니다. 이후 장치의 구현·CI 통과는 새 실운전 PASS를 뜻하지 않습니다. 버전별 기준은 [매뉴얼 로드맵](docs/manual.md#로드맵)에 있습니다.

## PI 결정과 다음 확인

[현재 PI 창구](https://github.com/ehojune/bioinfo-team-3d/issues/760)의 결정과 2026-10-20 총괄 인계를 적용합니다.

| 항목 | 결정 |
|---|---|
| 개발 총괄 | Codex 고정, Codex·Claude subagent에 여러 작업 위임. 제품 CSO 설정은 별개 |
| Codex 일일 한도 | 10-19 실제 11,376,303/14M, 미사용 2,623,697 → M 단위 올림 3M + 4M = **10-20 7M 확정**([근거](docs/reference/reproduction-receipt.md)) |
| #732 배정 | 표준 생물정보학은 bioinfo-agent/nf-core, 단순 작업은 직접 처리. 없는 복잡한 신규 파이프라인은 직접 코딩한 뒤 **기여 여부를 PI에게 묻고 yes일 때만 bioinfo-agent PR** |

#732는 PR #762(`923a4fa`)로 병합됐습니다. #748은 receipt 범위이며 #754의 CLI/storage rebuild는 별도 작업입니다. 다음 PI 확인은 공개 소형 주제 선택과 dry-run 뒤 bench 준비 게이트입니다. 이전 교대 운영·결정표는 [보존본](docs/reference/handoff-history-2026-10-19.md)에 있습니다.

## 구조 한눈에

CSO는 gateway 안에서 계획과 의존관계를 관리하고 runner가 실제 CLI를 병렬 실행합니다. 단계별 폴더·SQLite 상태·승인과 근거 기록을 거쳐 리뷰·보고서·요청 묶음으로 이어집니다.

| 위치 | 역할 |
|---|---|
| `labhq/gateway/`, `labhq/web/` | WS·REST·승인·상태. 2.5D/3D는 `state.js` reducer 공유 |
| `labhq/orchestrator/`, `labhq/registry.py` | 브리핑·계획 DAG·배정·리뷰·보고. 직원 정의 로드 |
| `labhq/runner/`, `labhq/adapters/`, `labhq/tools/` | CLI/MCP 배선·작업 격리·SGE/PBS/Slurm 제출·감시 |
| `agents/core/*.yaml` | 기본 역할 13개: Claude Code 8·Codex 5. 로컬 solo·파견직 별도 |
| `labhq/research/`, `labhq/vocab/`, `labhq/recruit/` | 연구 계약·checkpoint·분야 규칙·의미 모델·Paper2Agent |
| `labhq/report_contract.py`, `portfolio.py`, `result_storage.py` | 보고 구조·정리본·PDF·결과 저장 |
| `labhq/integrations/`, `patch_notes/entries/`, `docs/status/` | GitHub 보고·공개 가드·개발 기록과 생성 목차 |

직원 내부의 임의 agent 확장은 막습니다. Claude의 `Agent` 허용은 Paper2Agent 변환 담당 recruiter에만 있고 Codex 직원의 `multi_agent`는 꺼집니다. **개발 총괄의 별도 clone·위임 레인**과 제품 직원의 실행 경계는 다릅니다. 직원별 모델·도구는 [매뉴얼](docs/manual.md#직원과-도구)과 YAML이 기준입니다.

## 작업 규칙

PR·패치노트·관련 test·CI·리뷰·공개 검사 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다. 생성 파일 `STATUS.md`·`patch_notes/README.md`는 직접 고치지 않습니다. 직원 설정은 `scripts/integrations.py --check`, 기록은 `scripts/patch_notes.py check`·`scripts/notes_index.py --validate`로 확인합니다.

이벤트·안전 경계·설치·알려진 한계는 [매뉴얼](docs/manual.md), 옛 큐·결정은 [보존본](docs/reference/handoff-history-2026-10-19.md)에 있습니다.
