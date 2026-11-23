# 사전등록 양식

이 폴더를 실행마다 작업 폴더로 통째로 복사합니다. 분석과 보고서는 한 세션에서 작성합니다. 양식을 채우는 데 labhq나 외부 패키지가 필요 없습니다.

## 분석 전

1. `preregistration.json`에 가설, 자료의 버전과 분석 단위, 시료 포함·제외 기준을 적습니다. `primary_metric`에는 부호로 방향을 나타내는 값의 계산법, 단위, 비교 순서(예: 처리군 − 기준군)를 적습니다. 알려진 교란은 `known_confounders`에 처리 방침과 함께 적습니다. 확인한 교란이 없으면 빈 목록 `[]`을 둡니다.
2. 양성·음성 대조군에 서로 다른 `control_id`를 정합니다. 각 대조군의 비교, 지표와 단위, 기대 방향(`increase`, `decrease`, `no_meaningful_change`), 숫자로 판단할 합격 규칙을 적습니다. 필요한 만큼 항목을 추가하고 확보하지 못한 대조군은 `planned_check`에 사유를 적습니다.
3. `decrease_at_or_below`와 `increase_at_or_above`를 유한한 JSON 숫자로 채웁니다. 전자는 0보다 작고 후자는 0보다 커야 합니다. 아래 기준과 같거나 작으면 `decrease`, 위 기준과 같거나 크면 `increase`, 두 기준 사이면 `inconclusive`로 판정합니다. 지표값을 산출하지 못하면 `not_evaluable`입니다. 문헌의 방향과 달라도 같은 규칙을 적용합니다.
4. 빈 문자열과 임시 값을 채운 뒤 `registered_at_utc`에 `YYYY-MM-DDTHH:MM:SSZ` 형식으로 시각을 적습니다. 분석을 시작하기 전에 아래 명령을 복사한 폴더에서 실행하고, 해시와 시각을 별도 실행 기록에 남깁니다. 이 사본을 보존하고 이후 변경은 사유와 함께 보고서에 기록합니다.

```sh
python -c "from hashlib import sha256; from pathlib import Path; print(sha256(Path('preregistration.json').read_bytes()).hexdigest())"
```

## 점검과 보고

- 분석 전에 `sample_record_checklist.md`를 채웁니다. 각 줄에 점검 단위, 전체 대상 수 `N`, 확인 결과를 기록한 대상 수 `n`, 근거를 적습니다. 불일치를 발견한 대상도 점검을 마쳤다면 `n`에 넣고, `finding`과 `impact`에 문제와 해석에 미친 영향을 적습니다. 적용할 대상이 없을 때만 `0/0`을 쓰고 이유를 남깁니다. 전체 대상 수를 확인할 수 없다면 `N`에 `NA`와 함께 `finding`에 이유를 적고 미완료로 둡니다.
- 분석 뒤 `control_results.md`에 사전등록의 대조군 ID마다 한 줄을 채웁니다. 등록한 단위로 관측값을 쓰고 등록한 규칙에 따라 `pass` 또는 `fail`을 적습니다. 평가할 수 없으면 `observed`에 `NA`, `result`에 `not_evaluable`, 근거 칸에 그 이유를 적습니다.
- 파일명, 표 헤더, 점검 ID는 그대로 둡니다. 셀 안에 줄바꿈이나 `|` 문자를 넣지 않습니다. 근거는 공개 기록의 ID 또는 산출물의 상대 경로와 표·행으로 가리킵니다.
- 최종 보고서에는 불일치나 미점검 항목이 해석에 미치는 영향을 본문에 쓰고, 대조군 실패와 사전등록에서 달라진 점도 설명합니다. 아래 표에는 등록한 단위로 판정에 쓴 지표값을 반올림하기 전 값으로 적습니다. 값이 없으면 `NA`와 `not_evaluable`을 쓰고 이유를 본문에 남깁니다.

| field | value |
| --- | --- |
| primary_metric_value | |
| primary_decision | |

## 검사와 가림 보고서

Python 3.10 이상에서 실행합니다. 세 명령은 JSON을 표준 출력에 씁니다.

```sh
python kit.py check 실행폴더 보고서.md
python kit.py existing 산출묶음 --report report/report.md --repo labhq체크아웃
python kit.py blind 가림출력폴더 비공개폴더/대응표.json 보고서1.md 보고서2.md
```

- `check`는 점검표의 고정 ID 5개와 사전등록 대조군 ID마다 비거나 잘못된 줄을 한 건씩 셉니다. `0 ≤ n ≤ N`과 `0/0`의 사유를 확인하고 보고서 표의 값으로 주 판정을 다시 계산합니다. 대조군의 수치 합격 규칙은 사전등록을 보고 직접 대조합니다.
- `existing`은 labhq 체크아웃의 본문·표 대조와 인용 감사를 실행합니다. 보고서 경로는 묶음 기준이며 기본값은 `report.md`입니다. 링크의 표 경로는 보고서가 있는 폴더 기준입니다. 인용 감사는 묶음의 `citations.json`, `records.json`, 원문 사본을 씁니다. 두 JSON 중 하나라도 없으면 `unavailable`로 기록합니다. 대조한 수와 읽지 못한 표를 함께 확인합니다.
- `blind`는 본문에서 링크 주소, 그림, 코드 블록, 메타데이터를 빼고 같은 UTF-8 텍스트 형식으로 씁니다. 새 출력 폴더만 채점자에게 넘기고 새 대응표는 별도로 봉인합니다. 출력 JSON에 대응표의 SHA-256이 나옵니다. 넘기기 전에 본문에 비교군을 밝히는 문구가 남았는지 확인합니다.
- 종료 코드 `0`은 완료, `1`은 검사 지적, `2`는 입력·실행 오류입니다. `check`와 `blind`는 이 폴더만 복사해 실행합니다.
