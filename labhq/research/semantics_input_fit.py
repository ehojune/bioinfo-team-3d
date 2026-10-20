"""Optional observation adapter for the mainline CP1 evaluator.

The reviewed rules, aggregate API and meaning hash are shared; deleting this adapter
or the observer never removes CP1 validation.
"""
from .input_fit import (VERDICTS, evaluate, relation_table, reset_cache, table_sha256)
