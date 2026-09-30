"""Type to your buddy from a terminal (handy over SSH, or before the mic arrives).

    python -m buddy.chat            # replies are printed AND spoken through the speakers
    python -m buddy.chat --quiet    # printed only

Uses the same brain, memory and history as the voice buddy.
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


def main():
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
    cfg = load_config()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No ANTHROPIC_API_KEY — put it in the .env file")
    state = BuddyState()
    quiet = "--quiet" in sys.argv

    from .brain import Brain, Mouth

    if quiet:
        mouth = PrintOnlyMouth()
    else:
        from .audio import Speaker
        from .speech import Voice

        v = cfg["voice"]
        voice = Voice(v.get("piper_voice", "voices/en_US-lessac-medium.onnx"))
        mouth = Mouth(state, voice, Speaker(state, v.get("output_device")))
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
