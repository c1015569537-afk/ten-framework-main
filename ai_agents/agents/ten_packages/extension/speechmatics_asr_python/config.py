#
# This file is part of TEN Framework, an open source project.
# Licensed under the Apache License, Version 2.0.
# See the LICENSE file for more information.
#

from typing import Any, Dict, List
from dataclasses import dataclass, field
import copy

from pydantic import BaseModel
from ten_ai_base.utils import encrypt


@dataclass
class SpeechmaticsASRConfig(BaseModel):
    # Support multiple API keys for automatic failover
    # Priority: api_keys (if provided) > key (legacy support)
    api_keys: List[str] = field(default_factory=list)  # Multiple API keys for rotation
    key: str = ""  # Legacy single key support (deprecated but still supported)
    current_key_index: int = 0  # Track current key index for rotation
    attempted_key_indices: List[int] = field(default_factory=list)  # Track attempted keys to prevent infinite loops

    chunk_ms: int = 160  # 160ms per chunk
    language: str = "en"
    sample_rate: int = 16000
    uri: str = "wss://eu2.rt.speechmatics.com/v2"
    max_delay_mode: str = "flexible"  # "flexible" or "fixed"
    max_delay: float = 0.7  # 0.7 - 4.0
    encoding: str = "pcm_s16le"
    enable_partials: bool = True
    operating_point: str = "enhanced"
    hotwords: List[str] = field(default_factory=list)

    # True: streaming output final words, False: streaming output final sentences
    enable_word_final_mode: bool = False

    drain_mode: str = "disconnect"  # "disconnect" or "mute_pkg"
    mute_pkg_duration_ms: int = 1500

    dump: bool = False
    dump_path: str = "."

    # Audio settings
    audio_gain: float = 7.0  # 音频增益倍数 (1.0 - 10.0)，7x 为推荐值（6-8 之间）

    # Diarization settings
    diarization: str = "speaker"  # "none", "speaker", "channel", or "channel_and_speaker"
    speaker_sensitivity: float = 0.35  # 0.0 - 1.0, lower = easier to distinguish speakers
    max_speakers: int = 10  # 2 - 100, maximum number of speakers to detect
    prefer_current_speaker: bool = True  # reduce false speaker switches
    channel_diarization_labels: List[str] = field(
        default_factory=list
    )  # e.g., ["Agent", "Customer"]

    def to_str(self, sensitive_handling: bool = False) -> str:
        if not sensitive_handling:
            return f"{self}"

        config = copy.deepcopy(self)
        if config.key:
            config.key = config.key[:4] + "****"
        return f"{config}"

    params: Dict[str, Any] = field(default_factory=dict)
    black_list_params: List[str] = field(default_factory=lambda: [])

    def is_black_list_params(self, key: str) -> bool:
        return key in self.black_list_params

    def update(self, params: Dict[str, Any]) -> None:
        """Update configuration with additional parameters."""
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_current_key(self) -> str:
        """Get the current API key based on priority."""
        # If api_keys list is provided and not empty, use it
        if self.api_keys and len(self.api_keys) > 0:
            if 0 <= self.current_key_index < len(self.api_keys):
                return self.api_keys[self.current_key_index]
            else:
                # Reset to first key if index is out of bounds
                self.current_key_index = 0
                return self.api_keys[0]
        # Fallback to legacy single key
        return self.key

    def get_next_key(self) -> str:
        """Rotate to the next API key and return it."""
        if self.api_keys and len(self.api_keys) > 1:
            # Mark current key as attempted
            if self.current_key_index not in self.attempted_key_indices:
                self.attempted_key_indices.append(self.current_key_index)

            # Find next unattempted key
            for i in range(len(self.api_keys)):
                next_index = (self.current_key_index + 1 + i) % len(self.api_keys)
                if next_index not in self.attempted_key_indices:
                    self.current_key_index = next_index
                    return self.api_keys[next_index]

            # All keys have been attempted, raise exception
            raise ValueError("所有 API key 都已尝试过，无法继续切换")

        # If only one key or using legacy single key, no rotation
        return self.get_current_key()

    def has_multiple_keys(self) -> bool:
        """Check if there are multiple API keys available."""
        return len(self.api_keys) > 1

    def has_unattempted_keys(self) -> bool:
        """Check if there are unattempted keys available."""
        if not self.api_keys or len(self.api_keys) <= 1:
            return False
        return len(self.attempted_key_indices) < len(self.api_keys)

    def reset_key_rotation(self) -> None:
        """Reset key rotation state (clear attempted history)."""
        self.current_key_index = 0
        self.attempted_key_indices = []

    def reset_key_index(self) -> None:
        """Reset the key index to 0 (deprecated, use reset_key_rotation instead)."""
        self.reset_key_rotation()

    def to_json(self, sensitive_handling: bool = False) -> str:
        """Convert config to JSON string with optional sensitive data handling."""
        config_dict = self.model_dump()
        if sensitive_handling:
            if self.key:
                config_dict["key"] = encrypt(config_dict["key"])
        if config_dict["params"]:
            for key, value in config_dict["params"].items():
                if key == "key":
                    config_dict["params"][key] = encrypt(value)
        return str(config_dict)
