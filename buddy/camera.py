"""The buddy's eyes: takes one photo on request with rpicam-still and hands it to Claude.

Photos are kept in memory only (written briefly to RAM-backed /dev/shm, then deleted)."""
import base64
import logging
import os
import subprocess
import tempfile
import threading

log = logging.getLogger("camera")

TOOL_SPEC = {
    "name": "look",
    "description": (
        "Take a photo with your camera and look at it. The camera is on the front of your case, "
        "facing the user at their desk. Use it when the user asks what you see, to look at or "
        "identify something, to read text they hold up, or to check how something looks. "
        "Only use it when asked or clearly needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "focus": {"type": "string", "description": "What to pay attention to, e.g. 'the label on the box'"},
        },
    },
}

_lock = threading.Lock()  # one photo at a time


class Camera:
    def __init__(self, cfg: dict):
        c = cfg.get("camera", {})
        self.enabled = c.get("enabled", True)
        self.width = int(c.get("width", 1280))
        self.height = int(c.get("height", 960))
        self.rotation = int(c.get("rotation", 0))  # 0 or 180 if the camera is mounted upside down

    def capture_jpeg(self) -> bytes:
        tmpdir = "/dev/shm" if os.path.isdir("/dev/shm") else None
        fd, path = tempfile.mkstemp(suffix=".jpg", dir=tmpdir)
        os.close(fd)
        cmd = ["rpicam-still", "-n", "-t", "800", "--width", str(self.width),
               "--height", str(self.height), "-q", "85", "-o", path]
        if self.rotation == 180:
            cmd += ["--rotation", "180"]
        try:
            with _lock:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                err = (r.stderr or r.stdout).strip().splitlines()
                raise RuntimeError(err[-1] if err else f"rpicam-still exited {r.returncode}")
            with open(path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def tool_result(self, state, args) -> list | str:
        """Returns tool_result content: the photo plus a short text note."""
        if not self.enabled:
            return "The camera is turned off in config.toml."
        import time
        state.flash_time = time.time()
        state.flash.set()  # shutter flash on the face
        state.set(mood="curious", caption="Looking...")
        try:
            jpg = self.capture_jpeg()
        except Exception as e:
            log.error("Photo failed: %s", e)
            return f"Error: couldn't take a photo ({e})."
        log.info("Photo taken (%d KB)", len(jpg) // 1024)
        focus = (args or {}).get("focus")
        note = "Here is the photo you just took." + (f" Focus on: {focus}." if focus else "")
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpg).decode()}},
            {"type": "text", "text": note},
        ]
