# 2026-11-10 claude-worker3 — #1352 r2: 비차단 데이터-도구 적합성 참고

## 결론
단계를 시작할 때 `inputs/` 아래 파일을 확장자와 헤더로 읽어, 형식·데이터 종류·저장된 EDAM ID를 5~10줄 참고로 단계 프롬프트에 붙인다. 단계 작업을 알면 `input_fit.yaml`로 맞는지 알리고, 맞지 않으면 맞는 입력 종류만 적는다. 카드·실행 차단·모델 호출·외부 조회·설치는 없고, 오류나 모르는 파일이면 참고를 생략한다. semantics off에서도 동작한다.

## 바뀐 것
- `labhq/research/input_fit_hint.py`(새): 헤더 확인(FASTQ·BAM·CRAM·VCF·h5ad·FASTA·rds) / 확장자 추정 구분, 형식→데이터 종류는 어휘 정의에 있는 것(fastq·bam·cram·vcf)만, EDAM은 `edam_subset.yaml`에 저장된 것만
- `labhq/runner/daemon.py`: `kind == "step"`이면 TASK.md 쓰기 직전에 참고를 context에 붙임(최소 연결)
- `tests/test_input_fit_hint.py`(Windows inventory 등록): 대표 형식, 추정/확인 구분, FASTQ→DE 선행 처리 참고, 맞는 입력, 미인식·오류·깨진 표, 10줄 상한, 실제 runner 프롬프트 연결과 카드 0
- manual 14 한·영

## 실행한 것
- `tests/test_input_fit_hint.py` 12 passed
- runner·input_fit 관련 시험 60개 파일: 2545 passed (로컬 mcp 판본 문제 2건 실패, 무관)

## 미해결
- 단계 작업(operation)은 지금 `input_fit_operation` meta나 관계표의 직원 매핑(data_steward)으로만 정해진다. 계획 pack에서 작업을 넘기려면 `cso.py`가 meta에 `input_fit_operation`을 넣어야 한다(범위 밖)
- manifest 해석은 넣지 않았다. 헤더 확인만 한다
