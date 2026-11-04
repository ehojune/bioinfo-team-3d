"""Recognise explicit provider safety refusals without exposing provider text."""
import re

REASON = "모델 안전장치가 계획을 거부했습니다. 자동 재시도·모델 전환은 하지 않습니다."


class ModelRefused(Exception):
    def __init__(self, result):
        super().__init__(REASON)
        self.result = result


def is_model_refusal(result) -> bool:
    if result.error_kind == "model_refused":
        return True
    # Do not classify ordinary policy errors, research prose or quoted refusals.
    return any(re.match(r"\s*API Error:[^\n]{0,160}\bsafeguards flagged this session\b", value, re.I)
               for value in (result.error or "", result.text or ""))
