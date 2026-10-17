"""Unit tests for asset_core (no network, no bpy)."""

from asset_core.session import ChatSession, build_messages
from asset_core.transport import (
    BackendConfig,
    TransportError,
    parse_ollama_lines,
    parse_sse_lines,
)
from asset_core.worker import JsonlReader, JsonlWriter


# -- transport line parsers --------------------------------------------------


def test_parse_ollama_lines_yields_content():
    lines = [
        '{"message": {"role": "assistant", "content": "Прив"}, "done": false}',
        '{"message": {"role": "assistant", "content": "ет"}, "done": false}',
        '{"done": true, "total_duration": 123}',
    ]
    assert "".join(parse_ollama_lines(lines)) == "Привет"


def test_parse_ollama_lines_raises_on_error_payload():
    lines = ['{"error": "model not found"}']
    try:
        "".join(parse_ollama_lines(lines))
    except TransportError as exc:
        assert "model not found" in str(exc)
    else:
        raise AssertionError("TransportError not raised")


def test_parse_ollama_lines_ignores_garbage():
    lines = ["not json", "", '{"message": {"content": "ok"}}']
    assert "".join(parse_ollama_lines(lines)) == "ok"


def test_parse_sse_lines_yields_delta_content():
    lines = [
        "data: {\"choices\": [{\"delta\": {\"content\": \"hello\"}}]}",
        "",
        "data: {\"choices\": [{\"delta\": {\"content\": \" world\"}}]}",
        "data: [DONE]",
        "data: {\"choices\": [{\"delta\": {\"content\": \"ignored\"}}]}",
    ]
    assert "".join(parse_sse_lines(lines)) == "hello world"


def test_parse_sse_lines_handles_error_object():
    lines = ['data: {"error": {"message": "bad key"}}']
    try:
        "".join(parse_sse_lines(lines))
    except TransportError as exc:
        assert "bad key" in str(exc)
    else:
        raise AssertionError("TransportError not raised")


# -- config ------------------------------------------------------------------


def test_backend_config_from_dict_ignores_unknown_keys():
    cfg = BackendConfig.from_dict(
        {"kind": "openai", "host": "https://x/v1", "model": "m", "future": 1}
    )
    assert cfg.kind == "openai"
    assert cfg.host == "https://x/v1"
    assert cfg.model == "m"
    assert not hasattr(cfg, "future")


def test_backend_config_roundtrip():
    cfg = BackendConfig(kind="ollama", model="qwen", temperature=0.2)
    assert BackendConfig.from_dict(cfg.to_dict()) == cfg


# -- session / history trimming ----------------------------------------------


def test_build_messages_keeps_system_and_newest():
    history = [
        {"role": "user", "content": f"msg {i} " + "x" * 500}
        for i in range(20)
    ]
    messages = build_messages(history, "SYSTEM", max_chars=2000)
    assert messages[0] == {"role": "system", "content": "SYSTEM"}
    # newest message always survives
    assert messages[-1]["content"].startswith("msg 19")
    # budget respected (with 2-message floor slack)
    total = sum(len(m["content"]) for m in messages)
    assert total <= 2000 + 600


def test_build_messages_empty_history_is_system_only():
    assert build_messages([], "SYSTEM") == [{"role": "system", "content": "SYSTEM"}]


def test_chat_session_roundtrip():
    session = ChatSession()
    session.add("user", "вопрос")
    session.add("assistant", "ответ")
    messages = session.build("SYSTEM")
    assert [m["role"] for m in messages] == ["system", "user", "assistant"]
    session.clear()
    assert session.messages == []


# -- sidecar file protocol ----------------------------------------------------


def test_jsonl_writer_reader_roundtrip(tmp_path):
    events = tmp_path / "events.jsonl"
    writer = JsonlWriter(events)
    writer.emit({"type": "chunk", "text": "a"})
    writer.emit({"type": "chunk", "text": "b"})

    reader = JsonlReader(events)
    first = reader.read_new()
    assert [e["text"] for e in first] == ["a", "b"]
    assert reader.read_new() == []  # nothing new

    writer.emit({"type": "done"})
    assert reader.read_new() == [{"type": "done"}]
    writer.close()


def test_jsonl_reader_tolerates_torn_line(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_bytes(b'{"type": "chunk", "text": "ok"}\n{"type": "tru')
    reader = JsonlReader(events)
    assert reader.read_new() == [{"type": "chunk", "text": "ok"}]

    # the writer finishes the torn line later
    with open(events, "ab") as handle:
        handle.write(b'nc"}\n')
    assert reader.read_new() == [{"type": "trunc"}]


def test_jsonl_reader_missing_file(tmp_path):
    assert JsonlReader(tmp_path / "nope.jsonl").read_new() == []
