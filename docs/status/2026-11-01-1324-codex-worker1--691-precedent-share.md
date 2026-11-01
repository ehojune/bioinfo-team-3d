# #691 precedent lookup share

- A configured request cap reserves capacity for later stages: briefing and precedent share a cap that defaults to floor(cap × 0.5); `null` disables its share limit.
- Runner-issued task identity supplies the stage; the broker ignores a caller-supplied stage. Existing rows without stage still count toward the total only.
- YAML and staff copies preserve decimal share precision; cap arithmetic uses exact integer ratios.
- Local regression: 12 passed in test_external_lookup_flow_676.py, including YAML 0.29999999999999999 → 29, staff copy, mixed-stage and decimal boundaries. Real PI requests and deployment were not run.
