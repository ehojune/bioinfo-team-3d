[← Manual index](../manual.en.md)

## Publishing to your own repository

A Windows selection refusal is shown with a dedicated error. If a prerequisite step fails, the coverage report is skipped and diagnostic artifacts are preserved. A successful run goes through both coverage and receipt verification.

Development CI reduces only prose with identical protected examples and markup, and only specified records. For the manual, development instructions and policy, only sentence-boundary line breaks with the same content and structure are candidates. Reuse on main needs the same tree, workflow and policy, coverage, and success evidence of the required gates. Linux 3.10/3.12 and the Windows nightly and workflow_dispatch are exhaustive. The routine Windows run applies one required OS-boundary check and a 10-minute job cap. If optional evidence is wrong, it fails fast and does not widen to exhaustive. A timeout is also a failure. The clean-install, official validator and MCP gates are kept ([judgment and evidence boundary](../reference/ci-reuse.md)). The required 3D screen check gives each screen up to 30 seconds for browser start and up to 20 seconds for the UI check, and finishes within 60 seconds in total including cleanup.

```bash
./scripts/publish_github.sh          # create the public repository <your account>/labhq with the gh CLI + push
./scripts/check_public.sh            # pre-publication check: secrets, real config files, large and genomic data files
```
