"""The Freenove case's 4 RGB lights (GPIO adapter board at I2C 0x21), animated from the buddy's state.

Only the LED registers are used; the board's fan control is never touched.
Turns itself off quietly if the board or the smbus2 library isn't available."""
import logging
import math
import threading
import time

from . import settings

log = logging.getLogger("lights")

ADDR = 0x21
REG_LED_ONE = 0x01   # [led_id, r, g, b]
REG_LED_ALL = 0x02   # [r, g, b]
REG_LED_MODE = 0x03  # 1 = colors set by us
N_LEDS = 4

CYAN = (40, 220, 255)
MOOD_RGB = {
    "neutral": CYAN, "curious": CYAN, "thinking": (150, 60, 255),
    "happy": (255, 170, 40), "excited": (60, 255, 140), "love": (255, 60, 150),
    "sad": (60, 90, 255), "surprised": (255, 255, 255), "sleepy": (20, 90, 110),
}

TOOL_SPEC = {
    "name": "set_lights",
    "description": "Turn your case's colored lights on or off, or change their brightness, when the user asks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["on", "off", "brightness"]},
            "level": {"type": "integer", "description": "0-100, only for action=brightness"},
        },
        "required": ["action"],
    },
}


def apply_lights(action, level=None) -> str:
    s = settings.load()
    if action == "off":
        settings.save(lights_on=False)
        return "Lights turned off."
    if action == "on":
        settings.save(lights_on=True)
        return "Lights turned on."
    lvl = max(5, min(100, int(level if level is not None else 60)))
    settings.save(lights_on=True, lights_brightness=lvl)
    return f"Light brightness changed from {s.get('lights_brightness', 60)} to {lvl}."


class Board:
    def __init__(self, bus=1):
        from smbus2 import SMBus
        self.bus = SMBus(bus)
        self.bus.read_byte_data(ADDR, 0xf6)  # raises if the board isn't there
        self.bus.write_byte_data(ADDR, REG_LED_MODE, 1)

    def all(self, rgb):
        self.bus.write_i2c_block_data(ADDR, REG_LED_ALL, list(rgb))

    def one(self, i, rgb):
        self.bus.write_i2c_block_data(ADDR, REG_LED_ONE, [i, *rgb])


class Lights(threading.Thread):
    def __init__(self, state, cfg, board=None):
        super().__init__(daemon=True)
        c = cfg.get("lights", {})
        self.state = state
        self.enabled = c.get("enabled", True)
        self.default_brightness = int(c.get("brightness", 60))
        self.off_after = float(c.get("off_after_min", 15)) * 60
        self.board = board
        self.last = None

    def run(self):
        if not self.enabled:
            return
        try:
            if self.board is None:
                self.board = Board()
        except Exception as e:
            log.warning("Case lights not available (%s)", e)
            return
        log.info("Case lights running")
        cfg_tick = 0
        on, bright = True, self.default_brightness / 100
        while not self.state.quit_event.is_set():
            if cfg_tick <= 0:  # re-read voice-changed settings once a second
                s = settings.load()
                on = s.get("lights_on", True)
                bright = s.get("lights_brightness", self.default_brightness) / 100
                cfg_tick = 20
            cfg_tick -= 1
            try:
                self.frame(on, bright)
            except Exception as e:
                log.error("lights failed: %s", e)
                time.sleep(5)
            time.sleep(0.05)
        try:
            self.board.all((0, 0, 0))
        except Exception:
            pass

    def _set_all(self, rgb):
        rgb = tuple(max(0, min(255, int(v))) for v in rgb)
        if rgb != self.last:
            self.board.all(rgb)
            self.last = rgb

    def frame(self, on, bright):
        t = time.time()
        mode, mood, level, _, last = self.state.snapshot()
        if t - self.state.flash_time < 0.35:  # camera: white flash
            self._set_all((255, 255, 255))
            return
        if not on:
            self._set_all((0, 0, 0))
            return

        def scaled(rgb, k):
            return tuple(v * k * bright for v in rgb)

        if mode == "listening":
            k = 0.55 + 0.45 * math.sin(t * 6)
            self._set_all(scaled((40, 120, 255), k))
        elif mode == "thinking":
            # purple light running around the 4 LEDs
            self.last = None
            pos = (t * 6) % N_LEDS
            for i in range(N_LEDS):
                d = min(abs(i - pos), N_LEDS - abs(i - pos))
                k = max(0.08, 1 - d * 0.6)
                self.board.one(i, tuple(max(0, min(255, int(v))) for v in scaled((150, 60, 255), k)))
        elif mode == "speaking":
            k = 0.25 + 0.75 * min(1.0, level * 1.6)
            self._set_all(scaled(MOOD_RGB.get(mood, CYAN), k))
        elif mode == "error":
            k = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(t * 1.5))
            self._set_all(scaled((255, 120, 0), k))
        elif mode == "booting":
            k = 0.1 + 0.3 * (0.5 + 0.5 * math.sin(t * 2))
            self._set_all(scaled((255, 255, 255), k))
        else:  # idle
            idle_for = t - last
            if idle_for > self.off_after:
                self._set_all((0, 0, 0))
            else:
                fade = 1.0 if idle_for < self.off_after * 0.5 else 0.4
                k = (0.08 + 0.17 * (0.5 + 0.5 * math.sin(t * 1.2))) * fade
                self._set_all(scaled(CYAN, k))
