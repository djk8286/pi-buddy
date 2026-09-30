"""Shared state between the face (main thread) and the brain/audio threads."""
import threading
import time

MOODS = {"neutral", "happy", "excited", "curious", "thinking", "sad", "surprised", "sleepy", "love"}


class BuddyState:
    # modes: booting, idle, listening, thinking, speaking, error
    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "booting"
        self.mood = "neutral"
        self.mouth_level = 0.0      # 0..1, driven by speaker audio
        self.caption = "Waking up..."
        self.last_interaction = time.time()
        self.tap_event = threading.Event()
        self.quit_event = threading.Event()

    def set(self, mode=None, mood=None, caption=None):
        with self.lock:
            if mode is not None:
                self.mode = mode
            if mood is not None and mood in MOODS:
                self.mood = mood
            if caption is not None:
                self.caption = caption
            self.last_interaction = time.time()

    def snapshot(self):
        with self.lock:
            return self.mode, self.mood, self.mouth_level, self.caption, self.last_interaction
