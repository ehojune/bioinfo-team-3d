## 결론

Refs #636. 인사팀(recruiter)을 비버에서 카피바라로 바꿨습니다. 편집자(editor)는 2D·3D 모두 비버로 남습니다.

## 바뀐 것

- 이름·배정: `카피바라 인사팀` / `capybara`. 메신저 표시는 `카피바라(인사팀)`입니다.
- 2D: 캐릭터 1 스타일의 둥근 몸·작은 귀·뭉툭한 코와 명함 소품을 추가했습니다.
- 3D: fallback 없이 가벼운 procedural 카피바라를 만들고, 비버 editor archetype을 분리했습니다.
- README 데모 GIF·still, 직원 표, 3D 캐릭터 표를 갱신했습니다. PR #639 본문에 데스크톱·폰 전후 화면을 붙였습니다.

## 확인

- 새 test `tests/web_recruiter_capybara.cjs`는 main에서 실패하고 이 branch에서 통과합니다. 이름·매핑·정규직 동물 고유성·2D/3D 분기를 검사합니다.
- Node test 59개 파일 모두 통과. 관련 pytest 99 passed, 1 skipped.
- 2D visual test 32 views가 기존 baseline과 같아 baseline은 바꾸지 않았습니다.
- 데스크톱·폰은 빈 임시 포트 4개에서 캡처했고 8787·55921은 쓰지 않았습니다.
- `scripts/check_public.sh` 통과.

## 남은 것

- GitHub CI 결과를 확인합니다.
