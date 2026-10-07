# topic별 근거 논문

topic 43개마다 분석 방법·표준·벤치마크를 뒷받침하는 논문 목록이다. 찾아볼 때만 읽는다.

- 범위: PI 결정(2026-10-07, #446)에 따른 topic당 30편 목표를 채웠다. 한 논문이 여러 topic의 근거가 될 수 있어 topic별 수를 더하면 전체 편수보다 크다.
- 전체 1286편, 그중 429편이 두 topic 이상에 걸린다. topic별 최소 30편, 최대 82편. 30편 미만 topic은 없다.
- 만든 방법: topic마다 문헌 검색으로 후보를 모으고 같은 PMID는 한 행으로 합쳐 topic을 이었다.
- 검증: 기존 1255편은 #455에서 NCBI esummary 제목을 검증했다. 추가 31편은 2026-10-07에 `verify_sources.py`로 31행 한 묶음을 검증해 모두 통과했다([추가 검증표](topic_papers_added_2026-10-07.verified.tsv)). 새 행의 저자·연도·저널·제목도 NCBI metadata에서 가져왔다.
- 파일: [topic_papers.tsv](topic_papers.tsv) — `pmid, first_author, year, journal, kind, topics, title, why`. 첫 topic 순, 같은 topic 안에서는 최신 연도 순이다.

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
