"""OtomeKairo が所有する音声入力処理。"""

from otomekairo.audio.amivoice import AmiVoiceClient, AmiVoiceResult
from otomekairo.audio.models import AudioModelRuntime, SpeakerIdentification
from otomekairo.audio.segmenter import AudioSegmenter, SegmentedUtterance

__all__ = [
    "AmiVoiceClient",
    "AmiVoiceResult",
    "AudioModelRuntime",
    "AudioSegmenter",
    "SegmentedUtterance",
    "SpeakerIdentification",
]
