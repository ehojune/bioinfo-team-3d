[← Manual index](../manual.en.md)

## Interpreter reservation accounting

Before dispatch, the interpreter records the task, attempt, ceiling and source in a separate ledger. The request bound sums the same task as the research hold only once and replaces it with the confirmed cost. A reservation interrupted before task registration is also preserved, and shown as a missing task in the `translation_reservations_v1` projection of `/task-attempts`. Per-task basis is not inferred from the existing aggregate reservation.
