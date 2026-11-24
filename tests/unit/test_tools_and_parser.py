import pytest

from app.core.enums import ToolRisk
from app.core.exceptions import ToolPermissionError
from app.providers.json_parser import extract_json_object, parse_model
from app.runtime.contracts import GroundedAnswer
from app.tools.base import Tool
from app.tools.permissions import assert_permitted


class _Dummy(Tool):
    name = "cancel_order"
    description = "x"
    risk = ToolRisk.DESTRUCTIVE
    input_model = GroundedAnswer


def test_destructive_requires_confirmation() -> None:
    with pytest.raises(ToolPermissionError):
        assert_permitted(_Dummy(), confirmed=False)
    assert_permitted(_Dummy(), confirmed=True)


def test_json_parser_strips_fence() -> None:
    text = """当然可以：
```json
{"text":"退款申请尚未到账。","evidence_ids":[],"needs_review":true}
```
"""
    parsed = parse_model(text, GroundedAnswer)
    assert parsed.needs_review


def test_json_parser_rejects_garbage() -> None:
    with pytest.raises(Exception):
        extract_json_object("sorry I cannot")
