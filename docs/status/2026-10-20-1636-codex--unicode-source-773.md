# 재현 receipt 유니코드 source 경로 (#773, PR #816)

## 결론

PR #816(draft), 커밋 `fb5e47c`. 재현 receipt가 공개 source의 안전한 유니코드·구두점 경로(`code/analysis/outputs/scripts/분석.py`, `run (v2).py` 등)를 `blocked_path`로 막지 않고 문법 검사·실행합니다. Refs #773.

## 재현 (현재 main)

- 정리본의 `_code_target`은 공개 파일의 basename을 그대로 둡니다.
- 그런데 `labhq/reproduction_receipt.py::_path`는 `[A-Za-z0-9_./ -]`만 받았습니다. 그래서 정상 Python source가 `redacted-…py`·`blocked_path`로 기록되고 문법 검사를 받지 못했습니다.
- 새 positive test 5개가 수정 전에 실패했습니다(점으로 시작하는 ASCII 이름은 원래 통과).

## 바꾼 것

- **받는 것:** NFC 유니코드와 일반 구두점 이름(괄호·쉼표·`+`·`;`·`@`·`#`·`№`·공백이 든 폴더 등).
- **계속 거부:** 절대·drive(`C:`)·UNC·backslash 경로, `..`·`.labhq`.
- **새로 거부:**
  - 제어(Cc)·서식(Cf, 예: RLO·zero-width)·surrogate·사설·미할당 문자, 줄·문단 구분 문자, 일반 공백이 아닌 공백(NBSP 등)
  - Windows 예약 문자(`:<>"|?*`)와 장치 이름(`CON`·`con.tar.py`·`LPT1` 등)
  - 점·공백으로 끝나거나 공백으로 시작하는 부분(Windows가 이름을 바꿈)
  - NFC가 아닌 표기(같은 이름의 두 철자), UTF-8 255 byte를 넘는 부분
- **ASCII 강화:** 장치 이름과 끝 점·공백은 ASCII에서도 전에는 받았는데, OS 경로 모호성이라 함께 막았습니다.
- **막힌 이름의 처리:** 지금처럼 redacted이고, staging·실행하지 않으며, 결과는 `blocked_path`/`blocked_syntax`로 남습니다(성공으로 쓰지 않음).
- **바꾸지 않은 것:** `_path`는 파일을 열지 않는 논리 경로 검사입니다. #781의 hard link·링크 폴더 보호, #771의 script별 산출 판정, #800의 `-E -s -B` 실행·`-I -S -B` 문법 검사, 입력·caller 환경 격리는 그대로입니다.

## 확인 (로컬 mock/static만)

- **수정 전:** 새 test에서 16 failed. 유니코드 positive 5개, ASCII 장치 이름·끝 점/공백 negative 9개, 끝단 test 2개입니다.
- **수정 뒤:** 가짜 `subprocess.run`으로 receipt mock test 61 passed, 2 skipped(symlink 권한, 실제 interpreter CI test).
  - 끝단 test는 유니코드 source를 published 이름 그대로 임시 폴더에 두고 문법 검사·실행 명령·예상 산출 경로를 확인합니다.
  - 막힌 이름 4개는 redacted, 미실행, 파일 미생성을 확인합니다.
- Python 3.10 문법, `check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것·한계

- **실제 interpreter·R·bash에 유니코드 이름 전달:** 로컬에서 실행하지 않았습니다. 실제 interpreter test는 CI에서만 돕니다.
- **NFD 이름:** macOS 등에서 NFD로 만들어진 이름은 `blocked_path`로 남습니다(보수적).
- #774는 별도입니다.
