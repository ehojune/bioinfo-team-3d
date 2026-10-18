## 결론

PR #750에 기관 전문 접근과 PI PDF 요청 경로를 구현했습니다. Fixes #745. Fixes #746.

## 바뀐 것

- 설정으로 켠 기관 profile·proxy를 DOI 한 편씩 시도하며 TDM·외부 모델 허용, 간격, 하루 상한을 확인합니다.
- 실패 논문을 한 카드로 묶고 카드 첨부나 요청 안 inbox의 PDF를 읽습니다.
- 2시간 뒤에는 초록·다른 OA로 진행하고 접근 실패 원장과 보고서 한계에 남깁니다.

## 확인

- 새 test는 main에서 import 실패, branch에서 관련 pytest 107 passed입니다.
- 경계 pytest 277 passed·5 skipped, Node 64 passed, `git diff --check` 통과입니다.
- `scripts/check_public.sh`는 Windows Bash에서 30초 동안 출력 없이 멈춰 종료했습니다.
- 첫 CI의 slow job은 일반 MCP timeout까지 7320초로 늘어난 문제를 찾아, 문헌 MCP에만 긴 대기를 적용하도록 고쳤습니다.

## 남은 것

- 수정 head의 GitHub CI 확인이 남았습니다. 리뷰 요청과 병합은 하지 않습니다.
