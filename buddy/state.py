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
        self.volume = 50            # 0..100, set from config / settings.json at startup
        self.caption = "Waking up..."
        self.caption_start = 0.0    # when the current spoken caption starts playing
        self.caption_dur = 0.0      # how long it takes to say it (0 = not timed)
        self.last_interaction = time.time()
        self.tap_event = threading.Event()
        self.quit_event = threading.Event()
        self.flash = threading.Event()  # camera shutter flash on the face

    def set(self, mode=None, mood=None, caption=None):
        with self.lock:
            if mode is not None:
                self.mode = mode
            if mood is not None and mood in MOODS:
                self.mood = mood
            if caption is not None:
                self.caption = caption
                self.caption_dur = 0.0
            self.last_interaction = time.time()

    def snapshot(self):
        with self.lock:
            return self.mode, self.mood, self.mouth_level, self.caption, self.last_interaction

    def set_spoken_caption(self, text, mood, duration, delay=0.2):
        """Caption for a sentence that's about to be spoken, timed so pages follow the voice."""
        with self.lock:
            self.mode = "speaking"
            if mood in MOODS:
                self.mood = mood
            self.caption = text
            self.caption_start = time.time() + delay
            self.caption_dur = duration
            self.last_interaction = time.time()

    def caption_timing(self):
        with self.lock:
            return self.caption_start, self.caption_dur
