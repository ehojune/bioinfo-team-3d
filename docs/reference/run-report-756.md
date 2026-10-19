# 실행 보고서·파이프라인 모식도 (#756)

PI 10-19 요청: "수행한 bioinfo 분석 파이프라인 모식도와 nextflow에서 제공하는 레포트 제공 — 앞으로도 들어가야 함". 이 문서는 구현 근거만 둡니다. 동작 요약은 [매뉴얼](../manual.md#편집자와-정리본-확인)에 있습니다.

## 자료 출처

| 값 | 읽는 곳 | 없을 때 |
|---|---|---|
| 실행 여부 | `req.results[step]`. CSO가 시작하지 않은 단계(미충족 의존·건너뛴 수정)는 빈 `task_id`로 남김 | 결과가 없거나 빈 `task_id`면 "실행하지 않은 계획 단계" |
| 시작·종료 | `results[step].provenance.runs[*].started_at/ended_at` (runner manifest, `Workspace.provenance`) | 시작이 없으면 `기록 없음`, 종료가 하나라도 없으면 `종료 기록 없음`. 막대를 그리지 않음 |
| 걸린 시간 | 첫 시작부터 마지막 종료까지(HPC 깨우기 run 포함) | 위와 같음 |
| 비용 | `costs.task_cost_item`: actual·estimated·unknown | unknown이면 `기록 없음`. 합계는 기록된 단계만 더하고 뺀 수를 적음 |
| 담당·엔진 | 계획의 `agent_id`, run의 engine·model | `담당 기록 없음` |
| 입력·산출 | 계획 `input_refs`, 결과 `outputs`의 파일 이름 | 내부 자료 단계는 표시하지 않음 |
| QC 지점 | 단계 id·지시의 QC·검토·review·check (`run_report.step_flow`, 갈래 연결표와 같은 규칙) | — |

## Nextflow 기록

- bioinfo-agent의 `config/local.config` 6절이 `report`·`timeline`·`trace`·`dag`를 모두 켜고 `${outdir}/pipeline_info/`에 씁니다(nf-core 이름 `execution_report_<ts>.html` 등, `--run_label`이면 `<label>-trace-<ts>.txt`). labhq는 Nextflow를 직접 실행하지 않으므로 직원 지시(`agents/core/bioinfo-agent.yaml`)로 네 파일을 `outputs/pipeline_info/`에 복사하고 산출로 선언하게 합니다.
- `run_report.nextflow_kind`는 `pipeline_info` 아래 파일만 종류별 확장자(report·timeline: html, trace: txt·tsv, dag: html·svg·dot·mmd)로 인식합니다. `params_*.json` 같은 다른 파일은 기록으로 보지 않습니다.
- 정리본 텍스트 사본의 1 MB·합계 5 MB 상한은 nf-core report(보통 2–4 MB)를 조용히 빼므로, Nextflow 기록은 파일당 20 MB 상한만 둡니다. 묶음의 `bundle_max_file_mb` 상한은 그대로입니다.
- 내부 자료 단계의 기록은 trace의 sample tag 때문에 게시하지 않습니다(예전에는 집계 선언이 있으면 html을 허용). 정리 뒤에도 `.labhq/` 같은 로컬 경로가 남은 기록은 정리본 전체를 멈추지 않고 그 파일만 뺍니다. 두 경우 모두 실행 보고서에 이유를 적습니다.
- 기록이 없는 단계: bioinfo-agent면 "기록 없음 · Nextflow 사용 여부 미확인", 다른 직원이면 "쓰지 않음(산출 기록 기준)". 요청 전체에 기록이 없으면 README와 실행 보고서에 "Nextflow 산출 기록이 없습니다"를 적습니다.

## 표시 위치

- 정리본: `run/pipeline_diagram.svg`, `run/run_timeline.svg`, `run/run_report.md`, `run/nextflow/`. 그림 SVG는 한국어를 `<text>`로 쓰고 script·외부 글꼴이 없습니다(report_abstract와 같은 방식).
- README·`report/report.md`: 답 아래 `분석 파이프라인` 절에 두 그림·실행 보고서·Nextflow 링크. #608의 "그래픽 초록이 첫 그림" 규칙을 지키려고 그림은 링크로 둡니다. `run/run_report.md`는 그림을 넣습니다.
- PDF: 같은 절에 두 그림을 넣습니다(11장 상한에 포함). Nextflow 기록은 링크 없이 종류만 적습니다. PDF 캐시 키에 두 그림의 hash를 넣고 `RENDER_VERSION`을 7로 올렸습니다.
- `run/costs.md`: 요청 비용이 없으면 `$0.00` 대신 `기록 없음`, 단계별 값은 실행 보고서로 링크합니다.

## 확인 범위 (2026-10-19)

- 가짜 계획·실행 기록 pytest(`tests/test_run_report.py`): 실행 단계만 포함, SVG `<text>`, 타임라인 값과 누락, Nextflow 수집·경로 차단·내부 자료 제외·없음 표시, README·report·PDF 연결, `edition_assets` 크기 상한.
- headless Edge로 두 SVG를 PNG로 그려 글자 넘침이 없는지 눈으로 확인했습니다.
- 실제 Nextflow·HPC 실행, 실제 요청의 정리본 게시, PI 화면 확인은 하지 않았습니다.
