#!/usr/bin/env python3
"""MOCA speaker — TTS via Piper, cross-platform audio output."""

import json
import os
import platform
import subprocess
import sys

# Load config
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

_base_dir = os.path.dirname(os.path.abspath(__file__))
TTS_MODEL = os.path.join(_base_dir, config["tts"]["model"])
TTS_SPEED = config["tts"].get("speed", 1.0)
SYSTEM = platform.system()


def get_piper_cmd():
    """Get piper command — uses the piper-tts Python module."""
    return [sys.executable, "-m", "piper"]


def speak(text):
    """Speak text using Piper TTS piped to audio output."""
    try:
        piper_cmd = get_piper_cmd() + [
            "--model", TTS_MODEL,
            "--output-raw",
            "--length-scale", str(1.0 / TTS_SPEED),
        ]

        if SYSTEM in ("Darwin", "Linux"):
            # Pipe piper raw output to sox for playback
            sox_cmd = [
                "sox",
                "-t", "raw",
                "-r", "22050",
                "-e", "signed",
                "-b", "16",
                "-c", "1",
                "-", "-d",
            ]

            piper_proc = subprocess.Popen(
                piper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            sox_proc = subprocess.Popen(
                sox_cmd,
                stdin=piper_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            piper_proc.stdin.write(text.encode("utf-8"))
            piper_proc.stdin.close()
            piper_proc.stdout.close()
            sox_proc.wait()
            piper_proc.wait()

        elif SYSTEM == "Windows":
            # On Windows, capture raw audio and play with sounddevice
            import numpy as np
            import sounddevice as sd

            piper_proc = subprocess.Popen(
                piper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            piper_proc.stdin.write(text.encode("utf-8"))
            piper_proc.stdin.close()
            raw_audio = piper_proc.stdout.read()
            piper_proc.wait()

            if raw_audio:
                audio_data = np.frombuffer(raw_audio, dtype=np.int16)
                sd.play(audio_data, samplerate=22050, blocksize=1024)
                sd.wait()

    except FileNotFoundError:
        print("SPEAKER_ERROR: piper not found in PATH", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"SPEAKER_ERROR: {e}", file=sys.stderr, flush=True)


def main():
    """Read lines from stdin, speak each one, print DONE when finished."""
    for line in sys.stdin:
        text = line.strip()
        if text:
            speak(text)
            print("DONE", flush=True)


if __name__ == "__main__":
    main()
