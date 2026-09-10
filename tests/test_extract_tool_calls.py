import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _extractor_module():
    module_path = PROJECT_ROOT / "tools" / "extract_tool_calls.py"
    spec = importlib.util.spec_from_file_location("extract_tool_calls", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extracted_markdown_keeps_session_and_message_role() -> None:
    extractor = _extractor_module()
    session_key = "agent:audit:openresponses-user:docguard:task:task-1:attempt:try-1:agent:content"
    records = extractor.collect_content_items(
        iter(
            [
                {
                    "seq": 7,
                    "ts": "2026-08-27T00:00:00Z",
                    "sessionKey": session_key,
                    "sessionId": "session-1",
                    "data": {
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "审核完成"}],
                        }
                    },
                }
            ]
        )
    )

    markdown = extractor.render_markdown(records, Path("/tmp/events.jsonl"), "消息内容明细")

    assert records[0]["session_key"] == session_key
    assert records[0]["role"] == "assistant"
    assert f"- Session Key：`{session_key}`" in markdown
    assert "- Session ID：`session-1`" in markdown
    assert "- 角色：`assistant`" in markdown
