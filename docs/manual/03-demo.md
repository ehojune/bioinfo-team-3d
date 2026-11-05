[← 매뉴얼 목차](../manual.md)

## 둘러보기: mock 데모

API 키·클러스터 없이 돌아갑니다.

```bash
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\labhq demo --web
```

POSIX에서는 `python3 -m venv .venv`, `./.venv/bin/python -m pip install -e ".[dev]"`, `./labhq.sh demo --web`을 씁니다. 폰은 `--phone`을 더합니다.
실제 사무실이 떠 있어도 데모는 다른 빈 port를 고릅니다. 출력된 데모 주소만 열면 `데모(mock)` 표시와 안내 띠가 보입니다.
웹 요청은 약 15~30초에 걸쳐 진행되고 다음 요청까지 남은 시간을 보여 줍니다. 폰 승인을 기다리는 시간은 별도이며 실제 결정 기록과 섞이지 않습니다.
게이트웨이 없이 UI만 보려면 `python -m http.server --directory labhq/web 8000` 뒤 `http://127.0.0.1:8000/?demo=1`을 엽니다.
