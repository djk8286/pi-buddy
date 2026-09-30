"""Long-term memory: a folder of text files Claude reads and writes through the memory tool.

Claude sends commands like {"command": "create", "path": "/memories/people/hannah.md", ...}.
We map /memories/... onto data/memories/ on the Pi and refuse anything that escapes it.
"""
import shutil
from pathlib import Path

from .config import MEMORY_DIR

TOOL_SPEC = {"type": "memory_20250818", "name": "memory"}
MAX_PROMPT_CHARS = 12000  # how much memory text to preload into the system prompt


class MemoryError_(Exception):
    pass


class MemoryStore:
    def __init__(self, base: Path = MEMORY_DIR):
        self.base = Path(base).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    # ---------- path safety ----------
    def _resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.startswith("/memories"):
            raise MemoryError_(f"Error: path must start with /memories, got {path!r}")
        if "%2e" in path.lower() or "\\" in path:
            raise MemoryError_("Error: invalid path")
        rel = path[len("/memories"):].lstrip("/")
        target = (self.base / rel).resolve()
        try:
            target.relative_to(self.base)
        except ValueError:
            raise MemoryError_("Error: path escapes the memory directory")
        return target

    @staticmethod
    def _numbered(text: str, start: int = 1) -> str:
        return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(text.split("\n"), start))

    # ---------- commands ----------
    def handle(self, cmd: dict) -> str:
        try:
            op = cmd.get("command")
            fn = getattr(self, f"_cmd_{op}", None)
            if fn is None:
                return f"Error: unknown command {op!r}"
            return fn(cmd)
        except MemoryError_ as e:
            return str(e)
        except Exception as e:  # never crash the conversation over a file error
            return f"Error: {e}"

    def _cmd_view(self, cmd):
        path = cmd.get("path", "/memories")
        target = self._resolve(path)
        if not target.exists():
            return f"The path {path} does not exist. Please provide a valid path."
        if target.is_dir():
            lines = [f"{_size(target)}\t{path}"]
            for p in sorted(target.rglob("*")):
                rel = p.relative_to(target)
                if len(rel.parts) > 2 or any(part.startswith(".") for part in rel.parts):
                    continue
                lines.append(f"{_size(p)}\t{path.rstrip('/')}/{rel.as_posix()}")
            return ("Here're the files and directories up to 2 levels deep in "
                    f"{path}, excluding hidden items and node_modules:\n" + "\n".join(lines))
        text = target.read_text()
        start = 1
        rng = cmd.get("view_range")
        if rng:
            lines = text.split("\n")
            s, e = int(rng[0]), int(rng[1])
            e = len(lines) if e == -1 else e
            text = "\n".join(lines[s - 1:e])
            start = s
        return f"Here's the content of {path} with line numbers:\n" + self._numbered(text, start)

    def _cmd_create(self, cmd):
        path = cmd["path"]
        target = self._resolve(path)
        if target.exists() and target.is_dir():
            return f"Error: {path} is a directory"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(cmd.get("file_text", ""))
        return f"File created successfully at: {path}"

    def _cmd_str_replace(self, cmd):
        path = cmd["path"]
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: The path {path} does not exist. Please provide a valid path."
        text = target.read_text()
        old, new = cmd["old_str"], cmd.get("new_str", "") or ""
        count = text.count(old)
        if count == 0:
            return f"No replacement was performed, old_str `{old}` did not appear verbatim in {path}."
        if count > 1:
            return f"No replacement was performed. Multiple occurrences of old_str `{old}`. Please ensure it is unique"
        text = text.replace(old, new, 1)
        target.write_text(text)
        return "The memory file has been edited.\n" + self._numbered(text)

    def _cmd_insert(self, cmd):
        path = cmd["path"]
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: The path {path} does not exist"
        lines = target.read_text().split("\n")
        n = int(cmd["insert_line"])
        if n < 0 or n > len(lines):
            return f"Error: Invalid `insert_line` parameter: {n}. It should be within the range of lines of the file: [0, {len(lines)}]"
        new_lines = cmd.get("insert_text", "").rstrip("\n").split("\n")
        lines[n:n] = new_lines
        target.write_text("\n".join(lines))
        return f"The file {path} has been edited."

    def _cmd_delete(self, cmd):
        path = cmd["path"]
        target = self._resolve(path)
        if target == self.base:
            return "Error: cannot delete the /memories directory itself"
        if not target.exists():
            return f"Error: The path {path} does not exist"
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return f"Successfully deleted {path}"

    def _cmd_rename(self, cmd):
        old, new = cmd["old_path"], cmd["new_path"]
        src, dst = self._resolve(old), self._resolve(new)
        if not src.exists():
            return f"Error: The path {old} does not exist"
        if dst.exists():
            return f"Error: The destination {new} already exists"
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return f"Successfully renamed {old} to {new}"

    # ---------- preload for the system prompt ----------
    def dump_for_prompt(self) -> str:
        """All memory files concatenated, so Claude starts each conversation already knowing them."""
        parts, total = [], 0
        files = sorted(p for p in self.base.rglob("*") if p.is_file() and not p.name.startswith("."))
        for p in files:
            rel = "/memories/" + p.relative_to(self.base).as_posix()
            body = p.read_text(errors="replace").strip()
            chunk = f"--- {rel} ---\n{body}\n"
            if total + len(chunk) > MAX_PROMPT_CHARS:
                parts.append(f"--- {rel} --- (not preloaded; use the memory tool to view it)\n")
                continue
            parts.append(chunk)
            total += len(chunk)
        return "\n".join(parts) if parts else "(no memories saved yet)"


def _size(p: Path) -> str:
    if p.is_file():
        n = p.stat().st_size
    else:
        n = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    for unit in ("B", "K", "M"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


MEMORY_INSTRUCTIONS = """
LONG-TERM MEMORY
You have a persistent memory folder at /memories that survives restarts. Its current contents are
preloaded below, so you do not need to view it before answering. Use the `memory` tool to keep it current:
- Save durable facts {user} tells you: people, preferences, routines, projects, deadlines, decisions.
- Organize by subject, one file per subject, e.g. /memories/people/hannah.md, /memories/projects/homelab.md,
  /memories/preferences.md, /memories/todo.md. Update existing lines instead of duplicating them.
- Remove things that are no longer true. Don't save small talk or things that expire today.
- When you save something, you can mention it briefly ("got it, I'll remember that").
- Never store passwords, card numbers or government ID numbers.

CURRENT MEMORY CONTENTS:
{memory}
"""
