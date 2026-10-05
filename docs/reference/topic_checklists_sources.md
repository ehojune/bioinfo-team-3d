# topic 점검표 근거

`labhq/vocab/topic_checklists.yaml`의 항목이 기대는 표준과 문헌이다. 찾아볼 때만 읽는다.
PMID는 2026-10-05에 PubMed에서 첫 저자·학술지·연도와 제목 단어를 대조해 확인했다.
처음 세 topic(`bulk_rna_seq`, `microarray_expression`, `single_cell_rna_seq`)은 PR #395의 모의 연구 기록에서 나왔고 이 표에 없다.

| topic | 근거 |
|---|---|
| spatial_transcriptomics | Svensson et al. 2018 Nat Methods, SpatialDE (PMID 29553579) · Cable et al. 2022 Nat Biotechnol, RCTD (PMID 33603203) |
| atac_seq | ENCODE ATAC-seq data standards (https://www.encodeproject.org/atac-seq/) · Amemiya et al. 2019 Sci Rep, ENCODE Blacklist (PMID 31249361) |
| chip_seq | Landt et al. 2012 Genome Res, ENCODE ChIP-seq guidelines (PMID 22955991) · Li et al. 2011 Ann Appl Stat, IDR (doi:10.1214/11-AOAS466, PubMed 미등재) |
| cut_and_run | Skene & Henikoff 2017 eLife, CUT&RUN (PMID 28079019) · Meers et al. 2019 Epigenetics Chromatin, SEACR (PMID 31300027) |
| dna_methylation | Krueger et al. 2012 Nat Methods (PMID 22290186) · Feng et al. 2014 Nucleic Acids Res, DSS (PMID 24561809) |
| germline_wgs_wes | Van der Auwera et al. 2013 Curr Protoc Bioinformatics, GATK best practices (PMID 25431634) |
| somatic_wgs_wes | Cibulskis et al. 2013 Nat Biotechnol, MuTect·panel of normals (PMID 23396013) · Costello et al. 2013 Nucleic Acids Res, 산화 인공물 (PMID 23303777). tumor-only의 집단 생식계열 필터(gnomAD)는 PR #402 리뷰로 더함 |
| rare_disease_genomics | Richards et al. 2015 Genet Med, ACMG/AMP (PMID 25741868) · Köhler et al. 2021 Nucleic Acids Res, HPO (PMID 33264411) |
| ont_long_read | Wick et al. 2019 Genome Biol, basecaller 비교 (PMID 31234903) |
| pacbio_long_read | Wenger et al. 2019 Nat Biotechnol, HiFi (PMID 31406327) |
| long_read_transcriptomics | Pardo-Palacios et al. 2024 Nat Methods, LRGASP (PMID 38849569) |
| metagenome_assembly | Bowers et al. 2017 Nat Biotechnol, MIMAG (PMID 28787424) · Parks et al. 2015 Genome Res, CheckM (PMID 25977477) |
| metagenomic_taxonomy | Salter et al. 2014 BMC Biol, 시약 오염 (PMID 25387460) · Gloor et al. 2017 Front Microbiol, 조성 자료 (PMID 29187837) |
| amplicon_sequencing | Callahan et al. 2016 Nat Methods, DADA2 (PMID 27214047) · Bokulich et al. 2016 mSystems, mockrobiota (PMID 27822553) |
| viral_genomics | Grubaugh et al. 2019 Genome Biol, iVar (PMID 30621750) · O'Toole et al. 2021 Virus Evol, Pangolin (PMID 34527285) |
| bacterial_genome_assembly | Jain et al. 2018 Nat Commun, FastANI (PMID 30504855) · Schwengers et al. 2021 Microb Genom, Bakta (PMID 34739369) |
| alternative_splicing | Shen et al. 2014 PNAS, rMATS (PMID 25480548) · Mehmood et al. 2020 Brief Bioinform, splicing 도구 비교 (PMID 31802105) |
