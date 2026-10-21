# labhq 인수인계 — 개발 총괄에게

공개 저장소 [ehojune/bioinfo-team-3d](https://github.com/ehojune/bioinfo-team-3d) · 패키지/CLI `labhq` · 현재 버전 v0.5.
2026-10-19 PI 결정으로 **Codex가 총괄을 고정 담당**하며 Codex·Claude subagent에 위임합니다. 현재 창구는 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760)입니다.

## 시작과 종료

1. `AGENTS.md`, `STATUS.md` 상단, 열린 PR, #760의 최근 댓글을 읽습니다. 위임·점검·한도는 [운영 기준](docs/reference/lead-operations.md)을 따릅니다.
2. test는 바꾼 곳만, 전체 suite는 CI에 맡깁니다. 실제 설정·PI 인스턴스·원자료는 업무 범위와 승인 없이 건드리지 않습니다.
3. 끝날 때 PR과 `docs/status/`에 결과를 쓰고 이 문서의 우선순위를 갱신합니다. 지난 큐·결정은 [참고 기록](docs/reference/README.md)에 두고 여기에는 현재 것만 남깁니다.

## 현재 우선순위 (2026-10-22)

PI가 신규 일반 작업 보류를 해제했습니다. 승인 예산 안에서 열린 PR을 마무리하고 같은 담당자에게 관련 후속 작업을 이어 배정합니다. 시운전 체크리스트는 [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739)입니다.

| 후속 항목 | 현재 범위 |
|---|---|
| #799 | 정책·4-arm 기록기 병합(#815·#824). 실제 bench·효용은 미측정 |
| #739 | 공개 소형 주제 선택·계약·배포 runtime 준비. 실제 실행은 별도 승인 뒤 |

병합된 감사 항목의 구현·검증 범위는 [감사 우선순위](docs/reference/audit-priorities-2026-10-20.md)에 있습니다. #811의 queued-halt 검사는 executor 대기와 분리됐으며 [검증 경계](docs/reference/shadow-test-cost-811.md)를 따릅니다.

논문 스킬은 승인 뒤 함께 준비하고, 분석 단계는 시작 시 준비된 결과만 씁니다. 미완료면 기다리지 않고 생략 사실을 기록합니다([준비·취소와 CI 근거](docs/reference/paper-skill-waiting-671.md)). 검증 문서는 읽기 권한만 주며 결과 참조 receipt는 실제 tool 호출·과학적 효용의 증거가 아닙니다([참조 계약](docs/reference/paper-skills-798.md)).

개발 준비가 끝나고 PI가 실운전을 승인한 뒤의 순서는 공개 소형 주제 실운전 → bench dry-run·필수 게이트 → Astra의 코드·README·구조 전체 검토 → 동일 요청 bench 비교(labhq 혼합 roster 대 Astra·Sol·Opus 단독 세션)입니다. 가동 전에는 배포 인스턴스의 `doctor --runtime` 준비 판정을 다시 확인합니다. v0.5 선언(2026-10-08) 이후 장치의 구현·CI 통과는 새 실운전 PASS를 뜻하지 않습니다([로드맵](docs/manual.md#로드맵)).

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
