# MOCA Desktop Client

Cross-platform Electron tray app. Listens for wake words, sends speech to MOCA brain server, speaks responses via Piper TTS.

## Mac Setup

```bash
brew install piper sox
pip install SpeechRecognition pyaudio sounddevice numpy
npm install
npm start
```

## Windows Setup

```bash
# Download piper from github.com/rhasspy/piper/releases
# Add piper to PATH
pip install SpeechRecognition pyaudio sounddevice numpy
npm install
npm start
```

## Linux Setup

```bash
sudo apt install piper sox python3-pyaudio
pip install SpeechRecognition sounddevice numpy
npm install
npm start
```

## Configuration

All settings in `config.json`:

- `server` — brain server URL
- `wake_words` — words that trigger MOCA (default: "moca", "mo")
- `tts.model` — Piper voice model
- `tts.speed` — speech speed multiplier
- `voice.energy_threshold` — mic sensitivity
- `voice.pause_threshold` — silence before phrase ends
- `voice.phrase_time_limit` — max phrase length in seconds
