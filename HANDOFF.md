# labhq 인수인계 — 개발 총괄에게

공개 저장소 [ehojune/bioinfo-team-3d](https://github.com/ehojune/bioinfo-team-3d) · 패키지/CLI `labhq` · 현재 버전 v0.5.
2026-10-19 PI 결정으로 **Codex가 총괄을 고정 담당**하며 Codex·Claude subagent에 위임합니다. 현재 창구는 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760)입니다.

## 시작과 종료

1. `AGENTS.md`, `STATUS.md` 상단, 열린 PR, #760의 최근 댓글을 읽습니다. 위임·점검·한도는 [운영 기준](docs/reference/lead-operations.md)을 따릅니다.
2. test는 바꾼 곳만, 전체 suite는 CI에 맡깁니다. 실제 설정·PI 인스턴스·원자료는 업무 범위와 승인 없이 건드리지 않습니다.
3. 끝날 때 PR과 `docs/status/`에 결과를 쓰고 이 문서의 우선순위를 갱신합니다. 지난 큐·결정은 [참고 기록](docs/reference/README.md)에 두고 여기에는 현재 것만 남깁니다.

## 현재 우선순위 (2026-10-23)

개발 투입은 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760)의 최신 PI 결정을 봅니다. 현재 새 작업 발주는 보류하고 기존 PR을 마무리합니다. **B는 종료된 상태이며 NO-GO**입니다.

| 순서 | 현재 상태·다음 일 |
|---|---|
| 1 · [#877](https://github.com/ehojune/bioinfo-team-3d/issues/877) | 장애·재시작과 무관한 요청 전체 90분 hard stop 및 해당 자식만 종료하는 격리 검증. B GO 차단 P1 |
| 2 · [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739) | A/B 연구 요청0. B `45bd6e80` 격리 설치·P2A pin·11명 roster. 네 daemon의 독립 기한 종료는 확인했지만 현재 세대 doctor는 종료형 Job을 거부했고 직원 자식·실제 adapter 종료 증명은 미완료. 원문·출처 pin, $9/HTTP9 집행과 별도 GO가 남음 |
| 기존 PR · [#880](https://github.com/ehojune/bioinfo-team-3d/pull/880)·[#876](https://github.com/ehojune/bioinfo-team-3d/pull/876) | 공통 Chrome 시작 예산 수정 [#881](https://github.com/ehojune/bioinfo-team-3d/pull/881)은 병합·Windows5분59초. 두 PR은 수정 반영 뒤 최종 head CI·리뷰를 확인한다. Chrome 내부 지연 원인과 장기 안정성은 미확정 |
| 이후 · [#799](https://github.com/ehojune/bioinfo-team-3d/issues/799) | B 실운전 근거 뒤 bench dry-run·필수 gate → Astra 전체 검토 → 동일 요청 4-arm 비교를 판단. 구현·CI는 실측 근거가 아님 |

설치·doctor 통과는 연구 요청 성공이나 장치 효용의 근거가 아닙니다. B에서는 P2A 생성·부착·실제 참조·산출 연결과 의미·근거 장치의 사용 여부를 확인합니다. 맞지 않는 bulk/single-cell pack은 미평가로 남깁니다.

## PI 결정

| 항목 | 결정 |
|---|---|
| 개발 총괄 | Codex 고정, Codex·Claude subagent에 위임. 제품 CSO 설정과 별개 |
| token 한도 | PI가 #760에서 정한 일일 한도와 남은 주간 여유 안에서 admission. 기준은 [운영 기준](docs/reference/lead-operations.md) |
| 분석 배정(#732) | 표준 생물정보학은 bioinfo-agent/nf-core, 단순 작업은 직접. 없는 복잡한 파이프라인은 직접 코딩한 뒤 기여 여부를 PI에게 묻고 yes일 때만 bioinfo-agent PR([매뉴얼](docs/manual.md#직원과-도구)) |
| 의미 장치 | 측정용 Shadow는 유지하고 부가 확장은 동결. 연구 이점은 미측정 |

이전 결정은 [보존본](docs/reference/handoff-history-2026-10-19.md)에 있습니다.

## 구조 한눈에

CSO는 gateway 안에서 계획과 의존관계를 관리하고 runner가 실제 CLI를 병렬 실행합니다. 단계별 폴더·SQLite 상태·승인과 근거 기록을 거쳐 리뷰·보고서·요청 묶음으로 이어집니다.

| 위치 | 역할 |
|---|---|
| `labhq/gateway/`, `labhq/web/` | WS·REST·승인·상태. 2.5D/3D는 `state.js` reducer 공유 |
| `labhq/orchestrator/`, `labhq/registry.py` | 브리핑·계획 DAG·배정·리뷰·보고. 직원 정의 로드 |
| `labhq/runner/`, `labhq/adapters/`, `labhq/tools/` | CLI/MCP 배선·작업 격리·SGE/PBS/Slurm 제출·감시 |
| `agents/core/*.yaml` | 기본 역할 13개: Claude Code 8·Codex 5. 로컬 solo·파견직 별도 |
| `labhq/research/`, `labhq/vocab/`, `labhq/recruit/` | 연구 계약·checkpoint·분야 규칙·의미 모델·Paper2Agent |
| `labhq/report_contract.py`, `portfolio.py`, `result_storage.py`, `reproduction_receipt.py` | 보고 구조·정리본·PDF·결과 저장·재현 receipt |
| `labhq/integrations/`, `patch_notes/entries/`, `docs/status/` | GitHub 보고·공개 가드·개발 기록과 생성 목차 |

직원 내부의 임의 agent 확장은 막습니다. Claude의 `Agent` 허용은 Paper2Agent 변환 담당 recruiter에만 있고 Codex 직원의 `multi_agent`는 꺼집니다. 개발 총괄의 위임 레인과 제품 직원의 실행 경계는 다릅니다. 직원별 모델·도구는 [매뉴얼](docs/manual.md#직원과-도구)과 YAML이 기준입니다.

작업 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다. 직원 설정은 `scripts/integrations.py --check`, 기록은 `scripts/patch_notes.py check`·`scripts/notes_index.py --validate`로 확인합니다.

[#788 CI 정책](docs/reference/ci-reuse.md)은 보호 예제가 같은 prose·정해진 기록만 축소하고 main 재사용은 동일 tree·전체 gate 증거를 요구합니다. 불명확하면 full이며 #787의 3.10 진단을 유지합니다.
