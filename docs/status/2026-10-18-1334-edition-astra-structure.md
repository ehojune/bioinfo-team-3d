## 결론

PR #741에서 정리본의 재현 안내·코드 경로·중복 게시·PDF 지면을 고쳤습니다. Refs #736.

## 바뀐 것

- 재현 안내에는 lock·입력 설정·실행 순서·예상 출력·재현 범위만 두고 긴 지시는 따로 보관합니다.
- 코드의 개인 경로는 `LABHQ_INPUT_*`로 바꾸고 일반 문자열은 보존하며 Python 사본을 compile 검사합니다.
- 새 저장소는 대표 경로만, 기존 저장소는 `labhq/results/<요청 id>/` 호환 경로를 유지합니다.
- PDF 표지에 보고서 제목 전체를 쓰고 그림·설명과 graphical abstract의 지면 규칙을 조정했습니다.

## 확인

- 핵심 회귀는 main 상당 상태에서 4 failed, 수정 뒤 7 passed입니다.
- 관련 pytest 276 passed, `py_compile`과 `git diff --check` 통과.
- 실제 Edge PDF 2쪽에서 표지 제목·그래픽 초록·페이지 밀도를 확인했습니다.
- `scripts/check_public.sh`는 90초 무출력로 중단해 완료 여부를 확인하지 못했습니다.
- `docs/manual.md`: 158,217자 → 158,410자(+193자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.
