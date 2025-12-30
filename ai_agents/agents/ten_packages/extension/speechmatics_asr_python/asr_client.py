#
# This file is part of TEN Framework, an open source project.
# Licensed under the Apache License, Version 2.0.
# See the LICENSE file for more information.
#

import asyncio
import struct
from typing import Awaitable, Callable, List, Optional, Coroutine
import speechmatics

from ten_ai_base.message import (
    ModuleError,
    ModuleErrorCode,
    ModuleErrorVendorInfo,
    ModuleType,
)
from ten_ai_base.struct import ASRResult
from ten_ai_base.transcription import Word
from ten_runtime import AsyncTenEnv, AudioFrame
from .audio_stream import AudioStream, AudioStreamEventType
from .config import SpeechmaticsASRConfig
from .word import (
    SpeechmaticsASRWord,
    convert_words_to_sentence,
    get_sentence_duration_ms,
    get_sentence_start_ms,
)
from ten_ai_base.timeline import AudioTimeline

# from .language_utils import get_speechmatics_language


async def run_asr_client(client: "SpeechmaticsASRClient"):
    assert client.client is not None
    assert client.transcription_config is not None
    assert client.audio_settings is not None

    await client.client.run(
        client.audio_stream,
        client.transcription_config,
        client.audio_settings,
    )


