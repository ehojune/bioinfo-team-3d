## 결론

Refs #634. PR #638의 P1 `4281094837`을 수정했습니다. 수정 커밋 `5b886e2`, main 병합 `07fd2ae`.

## 바뀐 것

- `scoped_open_reads()`에서 모든 읽기 예외를 최종 검사합니다. 현재 작업·논문 스킬 빌드 폴더와 명시한 plugin·skill·자기 Claude project만 남깁니다.
- workspace·talent 공용 root, 다른 작업 subtree, staff 공용 상위 경로와 링크 별칭을 제외합니다. 같은 목록을 Claude·Codex·managed shell이 받습니다.
- main의 카피바라 표시와 이 branch의 편집자 이름 보존을 함께 반영했습니다.

## 확인

- 새 회귀 23건: 수정 전 23 failed → 수정 후 23 passed. 다른 작업의 `outputs/RESULT.md` 차단·자기 파일 허용을 세 경로에서 확인했습니다.
- 관련 pytest 567 passed·3 skipped. #553 비공개 불변식 포함.
- 전체 Node CJS 62개 파일과 공개 저장소 검사 통과.
- 기존 공용 root 허용 단언을 차단 단언으로 바꾸고 Claude 저장 출력 fixture에 현재 작업 경로를 명시했습니다.
- PI 인스턴스·PI venv·HPC 사용 없음. 테스트는 clone venv와 격리 홈에서 실행했습니다.

## 남은 것

- 동적으로 조합한 셸 경로는 기존 정적 판정의 한계입니다.
- CI 결과는 PR checks에서 확인합니다. 리뷰 요청·병합은 하지 않습니다.
