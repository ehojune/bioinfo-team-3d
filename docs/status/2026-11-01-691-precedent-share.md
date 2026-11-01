# #691 precedent lookup share

- A configured request cap reserves capacity for later stages: precedent defaults to floor(cap × 0.5); `null` disables its share limit.
- Runner-issued task identity supplies the stage; the broker ignores a caller-supplied stage. Existing rows without stage still count toward the total only.
- Local regression: 69 passed across lookup admission, broker, runner, live source and prompt flows. Real PI requests and deployment were not run.
