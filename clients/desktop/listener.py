#!/usr/bin/env python3
"""MOCA listener — continuous mic capture with wake word detection."""

import json
import os
import re
import sys
import threading

import speech_recognition as sr

# Load config
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(config_path, "r") as f:
    config = json.load(f)

WAKE_WORDS = [w.lower() for w in config["wake_words"]]
ENERGY_THRESHOLD = config["voice"]["energy_threshold"]
PAUSE_THRESHOLD = config["voice"]["pause_threshold"]
PHRASE_TIME_LIMIT = config["voice"]["phrase_time_limit"]

# Fuzzy match threshold — max edit distance to accept as wake word
WAKE_FUZZ_MAX_DIST = 2

paused = False
pause_lock = threading.Lock()


def _edit_distance(a, b):
    """Simple Levenshtein distance."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _is_wake_match(word):
    """Check if a word is close enough to any wake word (multi-strategy match)."""
    word = word.lower().rstrip(".,!?")

    # Known Google misrecognitions of "moca"
    ALIASES = {
        "mauka", "mokalo", "mocker", "mocha", "moka", "mokka",
        "mocca", "moco", "morca", "marca", "mocka", "muka",
    }

    if word in ALIASES:
        return True

    for w in WAKE_WORDS:
        # Exact match
        if word == w:
            return True
        # Substring containment (e.g. "mokalo" contains "moca")
        if len(w) >= 3 and w in word:
            return True
        # Edit distance (catches mocha→moca, morca→moca); skip short wake words
        if len(w) >= 3 and _edit_distance(word, w) <= WAKE_FUZZ_MAX_DIST:
            return True
    return False


def stdin_reader():
    """Read PAUSE/RESUME commands from stdin."""
    global paused
    for line in sys.stdin:
        cmd = line.strip().upper()
        if cmd == "PAUSE":
            with pause_lock:
                paused = True
        elif cmd == "RESUME":
            with pause_lock:
                paused = False


def strip_wake_words(text):
    """Remove wake words (fuzzy matched) from transcribed text."""
    words = text.split()
    cleaned = [w for w in words if not _is_wake_match(w)]
    return " ".join(cleaned).strip()


def has_wake_word(text):
    """Check if any word in text fuzzy-matches a wake word."""
    words = text.lower().split()
    return any(_is_wake_match(w) for w in words)


def main():
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = ENERGY_THRESHOLD
    recognizer.pause_threshold = PAUSE_THRESHOLD
    recognizer.dynamic_energy_threshold = False

    mic = sr.Microphone()

    # Calibrate for ambient noise
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)

    # Start stdin reader thread
    reader = threading.Thread(target=stdin_reader, daemon=True)
    reader.start()

    print("LISTENER_READY", flush=True)

    while True:
        with pause_lock:
            if paused:
                import time
                time.sleep(0.1)
                continue

        try:
            with mic as source:
                audio = recognizer.listen(
                    source,
                    phrase_time_limit=PHRASE_TIME_LIMIT,
                )

            with pause_lock:
                if paused:
                    continue

            text = recognizer.recognize_google(audio)

            if not text:
                continue

            # Debug: log raw recognition to stderr (visible in Electron console)
            print(f"RAW: {text}", file=sys.stderr, flush=True)

            if not has_wake_word(text):
                print(f"NO_WAKE: {text}", file=sys.stderr, flush=True)
                continue

            cleaned = strip_wake_words(text)
            if cleaned:
                print(f"TRIGGERED: {cleaned}", file=sys.stderr, flush=True)
                print(cleaned, flush=True)

        except sr.UnknownValueError:
            continue
        except sr.RequestError as e:
            print(f"LISTENER_ERROR: {e}", file=sys.stderr, flush=True)
            import time
            time.sleep(5)
        except Exception as e:
            print(f"LISTENER_ERROR: {e}", file=sys.stderr, flush=True)
            import time
            time.sleep(1)


if __name__ == "__main__":
    main()
