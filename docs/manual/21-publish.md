[← 매뉴얼 목차](../manual.md)

## 내 저장소로 올리기

HPC 사이트 프로필이나 `ssh_host`를 쓰는 요청 묶음은 텍스트 사본에 잡 로그의 경로 가림 규칙을 적용합니다. 확장자 없는 설정, `.out`·`.err`, 운전 기록과 manifest도 검사합니다. 원본은 보존하며 `MANIFEST.tsv`의 `redacted`·`source_sha256`·`source_size`로 변환 사본을 구분합니다. 사본의 크기·hash는 다시 계산합니다. 사이트 경로를 가린 스크립트는 `documented` 등급으로 남기고, 가림이 필요한 hash 고정 최종 checkpoint는 묶음 생성을 거절합니다([검증 범위](../reference/fix-1416.md)).

가림은 명시된 text 확장자·등록된 설정 이름에 한정하며 미등록 형식은 bytes를 보존합니다. script의 공유·UNC 경로도 가립니다. 텍스트는 순차 처리하며 JSON 16 MiB·한 줄 1M 문자·남은 경로 1만 개/16M 문자 상한이나 UTF-8 오류·NUL은 명시 오류로 묶음을 거절합니다.

Windows selection 거부는 전용 error로 표시합니다. 선행 단계가 실패하면 coverage report를 건너뛰고 진단 artifact를 보존합니다. 성공 실행은 coverage·receipt 검증을 모두 거칩니다.

개발 CI는 보호 예제·markup이 같은 prose와 정해진 기록만 축소합니다. manual·개발 지침·정책은 내용·구조가 같은 문장 경계 줄바꿈만 후보입니다. main 재사용에는 동일 tree·workflow/policy·coverage와 필수 gate 성공 증거가 필요합니다. Linux 3.10/3.12와 Windows nightly·workflow_dispatch는 전수입니다. routine Windows는 필수 OS 경계 검사 한 번과 10분 job 상한을 적용합니다. 선택 증거가 틀리면 빠르게 실패하며 전수로 확대하지 않습니다. timeout도 실패입니다. clean-install·공식 validator·MCP gate는 유지합니다([판별·증거 경계](../reference/ci-reuse.md)). 필수 3D 화면 검사는 화면마다 브라우저 시작 최대 30초·UI 검사 최대 20초를 주고 정리까지 합산 60초 안에 끝냅니다.

커밋 공동 작성자 표시(`.githooks/commit-msg`, `git config core.hooksPath .githooks`)는 git이 메시지 끝 문단에서 trailer로 인식하는 줄만 읽습니다. 본문에 쓴 `Co-authored-by:` 예시는 글로 두어 작성자 표시로 세지 않고, `core.commentChar`가 정한 주석 줄과 scissors 구분선(`git commit -v`) 아래 diff는 메시지로 읽지 않습니다. 모델명은 환경변수 `LABHQ_COMMIT_MODEL`이 있을 때만 이름에 채우며 없으면 추정하지 않습니다(#808).

```bash
./scripts/publish_github.sh          # gh CLI로 public 저장소 <내 계정>/labhq 생성 + push
./scripts/check_public.sh            # 올리기 전 점검: 비밀값, 실제 설정 파일, 대용량·유전체 데이터 파일
```
