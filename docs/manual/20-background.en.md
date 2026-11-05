[← Manual index](../manual.en.md)

## Background: The Virtual Biotech

The structure labhq was modeled on.

[The Virtual Biotech](https://doi.org/10.1126/science.aeg6779) from the Zou lab at Stanford (Science 2026; among the authors, Jiacheng Miao is first author of Paper2Agent) is
a structure of **4 divisions · 11 agents · over 100 tools** modeled directly on a real biotech organization.

**Leadership**
- **Virtual CSO** — does not analyze directly. Knows what to ask, whom to ask, and how to integrate evidence from several layers.
  Asks the user back about intent before expensive analysis.
- **Chief of staff** — while the CSO talks with the user, briefs in parallel on field trends, data status and recent announcements through web search.
- **Science reviewer** — evaluates question, evidence and thoroughness and requests revisions.

**Divisions**: target discovery and prioritization / target safety / modality selection / Clinical Officers

**Scientist agents appearing in the text**: statistical genetics, single-cell (atlas), functional genomics, biological pathway,
target biologist, pharmacologist, clinical-trial specialist

**What to learn from how it operates**
- Each agent is given **only a subset** of MCP tools that fits its specialty (separation of duties)
- Massive parallelism: about 37,000 clinical-trial specialist agents were launched to extract 56,000 Phase II/III clinical trials **while recording sources**
- The UI offers the reasoning process, tools in use, and downloads of data, code and reports (auditability)
- Cost of about $46–54 per case, under a day

### What changed in moving to our lab

| Virtual Biotech | labhq | Reason |
|---|---|---|
| 4 drug-discovery divisions | The duties of a one-PI bioinformatics lab (biology, data, literature, analysis, coding, QC) | The domain is different |
| About 100 prebuilt custom MCPs | Regular staff use the CLI's default tools + `labhq_hpc`, and missing methods are **topped up as needed by contract staff (paper MCP)** | Tools cannot all be built in advance |
| Single vendor | Vendor and model chosen per staff member, **reviewer from a different vendor** | To prevent the same model's shared blind spots |
| API calls from the cloud | A local runner submits to HPC (SGE/PBS/Slurm) → sleep → wake | Large and controlled-access data never leave the cluster |
| — | Phone approval gate, budget cap, data zones, lab notebook | A form one person can supervise |
