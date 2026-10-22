# topic별 근거 논문

topic 43개마다 분석 방법·표준·벤치마크를 뒷받침하는 논문 목록이다. 찾아볼 때만 읽는다.

- 범위: PI 결정(2026-10-07, #446)에 따른 topic당 30편 목표를 채웠다. 한 논문이 여러 topic의 근거가 될 수 있어 topic별 수를 더하면 전체 편수보다 크다.
- 전체 1286편, 그중 429편이 두 topic 이상에 걸린다. topic별 최소 30편, 최대 82편. 30편 미만 topic은 없다.
- 검증: 기존 1255편은 #455에서 NCBI esummary 제목을 검증했다. 추가 31편은 2026-10-07에 `verify_sources.py`로 31행 한 묶음을 검증해 모두 통과했다([추가 검증표](topic_papers_added_2026-10-07.verified.tsv)). 새 행의 저자·연도·저널·제목도 NCBI metadata에서 가져왔다.
- 파일: [topic_papers.tsv](topic_papers.tsv) — `pmid, first_author, year, journal, kind, topics, title, why`. 첫 topic 순, 같은 topic 안에서는 최신 연도 순이다.

## 토픽 발굴·선정과 논문 수집

여기서 topic은 RNA-seq·GWAS 같은 분석 분야다.

- **발굴 기준**: 리뷰·대표 논문, 교과서, 기술 블로그, 공식 튜토리얼, 학회 주제에서 후보를 모아 기존 분류·EDAM과 대조해 합치거나 나누는 절차를 정했다([#420](https://github.com/ehojune/bioinfo-team-3d/issues/420)).
- **선정**: 초기 모의 연구의 3개 분야에 PI가 검토한 17개([#402](https://github.com/ehojune/bioinfo-team-3d/pull/402))·23개([#446](https://github.com/ehojune/bioinfo-team-3d/pull/446))를 더했다. 대표 논문의 Methods·부록과 표준·지침에서 공통 분석을 뽑아 필수·권장 점검으로 정리했다([점검표 근거](topic_checklists_sources.md)).
- **논문 선정·보강**: 당시 43개 topic별로 방법·리뷰·벤치마크·대표 연구·프로토콜·지침을 찾아 종류와 선정 이유(`kind`, `why`)를 남겼다. 같은 PMID는 합치고 여러 topic을 연결했다. 우선 분야마다 10편 이상을 모은 뒤, 30편 미만인 6개 분야에 31편을 보강했다([#455](https://github.com/ehojune/bioinfo-team-3d/pull/455), [#462](https://github.com/ehojune/bioinfo-team-3d/pull/462)).
- **범위**: 이 목록은 당시 43개 분야 기준으로, 현재 점검표의 45개 분야와 범위가 다르다. 서지정보 대조는 논문 전문 독해나 논문별 품질 평가를 뜻하지 않는다. 전체 검색식·제외 기록을 갖춘 체계적 문헌고찰도 아니다.

## topic별 편수

| topic | 편수 |
|---|---:|
| `alternative_splicing` | 30 |
| `amplicon_sequencing` | 30 |
| `atac_seq` | 55 |
| `bacterial_genome_assembly` | 31 |
| `bulk_rna_seq` | 42 |
| `chip_seq` | 38 |
| `cut_and_run` | 35 |
| `de_novo_genome_assembly` | 58 |
| `dna_methylation` | 51 |
| `genome_annotation` | 47 |
| `germline_wgs_wes` | 62 |
| `gwas` | 44 |
| `hi_c` | 54 |
| `immune_repertoire_sequencing` | 36 |
| `long_read_transcriptomics` | 30 |
| `metabolomics` | 54 |
| `metagenome_assembly` | 30 |
| `metagenome_binning` | 33 |
| `metagenomic_functional_profiling` | 30 |
| `metagenomic_taxonomy` | 39 |
| `metaproteomics` | 30 |
| `metatranscriptomics` | 30 |
| `microarray_expression` | 30 |
| `multi_omics_integration` | 34 |
| `ont_long_read` | 58 |
| `pacbio_long_read` | 62 |
| `pangenomics` | 31 |
| `perturb_seq` | 34 |
| `phylogenetics` | 30 |
| `proteomics` | 60 |
| `proteomics_dia` | 64 |
| `rare_disease_genomics` | 49 |
| `single_cell_multiome` | 46 |
| `single_cell_proteomics` | 33 |
| `single_cell_rna_seq` | 53 |
| `somatic_wgs_wes` | 50 |
| `spatial_epigenomics` | 37 |
| `spatial_metabolomics` | 43 |
| `spatial_multiomics` | 44 |
| `spatial_proteomics` | 73 |
| `spatial_transcriptomics` | 82 |
| `structural_variant_calling` | 56 |
| `viral_genomics` | 30 |

## 종류별 편수

| kind | 편수 |
|---|---:|
| method | 677 |
| review | 179 |
| benchmark | 161 |
| landmark | 146 |
| protocol | 68 |
| guideline | 55 |
