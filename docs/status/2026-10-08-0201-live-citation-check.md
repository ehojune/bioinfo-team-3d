# 2026-10-08 · PR #477

## 결론

Crossref·doi.org·NCBI를 조회하는 live resolver를 추가하고, 철회 상태를 CP2와 `labhq verify`에 연결했다.

## 바뀐 것

- 인용 논문의 철회는 결함, 철회 공지·정정·우려 표명은 경고로 구분한다.
- 네트워크 실패와 timeout은 `requires_verification`으로 남긴다.
- 기본값은 꺼짐이며, 실제 응답 fixture를 쓰는 무네트워크 테스트를 추가했다.

## 실행한 것

- Windows 전체 pytest: 4669 passed, 57 skipped.
- 관련 pytest: 163 passed, 2 skipped. 최종 집중 테스트: 8 passed.
- `scripts/check_public.sh` 통과.

## 미해결

- CI 전체 결과를 확인 중이다.

Closes #422

🤖 Generated with Codex (gpt-5) for the labhq dev lead
