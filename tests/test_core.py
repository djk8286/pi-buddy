"""Tests that run without the Pi hardware: memory tool, history search, sentence splitting."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buddy.history import History  # noqa: E402
from buddy.memory import MemoryStore  # noqa: E402


def test_memory_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        m = MemoryStore(Path(d))
        assert "created" in m.handle({"command": "create", "path": "/memories/people/hannah.md",
                                      "file_text": "Partner\nLikes Disney\n"})
        out = m.handle({"command": "view", "path": "/memories"})
        assert "/memories/people/hannah.md" in out
        assert "edited" in m.handle({"command": "str_replace", "path": "/memories/people/hannah.md",
                                     "old_str": "Likes Disney", "new_str": "Loves Disney"})
        assert "Loves Disney" in m.handle({"command": "view", "path": "/memories/people/hannah.md"})
        assert "edited" in m.handle({"command": "insert", "path": "/memories/people/hannah.md",
                                     "insert_line": 0, "insert_text": "# Hannah"})
        assert m.handle({"command": "view", "path": "/memories/people/hannah.md",
                         "view_range": [1, 1]}).endswith("# Hannah")
        assert "renamed" in m.handle({"command": "rename", "old_path": "/memories/people/hannah.md",
                                      "new_path": "/memories/people/h.md"})
        assert "# Hannah" in m.dump_for_prompt()
        assert "deleted" in m.handle({"command": "delete", "path": "/memories/people/h.md"})


def test_memory_blocks_escape():
    with tempfile.TemporaryDirectory() as d:
        m = MemoryStore(Path(d) / "mem")
        for bad in ["/memories/../secret.txt", "/etc/passwd", "/memories/%2e%2e/x", "memories/x"]:
            out = m.handle({"command": "create", "path": bad, "file_text": "x"})
            assert out.startswith("Error"), (bad, out)
        assert not (Path(d) / "secret.txt").exists()
        assert m.handle({"command": "delete", "path": "/memories"}).startswith("Error")


def test_history_search():
    with tempfile.TemporaryDirectory() as d:
        h = History(Path(d) / "h.db")
        h.add("s1", "user", "My router keeps dropping the 5GHz band")
        h.add("s1", "assistant", "Try changing the channel.")
        h.add("s2", "user", "What's the weather?")
        assert "router" in h.search("router wifi", exclude_session="s2")
        assert "No matching" in h.search("router", exclude_session="s1")
        assert h.search("!!!") == "No search terms given."


def test_sentence_stream():
    from buddy.brain import SentenceStream
    s = SentenceStream()
    out = []
    for piece in ["[hap", "py] Morning, Dave! You've got", " a quiz today. Want **help**?"]:
        out += s.feed(piece)
    out += s.flush()
    assert out[0] == ("happy", "Morning, Dave!")
    assert out[1] == ("happy", "You've got a quiz today.")
    assert out[2] == ("happy", "Want help?")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