class SpeechmaticsASRClient:
    def __init__(
        self,
        config: SpeechmaticsASRConfig,
        ten_env: AsyncTenEnv,
        timeline: AudioTimeline,
    ):
        self.config = config
        self.ten_env = ten_env
        self.task = None
        self.audio_queue = asyncio.Queue()
        self.timeline = timeline
        self.audio_stream = AudioStream(
            self.audio_queue, self.config, self.timeline, ten_env
        )
        self.client_running_task: asyncio.Task | None = None
        self.client_needs_stopping = False
        self.sent_user_audio_duration_ms_before_last_reset = 0
        self.last_drain_timestamp: int = 0
        self.session_id = None
        self.connected = False
        self.websocket_connected = False  # Track WebSocket connection state separately
        # Cache the words for sentence final mode
        self.cache_words = []  # type: List[SpeechmaticsASRWord]

        # Counter for audio send logging (every 100 frames)
        self._audio_send_count = 0

        self.audio_settings: speechmatics.models.AudioSettings | None = None
        self.transcription_config: (
            speechmatics.models.TranscriptionConfig | None
        ) = None
        self.client: speechmatics.client.WebsocketClient | None = None
        self.on_asr_open: Optional[
            Callable[[], Coroutine[object, object, None]]
        ] = None
        self.on_asr_result: Optional[
            Callable[[ASRResult], Coroutine[object, object, None]]
        ] = None
        self.on_asr_error: Optional[
            Callable[
                [ModuleError, Optional[ModuleErrorVendorInfo]],
                Awaitable[None],
            ]
        ] = None
        self.on_asr_close: Optional[
            Callable[[], Coroutine[object, object, None]]
        ] = None

    async def start(self) -> None:
        """Initialize and start the recognition session"""
        connection_settings = speechmatics.models.ConnectionSettings(
            url=self.config.uri,
            auth_token=self.config.get_current_key(),
        )

        # sample_rate * bytes_per_sample * chunk_ms / 1000
        chunk_len = self.config.sample_rate * 2 / 1000 * self.config.chunk_ms

        self.audio_settings = speechmatics.models.AudioSettings(
            chunk_size=int(chunk_len),
            encoding=self.config.encoding,
            sample_rate=self.config.sample_rate,
        )

        additional_vocab = []
        if self.config.hotwords:
            for hw in self.config.hotwords:
                tokens = hw.split("|")
                if len(tokens) == 2 and tokens[1].isdigit():
                    additional_vocab.append({"content": tokens[0]})
                else:
                    self.ten_env.log_warn("无效的热词格式: " + hw)

        # Configure diarization if enabled
        diarization_config = None
        if self.config.diarization == "speaker":
            diarization_config = "speaker"
        elif self.config.diarization == "channel":
            diarization_config = "channel"
        elif self.config.diarization == "channel_and_speaker":
            diarization_config = "channel_and_speaker"

        # Speaker diarization config
        speaker_diarization_config = None
        if (
            self.config.diarization == "speaker"
            or self.config.diarization == "channel_and_speaker"
        ):
            # Check if SDK supports speaker_sensitivity and prefer_current_speaker
            # These parameters are available in speechmatics-python >= 4.0.0
            import inspect
            diarization_params = {"max_speakers": self.config.max_speakers}

            # Get RTSpeakerDiarizationConfig signature
            sig = inspect.signature(speechmatics.models.RTSpeakerDiarizationConfig.__init__)
            if "speaker_sensitivity" in sig.parameters:
                diarization_params["speaker_sensitivity"] = self.config.speaker_sensitivity
            if "prefer_current_speaker" in sig.parameters:
                diarization_params["prefer_current_speaker"] = self.config.prefer_current_speaker

            speaker_diarization_config = speechmatics.models.RTSpeakerDiarizationConfig(
                **diarization_params
            )

        self.transcription_config = speechmatics.models.TranscriptionConfig(
            enable_partials=self.config.enable_partials,
            language=self.config.language,
            max_delay=self.config.max_delay,
            max_delay_mode=self.config.max_delay_mode,
            additional_vocab=additional_vocab,
            operating_point=(
                self.config.operating_point
                if self.config.operating_point
                else None
            ),
            diarization=diarization_config,
            speaker_diarization_config=speaker_diarization_config,
            channel_diarization_labels=(
                self.config.channel_diarization_labels
                if self.config.channel_diarization_labels
                else None
            ),
        )

        # Initialize client
        self.client = speechmatics.client.WebsocketClient(connection_settings)

        # Set up callbacks
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.RecognitionStarted,
            self._handle_recognition_started,
        )
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.EndOfTranscript,
            self._handle_end_transcript,
        )
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.AudioEventStarted,
            self._handle_audio_event_started,
        )
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.AudioEventEnded,
            self._handle_audio_event_ended,
        )
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.Info, self._handle_info
        )
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.Warning, self._handle_warning
        )
        self.client.add_event_handler(
            speechmatics.models.ServerMessageType.Error, self._handle_error
        )

        if self.config.enable_word_final_mode:
            self.client.add_event_handler(
                speechmatics.models.ServerMessageType.AddTranscript,
                self._handle_transcript_word_final_mode,
            )
            self.client.add_event_handler(
                speechmatics.models.ServerMessageType.AddPartialTranscript,
                self._handle_partial_transcript,
            )
        else:
            self.client.add_event_handler(
                speechmatics.models.ServerMessageType.AddTranscript,
                self._handle_transcript_sentence_final_mode,
            )
            # Ignore partial transcript

        self.client_needs_stopping = False
        self.client_running_task = asyncio.create_task(self._client_run())

        self.ten_env.log_info("[连接] Speechmatics 客户端已启动，等待音频数据...")

    async def recv_audio_frame(
        self, frame: AudioFrame, session_id: str | None
    ) -> None:
        frame_buf = frame.get_buf()
        if not frame_buf:
            self.ten_env.log_warn("检测到空的音频帧")
            return

        self.session_id = session_id

        try:
            # Apply audio gain amplification to fix low volume issues
            # Use configured gain factor instead of hardcoded value
            gain_factor = self.config.audio_gain  # 默认 8x，可通过配置调整

            # Convert bytes to 16-bit PCM samples
            num_samples = len(frame_buf) // 2
            if num_samples > 0:
                fmt = f'<{num_samples}h'
                samples = list(struct.unpack(fmt, frame_buf))

                # Apply gain with clipping to prevent overflow
                amplified_samples = []
                for sample in samples:
                    amplified = int(sample * gain_factor)
                    # Clip to 16-bit range (-32768 to 32767)
                    amplified = max(-32768, min(32767, amplified))
                    amplified_samples.append(amplified)

                # 每100帧打印一次音频统计日志（仅在连接时）
                if self.connected:
                    self._audio_send_count += 1
                    if self._audio_send_count % 100 == 1:
                        # 只在需要打印时才计算统计信息
                        amp_max = max(abs(s) for s in amplified_samples)
                        amp_avg = sum(amplified_samples) / len(amplified_samples)
                        self.ten_env.log_debug(
                            f"[音频发送] 已发送 {self._audio_send_count} 帧, "
                            f"大小={len(frame_buf)}字节, "
                            f"增益后振幅: max={amp_max}, avg={amp_avg:.1f} (增益={gain_factor}x)"
                        )

                # Convert back to bytes
                frame_buf = struct.pack(fmt, *amplified_samples)
            else:
                self.ten_env.log_warn(f"[音频发送] 音频帧太小，样本数为0，大小={len(frame_buf)}字节")

            await self.audio_queue.put(frame_buf)
        except Exception as e:
            self.ten_env.log_error(f"发送音频帧错误: {e}")
            import traceback
            self.ten_env.log_error(f"错误详情: {traceback.format_exc()}")
            error = ModuleError(
                module=ModuleType.ASR,
                code=ModuleErrorCode.FATAL_ERROR.value,
                message=str(e),
            )
            asyncio.create_task(self._emit_error(error, None))

    async def stop(self) -> None:
        self.ten_env.log_info("[关闭连接] 正在停止 Speechmatics 服务...")
        self.client_needs_stopping = True

        # Reset connection states immediately
        self.websocket_connected = False
        self.connected = False

        if self.client is not None:
            try:
                # Synchronously call stop() and then wait for the connection to close
                self.client.stop()

                # Wait for WebSocket connection to fully close
                max_wait_time = 5.0  # Maximum wait time of 5 seconds
                start_time = asyncio.get_event_loop().time()

                while (
                    asyncio.get_event_loop().time() - start_time
                ) < max_wait_time:
                    # Check if the connection is actually closed
                    if not self.is_connected():
                        break
                    await asyncio.sleep(0.1)

                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed >= max_wait_time:
                    self.ten_env.log_warn(f"[关闭连接] 等待连接关闭超时 (耗时: {elapsed:.2f}s)")
                else:
                    self.ten_env.log_info(f"[关闭连接] 连接已关闭 (耗时: {elapsed:.2f}s)")

                # Force cleanup of references
                self.client = None

            except Exception as e:
                self.ten_env.log_error(f"[关闭连接] 异常: {e}")
                import traceback
                self.ten_env.log_error(f"错误详情: {traceback.format_exc()}")
                # Clean up references even if there's an error
                self.client = None

        await self.audio_queue.put(AudioStreamEventType.FLUSH)
        await self.audio_queue.put(AudioStreamEventType.CLOSE)

        if self.client_running_task:
            await self.client_running_task

        self.client_running_task = None
        self.ten_env.log_info("[关闭连接] Speechmatics 服务已停止")

    async def _client_run(self):
        last_connect_time = 0
        retry_interval = 0.5
        max_retry_interval = 30.0

        while not self.client_needs_stopping:
            try:
                current_time = asyncio.get_event_loop().time()
                if current_time - last_connect_time < retry_interval:
                    await asyncio.sleep(retry_interval)

                last_connect_time = current_time

                # Mark WebSocket as connecting
                self.websocket_connected = False

                await run_asr_client(self)

                retry_interval = 0.5

            except Exception as e:
                self.ten_env.log_error(f"[客户端] 运行时发生错误: {e}")
                import traceback
                self.ten_env.log_error(f"错误详情: {traceback.format_exc()}")
                retry_interval = min(retry_interval * 2, max_retry_interval)
                error = ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.FATAL_ERROR.value,
                    message=str(e),
                )
                asyncio.create_task(self._emit_error(error, None))

            if self.client_needs_stopping:
                break

    async def internal_drain_mute_pkg(self):
        # we push some silence pkg to the queue
        # to trigger the final recognition result.
        await self.audio_stream.push_mute_pkg()

    async def internal_drain_disconnect(self):
        await self.audio_queue.put(AudioStreamEventType.FLUSH)
        await self.audio_queue.put(AudioStreamEventType.CLOSE)

        # wait for the client to auto reconnect

    def _handle_recognition_started(self, msg):
        # Mark WebSocket as connected when recognition starts
        self.websocket_connected = True
        self.sent_user_audio_duration_ms_before_last_reset += (
            self.timeline.get_total_user_audio_duration()
        )
        self.timeline.reset()
        if self.on_asr_open:
            self.connected = True
            asyncio.create_task(self.on_asr_open())

    def _handle_partial_transcript(self, msg):
        try:
            metadata = msg.get("metadata", {})
            text = metadata.get("transcript", "")

            # 只在文本非空时才返回数据给前端
            if not text:
                return

            start_ms = metadata.get("start_time", 0) * 1000
            end_ms = metadata.get("end_time", 0) * 1000
            _duration_ms = int(end_ms - start_ms)

            _actual_start_ms = int(
                self.timeline.get_audio_duration_before_time(start_ms)
                + self.sent_user_audio_duration_ms_before_last_reset
            )

            # 从 results[].alternatives[].speaker 提取 speaker（Speechmatics 返回的 S1/S2/S3）
            speaker = "NONE"  # 默认为 NONE（无法识别说话人）
            try:
                results = msg.get("results", [])
                if results and isinstance(results, list) and len(results) > 0:
                    result = results[0]
                    if result and isinstance(result, dict):
                        alternatives = result.get("alternatives", [])
                        if alternatives and isinstance(alternatives, list) and len(alternatives) > 0:
                            alternative = alternatives[0]
                            if alternative and isinstance(alternative, dict):
                                speaker_from_result = alternative.get("speaker", "")
                                if speaker_from_result and isinstance(speaker_from_result, str) and speaker_from_result.strip():
                                    speaker = speaker_from_result.strip()
            except Exception as e:
                pass  # Silently use default speaker "NONE"

            result_metadata = {"speaker": speaker}

            asr_result = ASRResult(
                text=text,
                final=False,
                start_ms=_actual_start_ms,
                duration_ms=_duration_ms,
                language=self.config.language,
                words=[],
                metadata=result_metadata,
            )
            if self.on_asr_result:
                asyncio.create_task(self.on_asr_result(asr_result))
        except Exception as e:
            self.ten_env.log_error(f"处理转录结果时发生错误: {e}")
            import traceback
            self.ten_env.log_error(f"错误详情: {traceback.format_exc()}")
            error = ModuleError(
                module=ModuleType.ASR,
                code=ModuleErrorCode.FATAL_ERROR.value,
                message=str(e),
            )
            asyncio.create_task(self._emit_error(error, None))

    def _handle_transcript_word_final_mode(self, msg):
        try:
            metadata = msg.get("metadata", {})
            text = metadata.get("transcript", "")

            # 只在文本非空时才返回数据给前端
            if not text:
                return

            start_ms = metadata.get("start_time", 0) * 1000
            end_ms = metadata.get("end_time", 0) * 1000
            _duration_ms = int(end_ms - start_ms)
            _actual_start_ms = int(
                self.timeline.get_audio_duration_before_time(start_ms)
                + self.sent_user_audio_duration_ms_before_last_reset
            )

            # 从 results[].alternatives[].speaker 提取 speaker（Speechmatics 返回的 S1/S2/S3）
            speaker = "NONE"  # 默认为 NONE（无法识别说话人）
            try:
                results = msg.get("results", [])
                if results and isinstance(results, list) and len(results) > 0:
                    result = results[0]
                    if result and isinstance(result, dict):
                        alternatives = result.get("alternatives", [])
                        if alternatives and isinstance(alternatives, list) and len(alternatives) > 0:
                            alternative = alternatives[0]
                            if alternative and isinstance(alternative, dict):
                                speaker_from_result = alternative.get("speaker", "")
                                if speaker_from_result and isinstance(speaker_from_result, str) and speaker_from_result.strip():
                                    speaker = speaker_from_result.strip()
            except Exception as e:
                pass  # Silently use default speaker "NONE"

            result_metadata = {"speaker": speaker}

            asr_result = ASRResult(
                text=text,
                final=True,
                start_ms=_actual_start_ms,
                duration_ms=_duration_ms,
                language=self.config.language,
                words=[],
                metadata=result_metadata,
            )

            if self.on_asr_result:
                asyncio.create_task(self.on_asr_result(asr_result))
        except Exception as e:
            self.ten_env.log_error(f"处理转录结果时发生错误: {e}")
            import traceback
            self.ten_env.log_error(f"错误详情: {traceback.format_exc()}")
            error = ModuleError(
                module=ModuleType.ASR,
                code=ModuleErrorCode.FATAL_ERROR.value,
                message=str(e),
            )
            asyncio.create_task(self._emit_error(error, None))

    def _handle_transcript_sentence_final_mode(self, msg):
        try:
            results = msg.get("results", {})

            for result in results:
                # Get the first candidate
                alternatives = result.get("alternatives", [])
                if alternatives:
                    text = alternatives[0].get("content", "")
                    speaker = alternatives[0].get("speaker", "")
                    if text:
                        start_ms = result.get("start_time", 0) * 1000
                        end_ms = result.get("end_time", 0) * 1000
                        duration_ms = int(end_ms - start_ms)
                        actual_start_ms = int(
                            self.timeline.get_audio_duration_before_time(
                                start_ms
                            )
                            + self.sent_user_audio_duration_ms_before_last_reset
                        )
                        result_type = result.get("type", "")
                        is_punctuation = result_type == "punctuation"
                        channel = result.get("channel", "")

                        word = SpeechmaticsASRWord(
                            word=text,
                            start_ms=actual_start_ms,
                            duration_ms=duration_ms,
                            is_punctuation=is_punctuation,
                            speaker=speaker,
                            channel=channel,
                        )
                        self.cache_words.append(word)

                if result.get("is_eos") == True:
                    sentence = convert_words_to_sentence(
                        self.cache_words, self.config
                    )
                    start_ms = get_sentence_start_ms(self.cache_words)
                    duration_ms = get_sentence_duration_ms(self.cache_words)

                    # Extract speaker/channel from cached words (use first non-empty)
                    result_metadata = {}
                    for w in self.cache_words:
                        if w.speaker and not result_metadata.get("speaker"):
                            result_metadata["speaker"] = w.speaker
                        if w.channel and not result_metadata.get("channel"):
                            result_metadata["channel"] = w.channel

                    word_payload = self.get_words(self.cache_words)

                    asr_result = ASRResult(
                        text=sentence,
                        final=True,
                        start_ms=start_ms,
                        duration_ms=duration_ms,
                        language=self.config.language,
                        words=word_payload,
                        metadata=result_metadata,
                    )

                    if self.on_asr_result:
                        asyncio.create_task(self.on_asr_result(asr_result))
                    self.cache_words = []

            # if the transcript is not empty, send it as a partial transcript
            if self.cache_words:
                sentence = convert_words_to_sentence(
                    self.cache_words, self.config
                )
                start_ms = get_sentence_start_ms(self.cache_words)
                duration_ms = get_sentence_duration_ms(self.cache_words)

                # Extract speaker/channel from cached words
                result_metadata = {}
                for w in self.cache_words:
                    if w.speaker and not result_metadata.get("speaker"):
                        result_metadata["speaker"] = w.speaker
                    if w.channel and not result_metadata.get("channel"):
                        result_metadata["channel"] = w.channel

                word_payload = self.get_words(self.cache_words)
                asr_result_partial = ASRResult(
                    text=sentence,
                    final=False,
                    start_ms=start_ms,
                    duration_ms=duration_ms,
                    language=self.config.language,
                    words=word_payload,
                    metadata=result_metadata,
                )

                if self.on_asr_result:
                    asyncio.create_task(self.on_asr_result(asr_result_partial))
        except Exception as e:
            self.ten_env.log_error(f"处理转录结果时发生错误: {e}")
            error = ModuleError(
                module=ModuleType.ASR,
                code=ModuleErrorCode.FATAL_ERROR.value,
                message=str(e),
            )

            asyncio.create_task(self._emit_error(error, None))

    def _handle_end_transcript(self, msg):
        self.websocket_connected = False
        self.connected = False
        if self.on_asr_close:
            asyncio.create_task(self.on_asr_close())

    def _handle_info(self, msg):
        pass  # Remove info logs

    def _handle_warning(self, msg):
        self.ten_env.log_warn(f"[警告] 消息: {msg}")

    def _handle_error(self, error):
        self.ten_env.log_error(f"[错误] Speechmatics错误: {error}")
        error = ModuleError(
            module=ModuleType.ASR,
            code=ModuleErrorCode.NON_FATAL_ERROR.value,
            message=str(error),
        )

        asyncio.create_task(
            self._emit_error(
                error,
                None,
            )
        )

    def _handle_audio_event_started(self, msg):
        pass

    def _handle_audio_event_ended(self, msg):
        pass

    def get_words(self, words: List[SpeechmaticsASRWord]) -> List[Word]:
        """
        Get the cached words for sentence final mode.
        """
        new_words = []
        for w in words:
            new_words.append(
                {
                    "word": w.word,
                    "start_ms": w.start_ms,
                    "duration_ms": w.duration_ms,
                    "stable": True,
                }
            )
        return new_words

    async def _emit_error(
        self,
        error: ModuleError,
        vendor_info: Optional[ModuleErrorVendorInfo] = None,
    ):
        """
        Emit an error message to the extension.
        """
        self.ten_env.log_error(
            f"_emit_error, error: {error}, vendor_info: {vendor_info}"
        )
        if callable(self.on_asr_error):
            await self.on_asr_error(
                error, vendor_info
            )  # pylint: disable=not-callable

    def is_connected(self) -> bool:
        if self.client is None:
            return False

        # Check the internal state of the speechmatics client
        try:
            # Check multiple status indicators according to the speechmatics-python library
            session_running = getattr(self.client, "session_running", False)

            # If the client is stopping, consider it as not connected
            if self.client_needs_stopping:
                return False

            # Check both WebSocket and recognition state
            if not self.connected and not self.websocket_connected:
                return False

            return session_running
        except Exception as e:
            # If an exception occurs while checking the status, consider it as not connected
            return False

    def get_connection_info(self) -> dict:
        """Get detailed connection information for debugging"""
        info = {
            "client_exists": self.client is not None,
            "client_needs_stopping": self.client_needs_stopping,
            "session_id": self.session_id,
            "audio_queue_size": self.audio_queue.qsize(),
            "running_task_exists": self.client_running_task is not None,
            "is_connected": self.is_connected(),
        }

        if self.client:
            info.update(
                {
                    "session_running": getattr(
                        self.client, "session_running", False
                    ),
                    "client_type": type(self.client).__name__,
                }
            )

        return info
