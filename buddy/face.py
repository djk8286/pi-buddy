"""The animated face. Runs in the main thread (SDL requires it). Scales to any screen size."""
import math
import random
import time

import pygame

BG = (6, 8, 14)
EYE = (94, 242, 255)
MOOD_COLORS = {
    "love": (255, 110, 180),
    "sad": (110, 160, 255),
    "excited": (140, 255, 200),
    "sleepy": (70, 150, 170),
}

# Per-mood targets: openness, width/height scale, lid tilt (+ = sad droop outward),
# happy (0..1 crescent cut from below), look offset
MOOD_SHAPES = {
    "neutral":   dict(open=1.0, sw=1.0, sh=1.0, tilt=0.0, happy=0.0),
    "happy":     dict(open=1.0, sw=1.05, sh=0.95, tilt=0.0, happy=0.75),
    "excited":   dict(open=1.0, sw=1.12, sh=1.12, tilt=0.0, happy=0.55),
    "curious":   dict(open=1.0, sw=1.0, sh=1.08, tilt=0.0, happy=0.0),
    "thinking":  dict(open=0.8, sw=0.95, sh=0.9, tilt=-0.1, happy=0.0),
    "sad":       dict(open=0.75, sw=0.95, sh=0.9, tilt=0.35, happy=0.0),
    "surprised": dict(open=1.0, sw=1.18, sh=1.22, tilt=0.0, happy=0.0),
    "sleepy":    dict(open=0.28, sw=1.0, sh=0.9, tilt=0.1, happy=0.0),
    "love":      dict(open=1.0, sw=1.05, sh=0.95, tilt=0.0, happy=0.75),
}


def pick_display(pref="auto"):
    """Choose which screen to draw on. The Freenove case's HDMI audio board pretends to be a
    1280x720 monitor, so 'auto' picks the smallest screen: the built-in 800x480 touchscreen."""
    try:
        sizes = pygame.display.get_desktop_sizes()
    except Exception:
        return 0
    print(f"Screens found: {sizes}", flush=True)
    if isinstance(pref, int):
        return pref if 0 <= pref < len(sizes) else 0
    return min(range(len(sizes)), key=lambda i: sizes[i][0] * sizes[i][1])


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(a, b, t):
    return tuple(int(lerp(x, y, t)) for x, y in zip(a, b))


