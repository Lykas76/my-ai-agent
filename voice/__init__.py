"""Voice providers are client-side adapters, independent of dialogue."""
from typing import Protocol


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, language: str) -> str: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str, language: str) -> bytes: ...


class UnavailableVoice:
    def transcribe(self, audio, language):
        raise ValueError("Speech recognition is not configured")

    def synthesize(self, text, language):
        raise ValueError("Speech synthesis is not configured")
