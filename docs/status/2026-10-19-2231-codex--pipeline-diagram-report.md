## 결론

PR #764. 정리본과 PDF에 실제 실행된 단계만 그린 분석 파이프라인 모식도와 단계별 실행 타임라인·실행 보고서를 넣고, 단계가 남긴 Nextflow report·timeline·trace·dag를 `run/nextflow/`에 모읍니다. Fixes #756, Refs #760.

## 바뀐 것

- 새 `labhq/run_report.py`: `run/pipeline_diagram.svg`(담당·입력·산출·QC 띠·선행 단계), `run/run_timeline.svg`(시작→종료 막대, 걸린 시간·비용), `run/run_report.md`(표·실행하지 않은 계획 단계·Nextflow 절). 한국어는 SVG `<text>`이고 script·외부 글꼴이 없습니다.
- 값이 없으면 `기록 없음`/`종료 기록 없음`으로 두고 0원·시각을 만들지 않습니다. 비용은 `costs.task_cost_item`의 actual·estimated·unknown을 따릅니다. `run/costs.md`도 요청 비용이 없을 때 `$0.00` 대신 `기록 없음`을 씁니다.
- 내부 자료 단계는 `내부 자료 단계 N`으로만 그리고 입력·산출 이름을 쓰지 않습니다. 판정은 기존 `internal_data_steps` 규칙(`step_uses_internal_data`로 분리)을 그대로 씁니다.
- README·`report/report.md`의 답 아래 `분석 파이프라인` 절에 두 그림·실행 보고서·Nextflow를 링크합니다. 그래픽 초록이 첫 그림이라는 #608 규칙 때문에 Markdown에서는 링크로 두고, PDF에는 두 그림을 넣습니다(RENDER_VERSION 7, 캐시 키에 그림 hash 포함).
- Nextflow: `pipeline_info` 아래 nf-core·`--run_label` 이름까지 인식합니다(예전에는 `report.html`·`timeline.html`·`trace.txt`만, dag 없음). 내부 자료 단계의 기록과 정리 뒤에도 로컬 경로가 남은 기록은 빼고 이유를 적습니다. 정리본 전체는 멈추지 않습니다. 텍스트 1 MB·합계 5 MB 상한 대신 파일당 20 MB 상한을 둡니다. 기록이 없으면 bioinfo-agent 단계는 "사용 여부 미확인", 다른 단계는 "쓰지 않음(산출 기록 기준)"으로 적습니다.
- 실행 경로 확인: labhq는 Nextflow를 직접 실행하지 않습니다. bioinfo-agent의 기본 `config/local.config`가 report·timeline·trace·dag를 모두 켜고 `${outdir}/pipeline_info/`에 씁니다(코드를 읽어 확인). 그래서 `agents/core/bioinfo-agent.yaml`에 네 파일을 `outputs/pipeline_info/`에 복사해 산출로 선언하라는 세 줄을 프롬프트 끝에 더했습니다. #762도 이 파일의 앞쪽 문단을 고치지만 수정 구간이 겹치지 않습니다.
- 문서: manual의 결과 계약·정리본·bioinfo-agent 절, `docs/reference/run-report-756.md`(값 출처·수집 규칙·확인 범위), HANDOFF 큐와 Codex 한도(10-19 14M, 10-20 2M). README 한영판은 정리본 구성을 설명하지 않아 그대로 뒀습니다. LF 정규화 Unicode 글자 수: manual 161,060→161,797, HANDOFF 3,617→3,631, README 11,726·README.en 19,075는 변화 없음.

## 확인

- 새 `tests/test_run_report.py` 9개: 가짜 계획·실행 기록에서 실행한 단계만 포함, SVG `<text>`·script 없음, 타임라인 값·누락, Nextflow 수집·경로 차단·내부 자료 제외·없음 표시, README·report·PDF 연결, 저장된 앞부분에 절 삽입, `edition_assets` 크기 상한.
- 관련 pytest 23개 파일: 661 passed, 2 skipped. 기존 test 4개는 새 그림 수·파일 목록·RENDER_VERSION에 맞춰 기대값을 바꿨고, 두 그림의 이름과 순서, 정리본과 PDF 그림의 동일성을 더 확인합니다.
- `scripts/integrations.py --check`, 공개 저장소 검사, `git diff --check` 통과.
- headless Edge로 두 SVG를 PNG로 그려 글자 넘침이 없는지 확인했습니다. 실제 분석·Nextflow·HPC 실행, 실제 요청의 정리본 게시, 웹 화면 확인은 하지 않았습니다.

## 남은 것

- CI와 리뷰는 총괄이 이어받습니다.
- 실제 bioinfo-agent 실행에서 네 파일이 `outputs/pipeline_info/`에 선언되는지는 다음 실운전에서 확인해야 합니다.
- lane venv에 `markdown-it-py`가 없어 lane tmp에 `--target`으로 설치해 PYTHONPATH로 썼습니다. 공용 venv는 바꾸지 않았습니다.
