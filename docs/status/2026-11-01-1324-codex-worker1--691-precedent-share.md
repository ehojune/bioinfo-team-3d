# #691 precedent lookup share

- A configured request cap reserves capacity for later stages: briefing and precedent share a cap that defaults to floor(cap × 0.5); `null` disables its share limit.
- Runner-issued task identity supplies the stage; the broker ignores a caller-supplied stage. Existing rows without stage still count toward the total only.
- Local regression: 11 passed in test_external_lookup_flow_676.py, including mixed-stage and decimal boundaries. Real PI requests and deployment were not run.
