"""Type to your buddy from a terminal (handy over SSH, or before the mic arrives).

    python -m buddy.chat            # replies are printed AND spoken through the speakers
    python -m buddy.chat --quiet    # printed only

If the buddy (face) is running, your messages go to it: the face reacts and it speaks.
Otherwise a standalone buddy starts in this terminal. Same brain, memory and history either way.
"""
import logging
import os
import sys

from .config import load_config
from .state import BuddyState


class PrintOnlyMouth:
    """Stand-in for the speaking thread when running quietly."""

    class _Stop:
        def clear(self):
            pass

        def is_set(self):
            return False

    def __init__(self):
        self.stop_event = self._Stop()

    def say(self, mood, text):
        print(f"  {text}")

    def wait(self):
        pass


def chat_with_running_buddy(name="Pixel") -> bool:
    """Connect to the running buddy's socket. Returns False if it isn't running."""
    import socket
    from .config import DATA
    path = DATA / "buddy.sock"
    if not path.exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
    except OSError:
        return False
    print(f"Connected to the running buddy - watch the face! Type 'quit' to exit.\n")
    rfile = sock.makefile("rb")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"quit", "exit"}:
            break
        if not text:
            continue
        sock.sendall((text + "\n").encode())
        print(f"{name}>")
        for line in rfile:
            line = line.decode(errors="replace").rstrip("\n")
            if not line:
                break
            print(f"  {line}")
        else:
            print("(buddy disconnected)")
            break
        print()
    sock.close()
    return True


def main():
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
    cfg = load_config()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No ANTHROPIC_API_KEY — put it in the .env file")
    quiet = "--quiet" in sys.argv
    if not quiet and "--standalone" not in sys.argv and chat_with_running_buddy(cfg["buddy"]["name"]):
        return
    state = BuddyState()

    from .brain import Brain, Mouth

    if quiet:
        mouth = PrintOnlyMouth()
    else:
        from .audio import Speaker
        from .speech import Voice

        v = cfg["voice"]
        voice = Voice(v.get("piper_voice", "voices/en_US-joe-medium.onnx"))
        mouth = Mouth(state, voice, Speaker(state, v.get("output_device"), v.get("volume", 50)))
        _say = mouth.say

        def say_and_print(mood, text):
            print(f"  {text}")
            _say(mood, text)

        mouth.say = say_and_print
        mouth.start()

    brain = Brain(cfg, state, mouth)
    name = cfg["buddy"]["name"]
    print(f"Chatting with {name}. Type 'quit' to exit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"quit", "exit"}:
            break
        if not text:
            continue
        print(f"{name}>")
        brain.respond(text)
        print()


if __name__ == "__main__":
    main()
