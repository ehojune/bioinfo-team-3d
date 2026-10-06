# topic 점검표 근거

`labhq/vocab/topic_checklists.yaml`의 항목이 기대는 표준과 문헌이다. 찾아볼 때만 읽는다.
PMID는 2026-10-05에 PubMed에서 첫 저자·학술지·연도와 제목 단어를 대조해 확인했다. #420으로 더한 23개 topic(`proteomics`부터)의 PMID는 2026-10-06에 NCBI esummary로 같은 대조를 스크립트로 했다.
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
| proteomics | Elias & Gygi 2007 Nat Methods, target-decoy (PMID 17327847) · Deutsch et al. 2019 J Proteome Res, HPP MS 해석 지침 3.0 (PMID 31599596) · Lazar et al. 2016 J Proteome Res, 결측값 (PMID 26906401) · Čuklina et al. 2021 Mol Syst Biol, batch 효과 (PMID 34432947) |
| proteomics_dia | Demichev et al. 2020 Nat Methods, DIA-NN (PMID 31768060) · Rosenberger et al. 2017 Nat Methods, DIA FDR (PMID 28825704) · Lazar et al. 2016 J Proteome Res, 결측값 (PMID 26906401) |
| single_cell_proteomics | Gatto et al. 2023 Nat Methods, SCP 권고안 (PMID 36864200) · Cheung et al. 2021 Nat Methods, carrier 한계 (PMID 33288958) · Vanderaa & Gatto 2021 Expert Rev Proteomics, SCP 재현 (PMID 34602016) |
| spatial_proteomics | Taube et al. 2020 J Immunother Cancer, SITC 다중 IHC/IF 검증 (PMID 32414858) · Hickey et al. 2022 Nat Methods, 다중 항체 영상 primer (PMID 34811556) · Greenwald et al. 2022 Nat Biotechnol, Mesmer 분할 (PMID 34795433) · Chevrier et al. 2018 Cell Syst, spillover 보정 (PMID 29605184) |
| metaproteomics | Muth et al. 2015 Proteomics, DB 검색 (PMID 25778831) · Van Den Bossche et al. 2021 Nat Commun, CAMPI (PMID 34911965) · Gurdeep Singh et al. 2019 J Proteome Res, Unipept 4.0 (PMID 30465426) |
| metabolomics | Broadhurst et al. 2018 Metabolomics, QC 시료 지침 (PMID 29805336) · Dunn et al. 2011 Nat Protoc (PMID 21720319) · Sumner et al. 2007 Metabolomics, MSI 보고 기준 (PMID 24039616) · Schymanski et al. 2014 Environ Sci Technol, 동정 확신 수준 (PMID 24476540) |
| spatial_metabolomics | Palmer et al. 2017 Nat Methods, FDR 통제 주석 (PMID 27842059) · Alexandrov 2012 BMC Bioinformatics, MALDI 영상 분석 (PMID 23176142) · McDonnell et al. 2015 Anal Bioanal Chem, MSI 보고 지침 (PMID 25432304) |
| gwas | Anderson et al. 2010 Nat Protoc, QC (PMID 21085122) · Price et al. 2006 Nat Genet, PCA (PMID 16862161) · Bulik-Sullivan et al. 2015 Nat Genet, LDSC (PMID 25642630) · Pe'er et al. 2008 Genet Epidemiol, 유의수준 (PMID 18348202) · NCI-NHGRI Working Group 2007 Nature, 재현 (PMID 17554299) |
| structural_variant_calling | Zook et al. 2020 Nat Biotechnol, GIAB SV benchmark (PMID 32541955) · Kosugi et al. 2019 Genome Biol, caller 비교 (PMID 31159850) · Kirsche et al. 2023 Nat Methods, Jasmine 병합 (PMID 36658279) · Mahmoud et al. 2019 Genome Biol (PMID 31747936) |
| de_novo_genome_assembly | Rhie et al. 2021 Nature, VGP 기준 (PMID 33911273) · Manni et al. 2021 Mol Biol Evol, BUSCO (PMID 34320186) · Rhie et al. 2020 Genome Biol, Merqury (PMID 32928274) · Astashyn et al. 2024 Genome Biol, FCS-GX (PMID 38409096) · Challis et al. 2020 G3, BlobToolKit (PMID 32071071) |
| genome_annotation | Yandell & Ence 2012 Nat Rev Genet (PMID 22510764) · Flynn et al. 2020 PNAS, RepeatModeler2 (PMID 32300014) · Gabriel et al. 2024 Genome Res, BRAKER3 (PMID 38866550) · Holt & Yandell 2011 BMC Bioinformatics, MAKER2·AED (PMID 22192575) · Manni et al. 2021 Mol Biol Evol, BUSCO (PMID 34320186) |
| pangenomics | Hickey et al. 2024 Nat Biotechnol, Minigraph-Cactus (PMID 37165083) · Garrison et al. 2024 Nat Methods, PGGB (PMID 39433878) · Liao et al. 2023 Nature, HPRC (PMID 37165242) · Tonkin-Hill et al. 2020 Genome Biol, Panaroo (PMID 32698896) |
| phylogenetics | Katoh & Standley 2013 Mol Biol Evol, MAFFT (PMID 23329690) · Capella-Gutiérrez et al. 2009 Bioinformatics, trimAl (PMID 19505945) · Kalyaanamoorthy et al. 2017 Nat Methods, ModelFinder (PMID 28481363) · Hoang et al. 2018 Mol Biol Evol, UFBoot2 (PMID 29077904) · Croucher et al. 2015 Nucleic Acids Res, Gubbins (PMID 25414349) |
| hi_c | Lajoie et al. 2015 Methods, Hi-C 지침 (PMID 25448293) · Imakaev et al. 2012 Nat Methods, ICE (PMID 22941365) · Rao et al. 2014 Cell, 해상도 정의 (PMID 25497547) · Yang et al. 2017 Genome Res, HiCRep (PMID 28855260) · Yardımcı et al. 2019 Genome Biol, 재현성 (PMID 30890172) |
| spatial_epigenomics | Deng et al. 2022 Nature, spatial-ATAC-seq (PMID 35978191) · Llorens-Bobadilla et al. 2023 Nat Biotechnol, spatial ATAC (PMID 36604544) · Svensson et al. 2018 Nat Methods, SpatialDE (PMID 29553579) |
| multi_omics_integration | Argelaguet et al. 2018 Mol Syst Biol, MOFA (PMID 29925568) · Cantini et al. 2021 Nat Commun, 통합 방법 benchmark (PMID 33402734) · Singh et al. 2019 Bioinformatics, DIABLO (PMID 30657866) |
| single_cell_multiome | Hao et al. 2021 Cell, WNN (PMID 34062119) · Stuart et al. 2021 Nat Methods, Signac (PMID 34725479) · Mulè et al. 2022 Nat Commun, dsb (PMID 35440536) · Squair et al. 2021 Nat Commun, pseudobulk (PMID 34584091) · Ma et al. 2020 Cell, SHARE-seq peak–유전자 연결 (PMID 33098772) |
| spatial_multiomics | Zeira et al. 2022 Nat Methods, PASTE 절편 정합 (PMID 35577957) · Svensson et al. 2018 Nat Methods, SpatialDE (PMID 29553579) |
| perturb_seq | Dixit et al. 2016 Cell, Perturb-seq (PMID 27984732) · Replogle et al. 2022 Cell, genome-scale Perturb-seq (PMID 35688146) · Papalexi et al. 2021 Nat Genet, Mixscape (PMID 33649593) |
| immune_repertoire_sequencing | Shugay et al. 2014 Nat Methods, MIGEC (PMID 24793455) · Vander Heiden et al. 2014 Bioinformatics, pRESTO (PMID 24618469) · Rubelt et al. 2017 Nat Immunol, AIRR 권고 (PMID 29144493) · Vander Heiden et al. 2018 Front Immunol, AIRR 표준 형식 (PMID 30323809) · Kaplinsky & Arnaout 2016 Nat Commun, 다양성 추정 (PMID 27302887) |
| metagenomic_functional_profiling | Beghini et al. 2021 eLife, bioBakery 3·HUMAnN 3 (PMID 33944776) · Gloor et al. 2017 Front Microbiol, 조성 자료 (PMID 29187837) |
| metagenome_binning | Bowers et al. 2017 Nat Biotechnol, MIMAG (PMID 28787424) · Chklovski et al. 2023 Nat Methods, CheckM2 (PMID 37500759) · Sieber et al. 2018 Nat Microbiol, DAS Tool (PMID 29807988) · Meyer et al. 2022 Nat Methods, CAMI II (PMID 35396482) · Olm et al. 2017 ISME J, dRep (PMID 28742071) · Chaumeil et al. 2022 Bioinformatics, GTDB-Tk v2 (PMID 36218463) |
| metatranscriptomics | Kopylova et al. 2012 Bioinformatics, SortMeRNA (PMID 23071270) · Shakya et al. 2019 Front Genet (PMID 31608125) · Zhang et al. 2021 Bioinformatics, 차등 발현 통계 (PMID 34252963) · Klingenberg & Meinicke 2017 PeerJ, 정규화 (PMID 29062598) |
