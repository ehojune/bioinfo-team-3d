[← 매뉴얼 목차](../manual.md)

## 의미 모델과 온톨로지

[#1388](https://github.com/ehojune/bioinfo-team-3d/issues/1388) 삭제안은 검증 중입니다. 아래는 적용 뒤에도 유지할 계약이며 출시 안내가 아닙니다.

| 계약 | 삭제안 적용 뒤 동작 |
|---|---|
| 어휘·EDAM | 계획은 로컬 산출 종류 key를 선언합니다. EDAM 연결은 메타데이터이며 입력 적합성의 증거가 아닙니다. |
| 분야 점검표·pack | CP1이 필요한 점검과 pack 정의를 고정하고 리뷰가 그 근거를 보존합니다. |
| CP1 입력 적합성 | `research/input_fit.yaml`의 fit·mismatch·unknown 규칙을 씁니다. 알려진 불일치는 기존 횟수 안에서 계획을 교정합니다. 선언이 없거나 판본이 다르면 모름으로 남깁니다. |
| 단계 입력 참고(#1352) | 확장자와 헤더로 짧은 prompt 참고를 만듭니다. 오류면 생략하며 실행 차단·설치·추가 모델 호출은 없습니다. |
| core 상태 점검 | 어휘·점검표·pack 오류의 code·단계·시각을 CP1·CP2와 실행 기록에 남깁니다. 예외 본문은 공개하지 않습니다. |

삭제 대상은 live 그림자·객체 뷰·액션·A/B 재사용 참고·semantics CLI와 새 `semantic_report.md/json` 생성입니다. 오프라인 #136 모델·pilot은 보존합니다. 구현이나 삭제 여부만으로 연구 효용을 판단하지 않습니다.

옛 `semantics`·`research.semantic_report` 키는 비활성 호환 입력으로만 받습니다. 기능을 다시 켜지 않으며 다른 unknown key는 거부합니다. 통합을 마치면 과거 보고서 파일은 기존 공개 사본 경로로 계속 읽습니다.

[범위·근거·남은 통합](../reference/prune-semantics-1388.md) · [설정](15-config.md)
