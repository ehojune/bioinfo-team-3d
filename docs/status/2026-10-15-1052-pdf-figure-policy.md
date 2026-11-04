## 결론

Refs #608. PI 결정(10-15)대로 보고서 PDF의 그림 정책을 바꿨습니다. PDF 그림은 크기 제한이 없고 늘 요청 묶음의 원본이며, PDF는 50 MB 안으로 그림 해상도를 줄여 맞춥니다. 그림은 11장까지이고 한 장은 graphical abstract입니다. 그림이 없던 요청도 PDF에 abstract가 생깁니다.

## 바뀐 것

- **그림 크기** (`labhq/report_pdf.py`): `FigureEmbedder`의 파일당 8 MB·합계 48 MB 제한을 없앴습니다. 정리본 PDF도 정리본의 1 MB·5 MB 복사본이 아니라 묶음 원본으로 인쇄합니다(`result_storage.pdf_report_text`가 정리본과 같은 가림을 거친 본문에 묶음 그림 링크를 답니다). 정리본 복사본의 한도는 그대로입니다.
- **크기 맞춤** (`print_within_cap`): 첫 인쇄가 `github.edition_pdf_max_mb`(기본 50, 최대 75)를 넘으면 브라우저가 그림을 JPEG로 다시 인코딩하는 판(긴 변 2600→600 px, 품질 0.85→0.35, 6단계)으로 다시 인쇄합니다. 맞을 때까지 한 단계씩 줄이고, 끝내 안 맞으면 가장 작은 PDF를 그림을 모두 넣은 채 두고 크기를 알립니다. 인쇄 시간 초과도 다음 단계로 넘깁니다. 새 의존성은 없습니다. 알림은 게시 카드·정리본 README·`X-Labhq-Reason`에 나옵니다. 캐시 키에 상한이 들어갑니다(`RENDER_VERSION` 2).
- **11장** (`report_figures.MAX_REPORT_FIGURES`): 임베더가 11장까지 넣고 나머지는 표지에 "넣지 못했어요"로 적습니다. 묶음 점검은 11장 초과를 문제로 적고, 링크한 그림을 누적 상한 너머로 지키는 수도 11장입니다(24에서 변경). 작성 지침 `REPORT_FIGURE_RULE`도 11장과 abstract 요청으로 바꿨습니다.
- **graphical abstract** (`labhq/report_abstract.py`): 보고서에 abstract 링크가 없으면 계획의 단계·입력 출처·보고서 결론으로 질문 → 데이터 → 방법·단계 → 핵심 결과 흐름도 SVG를 그려 PDF의 첫 그림, 정리본의 `graphical_abstract.svg`(README·`report.md`의 첫 그림)로 넣습니다. 사무실 색 토큰, 시스템 글꼴, 외부 글꼴·네트워크 없음, 모든 글자 이스케이프입니다. 정리본에서는 라벨에 정리본 문서와 같은 가림을 씁니다. 묶음에 없는 abstract 링크는 묶음·정리본·PDF에서 지웁니다.
- **업로드** (`integrations/github.py`): `put_binary`가 base64 크기를 100 MB 한도로 보내기 전에 검사하고 쓰기 제한 시간을 900초로 둡니다. `edition_pdf_max_mb` 최대를 90에서 75로 낮췄습니다. 그 한도를 넘는 PDF만 이유를 적고 뺍니다.
- `docs/manual.md`(보고서 PDF·정리본·설정 절), 두 `labhq.example.yaml`.

## 확인

- 새 test 28건(`tests/test_pdf_figure_policy.py`)은 main 코드에서 import 단계부터 실패하고 이 branch에서 통과합니다. 실제 Edge test 1건은 브라우저가 없으면 건너뜁니다.
- 의도해서 바꾼 기존 test: `test_report_pdf.py`(이미지 수, 8 MB 한도), `test_result_storage_pdf.py`(PDF 출처·상한·기본값·링크 수), `test_late_storage.py`(정리본 첫 줄). 프롬프트 hash pin은 바뀌지 않았습니다.
- 관련 Python test 1131건과 Node test 58개 파일 통과, `check_public.sh` 통과.
- 이 PC의 Edge로 직접 확인: 6.2 MB PNG 3장(첫 인쇄 19.5 MB)을 한도 2 MB로 돌리면 3.7 → 2.8 → 1.5 MB로 3단계에서 맞고 그림 3장이 모두 들어갑니다. 13.4 MB PNG 7장(첫 인쇄 98 MB)은 한도 50 MB에서 1단계에 19 MB가 됩니다.

## 남은 것

- 작성자(CSO)가 직접 만든 abstract 파일은 아직 쓰이지 않습니다. CSO의 기본 도구가 Read·Glob·Grep뿐이라 파일을 만들 수 없고, 합성 단계 작업 폴더는 묶음에 들어가지 않습니다. 단계가 `graphical_abstract.png|svg`를 산출물로 내서 보고서가 링크할 때만 그 파일을 쓰고, 나머지는 labhq가 그립니다. CSO에 쓰기 도구를 주고 합성 산출물을 묶음에 싣는 일은 보안 자세가 바뀌므로 PI 결정이 필요합니다.
- 전체 pytest는 CI에서 돌립니다.
