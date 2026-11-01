"""Reprodução local: cada computador toca o próprio arquivo (volume individual)."""
import os

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudio, QAudioOutput, QMediaDevices, QMediaPlayer

MAX_VOICES = 8


class AudioEngine(QObject):
    def __init__(self, volume=0.8, parent=None):
        super().__init__(parent)
        self.volume = volume
        self.muted = False
        self._voices = []
        self.played = []   # usado pelos testes (KIOBOT_NO_AUDIO=1 não toca nada de verdade)

    def _linear(self):
        v = 0.0 if self.muted else self.volume
        try:
            return QAudio.convertVolume(v, QAudio.VolumeScale.LogarithmicVolumeScale,
                                        QAudio.VolumeScale.LinearVolumeScale)
        except Exception:
            return v * v

    def _apply(self):
        lin = self._linear()
        for _, out in self._voices:
            out.setVolume(lin)

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        self._apply()

    def set_muted(self, m):
        self.muted = bool(m)
        self._apply()

    def play(self, path):
        """Toca o arquivo. Retorna False se o computador não tiver saída de áudio."""
        if os.environ.get("KIOBOT_NO_AUDIO"):
            self.played.append(str(path))
            return True
        if QMediaDevices.defaultAudioOutput().isNull():
            return False
        while len(self._voices) >= MAX_VOICES:
            self._drop(self._voices[0])
        player, out = QMediaPlayer(self), QAudioOutput(self)
        out.setVolume(self._linear())
        player.setAudioOutput(out)
        player.setSource(QUrl.fromLocalFile(str(path)))
        voice = (player, out)
        self._voices.append(voice)
        player.mediaStatusChanged.connect(lambda st, v=voice: self._on_status(st, v))
        player.errorOccurred.connect(lambda *a, v=voice: self._drop(v))
        player.play()
        return True

    def _on_status(self, st, voice):
        if st == QMediaPlayer.MediaStatus.EndOfMedia:
            self._drop(voice)

    def _drop(self, voice):
        if voice in self._voices:
            self._voices.remove(voice)
            voice[0].stop()
            voice[0].deleteLater()
            voice[1].deleteLater()

    def stop_all(self):
        for v in list(self._voices):
            self._drop(v)
