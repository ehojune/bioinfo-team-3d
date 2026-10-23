# #833 Windows 필수 웹 검사 시간 계약

- [#876 Windows CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39897352658/job/120230799229), [#880 Windows CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39898639671/job/120234981537): Chrome가 생성됐지만 약 18~19초 동안 DevTools 포트가 나타나지 않아 UI 검사는 시작하지 못했다. 제품 결함 여부는 미확인이다.
- 총괄 판단으로 기존 viewport별 합산 30초를 60초로 바꾼다. Node 시작과 정리 8초를 포함하며 Chrome 시작은 최대 30초, UI 검사는 시작 뒤 최대 20초다. 실제 브라우저와 desktop·phone 단언, 실패 시 정리, Windows 필수 게이트는 그대로 요구한다. 재시도·워밍업·skip은 없다.
- 가짜 시계·자원과 정적 검사: Node #833 13건, Python CI 계약 57건 통과. 실제 Windows CI, 총 10분 목표, 제품 원인은 새 head에서 확인해야 한다.
