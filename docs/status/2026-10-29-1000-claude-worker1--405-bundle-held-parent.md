## 단계
요청 묶음 출력 폴더의 symlink 바꿔치기 — Refs #405 (PR #389 후속).

## 현재 main 대조
- 남아 있었습니다. `build_request_bundle`은 `requests/`를 경로로 한 번 검사한 뒤 `_clear_owned_dir(temp/target)`·`mkdir`·쓰기·`_replace_dir`를 모두 경로로 했습니다. 입력 쪽(`_held_workdir`)만 held handle이었습니다(`labhq/request_bundle.py` main `380d4fca`).

## 한 일 (`labhq/request_bundle.py`만)
- `requests/`를 `HeldDir.hold`로 한 번 열어(링크는 따르지 않음) 빌드 내내 잡고 있습니다. `HeldDir`는 Windows에서 handle이 delete 공유를 허용하지 않아 열려 있는 동안 그 폴더와 상위 폴더가 이름 바꾸기·삭제·교체되지 않습니다(`adapters/held_dir.py` 설명). 따라서 Windows에서는 바꿔치기 자체가 막힙니다.
- 그 밖의 플랫폼에서는 `_clear_owned_dir`(삭제 직전)와 마지막 `_replace_dir` 직전에 `held.same_as(requests/)`를 확인하고, 폴더가 바뀌었으면 삭제·교체 없이 `OSError`로 멈춥니다.
- 큰 함수의 지역 변수 `held`(작업 폴더 handle)와 이름이 겹치지 않도록 새 handle은 `requests_held`로 받습니다. 본문은 `_build_request_bundle`로 옮겼고 동작은 같습니다.

## 테스트 결과
- 새 시험 2건(`test_request_bundle.py`): (1) 빌드 중 `requests/` 이름 바꾸기가 Windows에서 거절되고 빌드가 끝난 뒤에는 handle이 남지 않음. (2) 이전 묶음이 있는 상태에서 빌드 도중 `requests/`를 symlink로 바꿔 두면 Windows는 바꿈 자체가 막혀 빌드가 끝나고, 그 밖의 플랫폼은 `OSError`로 멈추며 바뀐 대상 아래 같은 이름의 폴더(`precious.txt`)가 그대로 남음.
- `test_request_bundle.py` 전체 통과(로컬 Windows). 수정 전 같은 파일 99 passed.
- 미검증: POSIX에서의 실행(두 번째 시험의 `OSError` 분기는 로컬 Windows에서 실행되지 않음, CI ubuntu·macOS로만 확인), 실제 Windows 파일 공유 잠금 아래의 복구 시나리오.

## 한계(남는 것)
- POSIX에서는 확인과 삭제 사이의 아주 짧은 틈이 남습니다(`same_as` 뒤 `shutil.rmtree`는 경로로 동작). 완전히 닫으려면 dir_fd 기반 삭제·교체가 필요한데 `shutil.rmtree(dir_fd=)`는 Python 3.11부터라 3.10 지원과 맞지 않습니다. #405가 말한 "runner 묶음 이전 때 함께 설계"의 몫입니다.
- 묶음 안 파일을 쓰는 경로(`temp / "x"`)는 여전히 경로 기반입니다. 이번 지적의 삭제·교체는 막았지만 새 파일 생성은 같은 방식으로 보호하지 않습니다.

## 바꾼 파일
labhq/request_bundle.py, tests/test_request_bundle.py와 변경 기록.

## 막힌 점
없음.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
