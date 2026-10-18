## 결론

PR #741을 #740이 포함된 `origin/main`과 합쳐 정리본의 콘텐츠·재현·PDF 규칙을 함께 보존했습니다. Refs #736.

## 바뀐 것

- 단계 스크립트는 원래 `outputs/` 구조와 실행 폴더를 보존하고 재현 안내에 폴더별 명령을 적습니다.
- 서로 다른 개인 경로는 고유한 `LABHQ_INPUT_*`로 바꿉니다. f-string·raw·연결 문자열도 처리하고, 나머지는 경로를 지운 뒤 수동 확인 주석을 붙입니다.
- 새 저장소는 대표 경로만, 기존 저장소는 `labhq/results/<요청 id>/` 호환 경로를 유지합니다.
- PDF 표지에 보고서 제목 전체를 쓰고 그림·설명과 graphical abstract의 지면 규칙을 조정했습니다.
- README 첫 화면·표·claim 각주·수정 이력·갈래 근거 문서는 #740 규칙을 따릅니다.

## 확인

- 이번 P1 회귀 3건은 직전 head에서 3 failed, 수정 뒤 통과했습니다.
- 관련 pytest 188 passed, 게시 Python `py_compile`과 `git diff --check` 통과.
- 실제 Edge PDF 2쪽에서 표지 제목·그래픽 초록·페이지 밀도를 확인했습니다.
- merge 뒤 관련 pytest 286 passed, `git diff --check` 통과.
- `scripts/check_public.sh`는 90초 무출력로 중단해 완료 여부를 확인하지 못했습니다.
- `docs/manual.md`: main 159,391자·branch 159,808자 → merge 159,634자.

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.