class Face:
    def __init__(self, state, cfg):
        self.state, self.cfg = state, cfg
        # Only video + fonts. Plain pygame.init() would also open the sound card and block our speech.
        pygame.display.init()
        pygame.font.init()
        pygame.mouse.set_visible(False)
        flags = pygame.FULLSCREEN if cfg["ui"].get("fullscreen", True) else 0
        size = (0, 0) if flags else (800, 480)
        idx = pick_display(cfg["ui"].get("display", "auto"))
        self.screen = pygame.display.set_mode(size, flags, display=idx)
        pygame.display.set_caption(cfg["buddy"]["name"])
        self.W, self.H = self.screen.get_size()
        self.u = min(self.W, self.H * 1.6)  # base unit that keeps proportions on any screen
        self.font = pygame.font.Font(None, max(18, int(self.H * 0.07)))
        self.clock = pygame.time.Clock()
        self.fps = int(cfg["ui"].get("fps", 30))
        self.sleep_after = float(cfg["ui"].get("sleep_after_min", 15)) * 60
        self.show_captions = cfg["ui"].get("show_captions", True)
        # animated values
        self.cur = dict(MOOD_SHAPES["neutral"])
        self.color = EYE
        self.look = [0.0, 0.0]
        self.look_target = [0.0, 0.0]
        self.next_saccade = time.time() + 2
        self.blink_t = -1.0
        self.next_blink = time.time() + 3
        self.mouth = 0.0
        self.t0 = time.time()
        self.press_start = None

    # ---------- input ----------
    def handle_events(self):
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.state.quit_event.set()
            elif e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q):
                self.state.quit_event.set()
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE:
                self.state.tap_event.set()
            elif e.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                self.press_start = time.time()
            elif e.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP):
                # a 5-second press-and-hold quits; a normal tap talks / interrupts
                if self.press_start and time.time() - self.press_start > 5:
                    self.state.quit_event.set()
                else:
                    self.state.tap_event.set()
                self.press_start = None

    # ---------- animation ----------
    def update(self, dt):
        mode, mood, level, caption, last = self.state.snapshot()
        now = time.time()
        if mode == "idle" and now - last > self.sleep_after:
            mood = "sleepy"
        if mode == "booting":
            mood = "sleepy"
        target = dict(MOOD_SHAPES.get(mood, MOOD_SHAPES["neutral"]))
        if mode == "listening":
            target["sw"] *= 1.08
            target["sh"] *= 1.08
            target["open"] = max(target["open"], 0.9)
        k = min(1.0, dt * 8)
        for key in self.cur:
            self.cur[key] = lerp(self.cur[key], target[key], k)
        self.color = lerp_color(self.color, MOOD_COLORS.get(mood, EYE), k)

        # where the eyes look
        if mode == "thinking":
            self.look_target = [0.35, -0.45]
        elif mode == "listening":
            self.look_target = [0.0, 0.0]
        elif now > self.next_saccade:
            self.look_target = [random.uniform(-0.5, 0.5), random.uniform(-0.3, 0.3)]
            if random.random() < 0.4:
                self.look_target = [0.0, 0.0]
            self.next_saccade = now + random.uniform(1.5, 4.5)
        k2 = min(1.0, dt * 12)
        self.look = [lerp(self.look[0], self.look_target[0], k2), lerp(self.look[1], self.look_target[1], k2)]

        # blinking
        if self.blink_t < 0 and now > self.next_blink and mood != "sleepy":
            self.blink_t = 0.0
            self.next_blink = now + random.uniform(2.5, 6.0)
            if random.random() < 0.2:
                self.next_blink = now + 0.35  # occasional double blink
        if self.blink_t >= 0:
            self.blink_t += dt
            if self.blink_t > 0.18:
                self.blink_t = -1.0

        self.mouth = lerp(self.mouth, level, min(1.0, dt * 20))
        return mode, mood, caption

    def blink_factor(self):
        if self.blink_t < 0:
            return 1.0
        return abs(math.cos(self.blink_t / 0.18 * math.pi))

    # ---------- drawing ----------
    def draw_eye(self, cx, cy, side):
        c = self.cur
        t = time.time() - self.t0
        breathe = 1 + 0.015 * math.sin(t * 1.3)
        w = self.u * 0.17 * c["sw"]
        h = self.H * 0.40 * c["sh"] * breathe
        cx += self.look[0] * self.u * 0.06
        cy += self.look[1] * self.H * 0.06
        openness = max(0.04, c["open"] * self.blink_factor())
        rect = pygame.Rect(0, 0, int(w), int(h))
        rect.center = (int(cx), int(cy))
        radius = int(min(w, h) * 0.32)
        pygame.draw.rect(self.screen, self.color, rect, border_radius=radius)

        self.screen.set_clip(rect.inflate(4, 4))
        # top eyelid (closes the eye and tilts for sad/curious)
        lid_y = rect.top + h * (1 - openness)
        tilt = c["tilt"] * h * 0.5
        inner_x, outer_x = (rect.right, rect.left) if side < 0 else (rect.left, rect.right)
        pygame.draw.polygon(self.screen, BG, [
            (inner_x, rect.top - 2), (outer_x, rect.top - 2),
            (outer_x, lid_y + tilt), (inner_x, lid_y - tilt),
        ])
        # happy crescent: bite out the bottom of the eye with a background ellipse
        if c["happy"] > 0.02:
            bite_h = h * 1.1
            bite = pygame.Rect(0, 0, int(w * 1.2), int(bite_h))
            bite.centerx = rect.centerx
            bite.top = int(rect.bottom - h * 0.85 * c["happy"])
            pygame.draw.ellipse(self.screen, BG, bite)
        self.screen.set_clip(None)

    def draw_mouth(self, mode, mood):
        cx, cy = self.W / 2, self.H * 0.73
        w = self.u * 0.11
        col = self.color
        if mode == "speaking" or self.mouth > 0.05:
            h = max(self.H * 0.018, self.H * 0.11 * self.mouth)
            r = pygame.Rect(0, 0, int(w * (0.7 + 0.3 * self.mouth)), int(h))
            r.center = (int(cx), int(cy))
            pygame.draw.rect(self.screen, col, r, border_radius=int(h / 2))
        elif mood in ("happy", "excited", "love"):
            arc = pygame.Rect(0, 0, int(w), int(self.H * 0.08))
            arc.center = (int(cx), int(cy - self.H * 0.03))
            pygame.draw.arc(self.screen, col, arc, math.pi * 1.1, math.pi * 1.9, max(3, int(self.H * 0.012)))
        elif mood == "surprised":
            pygame.draw.circle(self.screen, col, (int(cx), int(cy)), int(self.H * 0.035), max(3, int(self.H * 0.01)))
        elif mood == "sad":
            arc = pygame.Rect(0, 0, int(w * 0.8), int(self.H * 0.07))
            arc.center = (int(cx), int(cy + self.H * 0.02))
            pygame.draw.arc(self.screen, col, arc, math.pi * 0.15, math.pi * 0.85, max(3, int(self.H * 0.012)))
        else:
            r = pygame.Rect(0, 0, int(w * 0.5), max(3, int(self.H * 0.012)))
            r.center = (int(cx), int(cy))
            pygame.draw.rect(self.screen, col, r, border_radius=r.height // 2)

    def draw_extras(self, mode, mood, caption):
        t = time.time() - self.t0
        if mode == "thinking":  # three bouncing dots top-right
            for i in range(3):
                y = self.H * 0.12 - abs(math.sin(t * 5 + i * 0.7)) * self.H * 0.03
                pygame.draw.circle(self.screen, self.color, (int(self.W * 0.80 + i * self.u * 0.035), int(y)),
                                   max(3, int(self.H * 0.015)))
        if mode == "listening":  # pulsing ring along the bottom edge
            a = 0.5 + 0.5 * math.sin(t * 6)
            col = lerp_color(BG, self.color, 0.35 + 0.5 * a)
            pygame.draw.rect(self.screen, col, (0, self.H - max(4, int(self.H * 0.012)), self.W, self.H), 0)
        if mood == "sleepy" and mode in ("idle", "booting"):
            for i in range(3):
                phase = (t * 0.4 + i / 3) % 1
                z = self.font.render("z", True, lerp_color(self.color, BG, phase))
                self.screen.blit(z, (self.W * 0.72 + phase * self.u * 0.08, self.H * (0.32 - phase * 0.25)))
        if self.show_captions and caption and mode in ("speaking", "thinking", "booting", "error"):
            self.draw_caption(caption)

    def draw_caption(self, text):
        max_w = self.W * 0.9
        words, lines, line = text.split(), [], ""
        for w in words:
            test = (line + " " + w).strip()
            if self.font.size(test)[0] > max_w and line:
                lines.append(line)
                line = w
            else:
                line = test
        lines.append(line)
        lines = lines[-2:]
        lh = self.font.get_linesize()
        y = self.H - lh * len(lines) - self.H * 0.02
        for ln in lines:
            surf = self.font.render(ln, True, (200, 210, 225))
            self.screen.blit(surf, ((self.W - surf.get_width()) / 2, y))
            y += lh

    def run(self):
        while not self.state.quit_event.is_set():
            dt = self.clock.tick(self.fps) / 1000.0
            self.handle_events()
            mode, mood, caption = self.update(dt)
            self.screen.fill(BG)
            gap = self.u * 0.16
            eye_y = self.H * 0.38
            self.draw_eye(self.W / 2 - gap, eye_y, side=-1)
            self.draw_eye(self.W / 2 + gap, eye_y, side=+1)
            self.draw_mouth(mode, mood)
            self.draw_extras(mode, mood, caption)
            pygame.display.flip()
        pygame.quit()
