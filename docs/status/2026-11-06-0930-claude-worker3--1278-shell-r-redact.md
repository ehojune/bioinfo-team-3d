## 단계
#1278 남은 부분: 셸·R 코드의 비밀값 가리기에서 `TOKEN=abc'xyz'`처럼 조각을 붙여 쓴 값이 새던 부류.

## 한 일
- `labhq/portfolio.py`: 셸·R의 기본값을 뒤집었다. 위험해 보이는 것만 가리지 않고, 안전하다고 확실한 형태만 남기고 나머지는 모두 `"<redacted-secret>"`로 바꾼다.
- 셸(`_shell_word`): 값을 한 단어로 읽는다. `$VAR`·`${VAR}`·`$(...)`·역따옴표만으로 된 경우만 남긴다. 따옴표 문자열, 이스케이프, `${VAR:-x}`, 배열, 산술, 붙여 쓴 조각은 가린다. 빈 값과 `#` 주석은 값이 아니다.
- R(`_r_secret_spans`): 변수·`NULL`·`$`·`::` 참조, `Sys.getenv` 등 조회·읽기 함수, 문자열·숫자 인자가 없는 호출만 남긴다. 문자열·숫자·`paste0("a","b")`는 가린다. 이름 뒤 `=`는 `==` 비교와 구분한다.
- `scan_publish_files`는 같은 판정을 쓴다. Python의 `ast` 판정, 문서 규칙, `SECRET_PATTERNS`는 그대로다.
- 시험 39건 추가(`tests/test_result_storage_redact_1278.py`): 셸 12건·R 7건 가림, 셸 11건·R 9건 그대로.

## 확인
- Windows: 새 시험 포함 portfolio·result_storage 시험 166 passed.

## 하지 않은 것
- 변수 이름이 `GITHUB_TOKEN`처럼 단어 경계가 없는 경우는 이름 판정이 그대로라 읽지 않는다(실제 토큰 형식은 `SECRET_PATTERNS`가 잡는다).
- `$(...)` 안의 리터럴은 안전한 형태로 본다(지시).
