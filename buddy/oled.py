"""The case's little 0.96" OLED (SSD1306, 128x64, I2C 0x3C) as a companion status screen.

Idle: big clock, date and CPU temperature.  Active: mini eyes matching the big face + a status word.
Shifts a pixel every minute and dims when idle for a long time, to avoid OLED burn-in.
Turns itself off quietly if the screen or the luma.oled library isn't available."""
import logging
import math
import threading
import time
from pathlib import Path

log = logging.getLogger("oled")

STATUS = {
    "listening": "Listening...",
    "thinking": "Thinking...",
    "speaking": "Speaking",
    "booting": "Waking up...",
    "error": "Need help",
}
FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def _font(size, bold=False):
    from PIL import ImageFont
    path = FONT_PATHS[1 if bold else 0]
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def cpu_temp():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except Exception:
        return None


class OledPanel(threading.Thread):
    W, H = 128, 64

    def __init__(self, state, cfg, device=None):
        super().__init__(daemon=True)
        o = cfg.get("oled", {})
        self.state = state
        self.enabled = o.get("enabled", True)
        self.rotation = int(o.get("rotation", 0))
        self.dim_after = float(o.get("dim_after_min", 10)) * 60
        self.device = device  # tests can pass a fake
        self.f_big = _font(30, bold=True)
        self.f_small = _font(11)
        self.f_status = _font(13, bold=True)

    def _open(self):
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306
        rot = {0: 0, 90: 1, 180: 2, 270: 3}.get(self.rotation, 0)
        return ssd1306(i2c(port=1, address=0x3C), rotate=rot)

    def run(self):
        if not self.enabled:
            return
        try:
            if self.device is None:
                self.device = self._open()
        except Exception as e:
            log.warning("OLED not available (%s) - small screen disabled", e)
            return
        log.info("OLED status screen running")
        dimmed = None
        while not self.state.quit_event.is_set():
            mode, mood, _, caption, last = self.state.snapshot()
            idle = mode == "idle"
            want_dim = idle and time.time() - last > self.dim_after
            if want_dim != dimmed:
                try:
                    self.device.contrast(10 if want_dim else 200)
                except Exception:
                    pass
                dimmed = want_dim
            try:
                img = self.render(mode, mood)
                self.device.display(img)
            except Exception as e:
                log.error("OLED draw failed: %s", e)
                time.sleep(5)
            time.sleep(0.12 if not idle else 1.0)
        try:
            self.device.clear()
        except Exception:
            pass

    # ---------- drawing ----------
    def render(self, mode, mood):
        from PIL import Image, ImageDraw
        img = Image.new("1", (self.W, self.H))
        d = ImageDraw.Draw(img)
        if mode == "idle":
            self._draw_clock(d)
        else:
            self._draw_eyes(d, mood, mode)
            text = STATUS.get(mode, "")
            if mode == "speaking" and mood == "sad":
                text = "Hmm..."
            w = d.textlength(text, font=self.f_status)
            d.text(((self.W - w) / 2, 46), text, font=self.f_status, fill=1)
        return img

    def _draw_clock(self, d):
        now = time.localtime()
        # tiny drift every minute so the same pixels aren't lit forever (burn-in)
        dx = (now.tm_min % 5) - 2
        dy = (now.tm_min // 5) % 3 - 1
        hhmm = time.strftime("%I:%M", now).lstrip("0")
        w = d.textlength(hhmm, font=self.f_big)
        d.text(((self.W - w) / 2 + dx, 4 + dy), hhmm, font=self.f_big, fill=1)
        date = time.strftime("%a %b %d", now)
        t = cpu_temp()
        line = f"{date}   {t:.0f}°C" if t is not None else date
        w = d.textlength(line, font=self.f_small)
        d.text(((self.W - w) / 2 + dx, 46 + dy), line, font=self.f_small, fill=1)

    def _draw_eyes(self, d, mood, mode):
        t = time.time()
        blink = (t % 4.0) < 0.15
        ew, eh = 26, 30
        cx = [self.W // 2 - 24, self.W // 2 + 24]
        top = 8
        if mode == "thinking":
            off = (6, -2)
        else:
            off = (int(3 * math.sin(t * 0.7)), 0)
        for i, x in enumerate(cx):
            x0, y0 = x - ew // 2 + off[0], top + off[1]
            if blink:
                d.rectangle([x0, y0 + eh // 2 - 2, x0 + ew, y0 + eh // 2 + 2], fill=1)
                continue
            if mood in ("happy", "excited", "love"):
                # upside-down U: happy squint
                d.chord([x0, y0 + 4, x0 + ew, y0 + eh + 8], 180, 360, fill=1)
                d.chord([x0 + 5, y0 + 14, x0 + ew - 5, y0 + eh + 8], 180, 360, fill=0)
            elif mood == "sleepy":
                d.rounded_rectangle([x0, y0 + eh - 10, x0 + ew, y0 + eh], radius=4, fill=1)
            else:
                h = eh + (4 if mood == "surprised" else 0)
                d.rounded_rectangle([x0, y0, x0 + ew, y0 + h], radius=8, fill=1)
                if mood == "sad":  # droopy outer lids
                    if i == 0:
                        d.polygon([(x0 - 1, y0 - 1), (x0 + ew + 1, y0 - 1), (x0 - 1, y0 + 12)], fill=0)
                    else:
                        d.polygon([(x0 - 1, y0 - 1), (x0 + ew + 1, y0 - 1), (x0 + ew + 1, y0 + 12)], fill=0)
        if mode == "listening":  # little pulse dots under the eyes
            for k in range(3):
                on = int(t * 4) % 3 == k
                d.ellipse([self.W // 2 - 10 + k * 8, 40, self.W // 2 - 6 + k * 8, 44], fill=1 if on else 0,
                          outline=1)
