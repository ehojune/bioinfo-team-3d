## 2026-10-08 · 웹·CLI에서 작업 종류(자동·간단·연구)를 PI가 고름 (PI 점검 R16)

- 결론: 연구 lane을 켜면 CP1·CP2를 거칠지는 접수 규칙만으로 정해졌다. 웹 시운전에서 메타데이터 표 요청이 CP1과 5단계 연구 계획으로 간 일이 있었다(규칙은 #498에서 고침). 이제 PI가 웹 입력창 옆 **작업 종류**(자동 판단·간단한 일·연구)나 `labhq send --work-kind simple|research`로 직접 고른다. 연구를 직원 한 명에게 직접 보내는 조합은 웹·CLI 모두 보내기 전에 막고 안내한다. 메모 모드에서는 선택이 숨는다.
- 바뀐 것: `labhq/web/index.html`(선택·전송·메모 모드), `labhq/cli.py`(`send --work-kind`), `docs/manual.md`(웹 설명과 CLI 예), 시험 `tests/web_work_kind.cjs`·`tests/test_intake_references.py` 1건.
- 실행한 것: 웹·CLI 관련 시험 163 passed, 전체 pytest(Windows) 4793 passed·59 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 3D 사무실에는 따로 입력창이 없어 해당 없음.
- 근거: `tests/web_work_kind.cjs`, `tests/test_intake_references.py::test_cli_send_passes_the_work_kind_and_refuses_research_to_one_agent`.
