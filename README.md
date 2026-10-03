# Pi Buddy

A desktop AI buddy for the Raspberry Pi 5. It has an animated face on the touchscreen, you talk to it by voice, it answers with Claude, and it keeps long-term memory stored on the Pi.

**Stage 1 (this version):**

- animated face with moods
- wake word or tap to talk
- local speech-to-text and text-to-speech
- Claude with long-term memory
- a searchable conversation log
- web search

**Coming next:**

- reminders and timers
- calendar
- camera ("what's this?")
- recognizing you versus Hannah
- homelab checks

---

## 1. Flash a fresh SD card (about 15 minutes)

1. On your computer, install **Raspberry Pi Imager** from raspberrypi.com/software.
2. Choose **Raspberry Pi 5**, then **Raspberry Pi OS (64-bit)**. Use the normal desktop version, not Lite.
3. Click **Edit settings** before writing, and set these:
   - Hostname: `pibuddy`
   - A username and password
   - Your Wi-Fi network and country
   - On the **Services** tab, turn on **SSH** (password login is fine)
4. Write the card, put it in the Pi and boot it.

**Check the screen first.** It should show the desktop, and a tap should move the pointer. If it stays black, the screen probably needs a line in `/boot/firmware/config.txt`. Look up the model number printed on the screen's board and check the maker's wiki (Waveshare and similar brands list the exact line).

**Check audio:**

```bash
arecord -l                    # your microphone should be listed
aplay -l                      # your speakers should be listed
speaker-test -t wav -c 2 -l 1 # you should hear a voice
```

If the wrong device plays, right-click the speaker icon in the top bar and pick the right output and input.

## 2. Put this code on GitHub (one time)

1. On github.com, click **New repository**. Name it `pi-buddy`, make it **Private**, and create it.
2. Click **uploading an existing file**, drag in everything from this folder, and commit.

   Don't upload `.env` or `config.toml`. They aren't in the zip anyway.

## 3. Install on the Pi

From the Pi's terminal, or over SSH from your computer with `ssh <user>@pibuddy.local`:

```bash
git clone https://github.com/<your-username>/pi-buddy.git
cd pi-buddy
bash install.sh
```

Because the repo is private, git will ask for a password. Use a GitHub **personal access token**, not your GitHub password. You can create one under GitHub → Settings → Developer settings → Tokens.

The install takes about 10 to 20 minutes. At the end it asks for your Anthropic API key and saves it to `.env`, which is only on the Pi and never uploaded.

## 4. Run it

```bash
./run.sh
```

- Tap the screen or say **"hey jarvis"**. It chimes, and the face looks at you while it listens.
- Talk, then pause. It thinks (the eyes look up and dots bounce), then answers out loud.
- After it answers you have about 6 seconds to reply without the wake word.
- Tapping while it's talking interrupts it.
- To quit, press **Esc** or press and hold the screen for 5 seconds.

It also starts on its own every time the Pi boots to the desktop.

**Camera:** ask *"What do you see?"*, *"What's this?"* or *"Read this label."* The screen flashes white, it takes one photo, and Claude looks at it. Photos are never saved; only the most recent one stays in the conversation. If pictures come out upside down, set `rotation = 180` under `[camera]` in `config.toml`, or set `enabled = false` to turn the camera off.

**Small screen (OLED):** shows a clock, date and CPU temperature while idle, and mini eyes with "Listening… / Thinking… / Speaking" while active. It dims after 10 idle minutes to prevent burn-in. Settings are under `[oled]` in `config.toml`.

**Typing mode** (over SSH, or with no mic): same brain and memory, but you type instead of talking. If the face is running, your messages go to it, so the face reacts and it speaks.

```bash
cd ~/pi-buddy && source .venv/bin/activate
python -m buddy.chat           # replies are printed and spoken
python -m buddy.chat --quiet   # printed only
```

With no microphone plugged in, the face shows "Plug in a USB microphone" and keeps checking. Plug one in and it starts listening, with no restart needed.

**Try:**

- "Remember that Hannah's birthday is May 3rd."
- Later, in a new conversation: "When's Hannah's birthday?"
- "What's the weather in Richmond tomorrow?"
- "What did we talk about yesterday?"

## Updating

The easiest way is to copy the new zip straight to the Pi, which keeps hidden files like `.gitignore` and the file permissions:

1. On your computer, open a terminal (PowerShell on Windows) in the folder where the zip was downloaded, and run:
   ```bash
   scp pi-buddy-full.zip djk8286@pibuddy.local:~
   ```
2. On the Pi:
   ```bash
   cd ~ && unzip -o pi-buddy-full.zip && cd pi-buddy
   git status --short          # should show only code files, never .env / data / voices
   git add -A && git commit -m "Update" && git push
   ```
3. Restart the buddy with `pkill -f buddy.main`. The auto-start relaunches it at the next boot; to start it now, run `./run.sh`.

Your `.env`, `config.toml`, memories and voices are never touched by an update.

## Customizing

| File | What it controls |
|---|---|
| `config.toml` | name, Claude model, wake word, voice, timings, captions |
| `data/settings.json` | things it changed itself, like volume ("turn it down") |
| `personality.md` | how it talks and behaves (edit freely) |
| `data/memories/` | its long-term memory: plain text files you can read and edit |
| `data/history.db` | the log of every conversation |
| `data/buddy.log` | debug log. Check it if something's off |

**Other voices:** browse https://rhasspy.github.io/piper-samples/, then run for example:

```bash
source .venv/bin/activate
python -m piper.download_voices en_US-ryan-high --data-dir voices
```

Then set `piper_voice = "voices/en_US-ryan-high.onnx"` in `config.toml`.

## Back up the memory

```bash
bash backup.sh                 # saves to ~/pi-buddy-backups, keeps 30
crontab -e                     # add this line for nightly 3am backups:
0 3 * * * bash /home/<user>/pi-buddy/backup.sh
```

To also keep an off-Pi copy, point the backup at a network share or USB stick: `bash backup.sh /mnt/nas/pi-buddy`.

## How the memory works

- **Short-term:** the current conversation, which is the last 20 messages. After 10 quiet minutes a fresh conversation starts.
- **Long-term:** Claude's memory tool reads and writes text files under `data/memories/`. Every new conversation starts with all of those files loaded into Claude's instructions, so it already knows you without an extra lookup.
- **History:** every exchange is logged to SQLite. Claude can search it with the `search_history` tool.

Memory files live only on the Pi. What you say is sent to the Claude API to generate replies.

## Troubleshooting

| Problem | Fix |
|---|---|
| Face shows "Startup error: No ANTHROPIC_API_KEY" | Run `echo ANTHROPIC_API_KEY=sk-ant-... > .env` |
| It never hears the wake word | Tap to talk works regardless. Check `data/buddy.log` for "Wake word unavailable". Try `wake_threshold = 0.3` |
| It wakes up by itself | Raise `wake_threshold` to 0.6 or 0.7 |
| It cuts you off mid-sentence | Raise `silence_seconds` to 1.3 |
| It waits too long after you stop | Lower `silence_seconds`, or reduce background noise such as the fan near the mic |
| Wrong mic or speaker | Run `arecord -L` / `aplay -L` to list devices, then set `input_device` / `output_device` in `config.toml` (e.g. `plughw:2,0`). Blank = system default |
| Replies are slow | Use `model = "claude-haiku-4-5-20251001"` and `whisper_model = "tiny.en"` |
| Screen is sideways | Use Screen Configuration in the Pi desktop menu to rotate it |
