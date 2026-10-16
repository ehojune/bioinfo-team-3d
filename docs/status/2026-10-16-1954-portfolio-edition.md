## 결론

PR #699의 3차 봇 지적 세 건을 반영했습니다. Refs #686.

## 바뀐 것

- wheel에 bundled plugin manifest·SKILL·template을 싣고 clean-install에서도 wheel 내용을 검사합니다.
- 새 저장소 루트에는 portfolio 정리본만, 전체 호환 사본은 `labhq/results/<요청 id>/`에만 올립니다.
- 행 단위 표와 내부 자료의 1–9 칸 표는 두 payload에 같은 규칙으로 제외합니다.
- 중첩 보고서의 그림 링크를 다시 쓰고 정리본의 모든 Markdown 상대 링크를 검사합니다.
- PNG 그래픽 초록만 있어도 SVG 대체본을 만듭니다.
- 내부 자료 표는 단계가 `output_types: table`로 선언하고 1–9 칸이 없을 때만 루트·호환 사본에 싣습니다.
- PDF는 request bundle 기준 그림 링크를 쓰고, 이전 버전의 `report.md` 정리본도 재개해 QC합니다.

## 확인

- 3차 지적 회귀는 수정 전 5 failed/1 passed, 수정 후 통과했습니다.
- 관련 pytest 42 passed. 실제 wheel을 빌드·설치해 `BUNDLED_PLUGIN_DIR` manifest를 읽었습니다.
- 기존 clean-install은 editable install이라 wheel 누락을 볼 수 없었습니다.
- `docs/manual.md`는 152,465자에서 152,528자로 63자 늘었습니다.

## 남은 것

CI 결과는 최종 push 뒤 확인합니다. 리뷰 재요청과 병합은 하지 않습니다.
