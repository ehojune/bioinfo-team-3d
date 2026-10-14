## 결론

Refs #584 (4번). 편집자 정리본에 `report.pdf`를 넣고, 정리본 안의 그림 링크가 정리본이 가진 파일만 가리키게 했습니다. PDF를 못 만들거나 크기 한도를 넘으면 PDF만 빼고 게시합니다. 통제 데이터 요청은 전과 같이 정리본 단계에서 멈춥니다.

## 바뀐 것

- **PDF 포함** (`labhq/result_storage.py`): 정리본을 다 쓴 뒤 `report_pdf.build_report_file`로 PDF를 만들어 `report.pdf`로 같은 폴더에 둡니다. 원본 묶음이 아니라 정리본 폴더의 가려 쓴 `report.md`와 정리본이 가진 그림에서 인쇄하고(`build_report_file(..., source=정리본 폴더)`, 캐시는 쓰지 않음), 부록은 넣지 않습니다. README가 `report.pdf`를 링크하고 게시 카드에 한 줄(`pdf`)이 보입니다.
- **건너뜀**: 브라우저가 없거나, 인쇄가 실패하거나, 만드는 중 오류가 나면 "PDF는 포함하지 않았어요: 이유" 한 줄을 README와 카드에 남기고 나머지는 그대로 게시합니다. 이유 문장은 고정 문구라 경로·오류 내용이 새지 않습니다.
- **크기 상한**: `github.edition_pdf_max_mb`(기본 20). 넘으면 같은 방식으로 건너뜁니다. 재개한 초안은 디스크의 PDF가 `%PDF-`로 시작하고 상한 안일 때만 올립니다.
- **업로드** (`integrations/github.py`): `put_binary`를 더하고 `put_image`가 이를 부릅니다(메시지만 다름). 이진 파일 규칙은 그림과 같습니다.
- **그림 링크** (`edition_figure_links`): 보고서의 `<workdir_id>/outputs/<파일>`을 정리본이 복사한 `steps/<step_id>/outputs/<파일>`로 고칩니다. 정리본에 없는 그림(미보고, 1 MB 초과, 형식 제외)이나 정리본 밖 경로는 `*(그림 생략: 설명)*`로 바꿉니다. 코드 안은 건드리지 않고, http(s)·data: 링크는 그대로 둡니다.
- **게시 카드**: `result_publish` 카드의 `pdf` 항목에 "보고서 PDF" 이름을 붙였습니다(`labhq/web/ui/decide.js`).
- **테스트 격리** (`tests/conftest.py`): 모든 test에서 `find_browser`를 None으로 둡니다. 정리본 test가 실제 Edge를 띄우고 있었습니다(기존 `test_new_repo_and_upload_only_after_review`가 PDF를 받아 실패해서 알았습니다).
- `docs/manual.md`(정리본 절, 설정 절)와 두 `labhq.example.yaml`.

## 확인

- 새 test 12건(`tests/test_result_storage_pdf.py`)과 Node 1건(`tests/web_result_storage.cjs`)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다. PDF 포함(빌더 mock), 브라우저 없음·인쇄 실패·오류·실제 빌더 무브라우저 건너뜀, 상한(경계 포함), 그림 링크 해소, 통제 데이터 요청 미게시, 재개한 초안의 PDF 업로드, PDF 아닌 파일 거부.
- 관련 Python test(정리본·늦은 저장·보고서 PDF·설정·요청 묶음·정책) 통과, Node test 전부 통과, `scripts/check_public.sh` 통과.
- 이 PC의 Edge로 한 번 직접 돌려 정리본에 실제 PDF(그림 포함 약 40 KB)가 들어가는 것을 확인했습니다.

## 남은 것

- 정리본의 그림 한도(파일당 1 MB, 합계 5 MB)는 그대로라서, 그보다 큰 그림은 정리본과 PDF 모두에서 빠지고 "그림 생략"으로 남습니다. 한도를 PDF용으로 따로 둘지는 PI 결정입니다.
- 원격(http) 그림 링크는 묶음 경로가 아니라서 그대로 둡니다.
