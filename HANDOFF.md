# labhq 인수인계 — 개발 총괄에게

공개 저장소 [ehojune/bioinfo-team-3d](https://github.com/ehojune/bioinfo-team-3d) · 패키지/CLI `labhq` · 현재 버전 v0.5.
개발 담당과 위임 범위는 최신 PI 인계를 따릅니다. PI 창구는 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760), 현재 지원·복구 기준은 [#882](https://github.com/ehojune/bioinfo-team-3d/issues/882)에서 확인합니다.

## 시작과 종료

1. `AGENTS.md`, `STATUS.md` 상단, 열린 PR, #760의 최근 댓글과 현재 배정 이슈의 최신 인계를 읽습니다. 위임·점검·한도는 [운영 기준](docs/reference/lead-operations.md)과 최신 PI 결정을 따릅니다.
2. test는 바꾼 곳만, 전체 suite는 CI에 맡깁니다. 실제 설정·PI 인스턴스·원자료는 업무 범위와 승인 없이 건드리지 않습니다.
3. 끝날 때 PR과 `docs/status/`에 결과를 쓰고 이 문서의 우선순위를 갱신합니다. 지난 큐·결정은 [참고 기록](docs/reference/README.md)에 두고 여기에는 현재 것만 남깁니다.

## 현재 우선순위 (2026-10-26)

main·열린 PR을 대조한 큐입니다. 구현·CI 통과는 운영 PASS나 새 실행 승인이 아닙니다. 기존 실행 계약과 중단 결정을 유지하며, 지원 범위는 [manual](docs/manual.md#중단-조건-한눈에)을 봅니다.

| 순서 | 현재 상태·다음 일 |
|---|---|
| 종료 조건 · [#877](https://github.com/ehojune/bioinfo-team-3d/issues/877#issuecomment-6370526043) | API 단절 뒤 기한 종료·잔류 0은 특정 설치판의 무해한 증명에서 PASS. 실제 adapter timeout/cancel·복구 중복 방지·full-capability doctor는 미평가. 요청 전체 상한 확인이 남아 OPEN이며 새 실행 GO는 별도 |
| 저장 · [#606](https://github.com/ehojune/bioinfo-team-3d/issues/606) | [#966](https://github.com/ehojune/bioinfo-team-3d/pull/966) publish 저장 대기는 병합(`929eded9`). [#965](https://github.com/ehojune/bioinfo-team-3d/issues/965)의 동기 helper·SavedResults 전환은 [#972](https://github.com/ehojune/bioinfo-team-3d/pull/972) OPEN으로 미완료. #606 전체 완료는 미검증 |
| 복구 · [#675](https://github.com/ehojune/bioinfo-team-3d/issues/675) | [#937](https://github.com/ehojune/bioinfo-team-3d/pull/937) 로컬 job 구현·[#968](https://github.com/ehojune/bioinfo-team-3d/pull/968) 취소 재전송 회귀 병합. 실제 transport·OS 재시작은 미검증; WSL·진행률은 잔여 |
| 소유권 · [#618](https://github.com/ehojune/bioinfo-team-3d/issues/618) | [#967](https://github.com/ehojune/bioinfo-team-3d/pull/967) 병합으로 획득 경쟁 P1 해소. broker port P2는 보류하며 v1 선결에 추가하지 않음 |
| 디스크 · [#678](https://github.com/ehojune/bioinfo-team-3d/issues/678) | [#920](https://github.com/ehojune/bioinfo-team-3d/pull/920) 시작 전 여유 검사 병합. 다운로드·압축 해제 직전 검사와 실제 부족 상황 검증은 잔여 |
| 지원 · [#882](https://github.com/ehojune/bioinfo-team-3d/issues/882) | 문서 갱신으로 닫지 않음. 새 설치·반복 실행·복구·지원 요청 end-to-end는 운영 미검증. 실제 실행은 별도 GO 필요 |
| 이후 · [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739) | dry-run·필수 gate·전체 검토와 별도 비교 승인 뒤 동일 조건 비교. 구현·회귀 CI만으로 연구 효용을 판정하지 않음 |

설치·doctor 통과는 연구 요청 성공의 근거가 아닙니다. P2A 생성→부착→사용→산출과 장치 효용은 요청별 근거로 판단하며 비해당 pack은 미평가로 남깁니다.

## PI 결정

| 항목 | 결정 |
|---|---|
| 개발 담당 | [현재 역할 인계](https://github.com/ehojune/bioinfo-team-3d/issues/882#issuecomment-6402248394)의 담당·파일 소유권을 따름. 특정 에이전트 고정 총괄 안내보다 이 인계가 우선하며 제품 CSO 설정과 별개 |
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
