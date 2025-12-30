import json
import time
from typing import Literal, Optional

from .agent.decorators import agent_event_handler
from ten_runtime import (
    AsyncExtension,
    AsyncTenEnv,
    Cmd,
    Data,
)

from .agent.agent import Agent
from .agent.events import (
    ASRResultEvent,
    LLMResponseEvent,
    ToolRegisterEvent,
    UserJoinedEvent,
    UserLeftEvent,
)
from .helper import _send_cmd, _send_data, parse_sentences
from .config import MainControlConfig  # assume extracted from your base model
from .game_logic import WhoLikesWhatGame

import uuid


class MainControlExtension(AsyncExtension):
    """
    The entry point of the agent module.
    Consumes semantic AgentEvents from the Agent class and drives the runtime behavior.
    """

    def __init__(self, name: str):
        super().__init__(name)
        self.ten_env: AsyncTenEnv = None
        self.agent: Agent = None
        self.config: MainControlConfig = None

        self.stopped: bool = False
        self._rtc_user_count: int = 0
        self.sentence_fragment: str = ""
        self.turn_id: int = 0
        self.session_id: str = "0"
        self.pending_response_target: Optional[str] = None
        self.game: Optional[WhoLikesWhatGame] = None

    def _current_metadata(self) -> dict:
        return {"session_id": self.session_id, "turn_id": self.turn_id}

    async def on_init(self, ten_env: AsyncTenEnv):
        self.ten_env = ten_env

        try:
            # Load config from runtime properties
            config_json, _ = await ten_env.get_property_to_json(None)
            self.config = MainControlConfig.model_validate_json(config_json)
        except Exception as e:
            ten_env.log_error(f"加载配置失败: {e}, 使用默认配置")
            import traceback
            ten_env.log_error(f"错误详情: {traceback.format_exc()}")
            self.config = MainControlConfig()

        try:
            self.agent = Agent(ten_env)
        except Exception as e:
            ten_env.log_error(f"创建代理失败: {e}")
            import traceback
            ten_env.log_error(f"错误详情: {traceback.format_exc()}")
            raise

        self.game = WhoLikesWhatGame(self)

        # Now auto-register decorated methods
        for attr_name in dir(self):
            fn = getattr(self, attr_name)
            event_type = getattr(fn, "_agent_event_type", None)
            if event_type:
                self.agent.on(event_type, fn)

    # === Register handlers with decorators ===
    @agent_event_handler(UserJoinedEvent)
    async def _on_user_joined(self, event: UserJoinedEvent):
        self._rtc_user_count += 1
        if self._rtc_user_count == 1 and self.config and self.config.greeting:
            await self._send_to_tts(self.config.greeting, True)
            # No label for assistant greeting
            await self._send_transcript(
                "assistant", self.config.greeting, True, 100
            )
        if self.game and not self.game.enrollment_prompted:
            await self.game.start_enrollment_flow()

    @agent_event_handler(UserLeftEvent)
    async def _on_user_left(self, event: UserLeftEvent):
        self._rtc_user_count -= 1

    @agent_event_handler(ToolRegisterEvent)
    async def _on_tool_register(self, event: ToolRegisterEvent):
        await self.agent.register_llm_tool(event.tool, event.source)

    @agent_event_handler(ASRResultEvent)
    async def _on_asr_result(self, event: ASRResultEvent):
        game = self.game
        if not game:
            return

        # 调试日志：打印收到的 ASRResultEvent 完整内容
        self.ten_env.log_info(
            f"[ASR 接收] 收到 ASR 结果: text='{event.text}', final={event.final}, "
            f"metadata={event.metadata}, metadata类型={type(event.metadata)}"
        )

        raw_session_id = event.metadata.get("session_id", "100")
        self.session_id = str(raw_session_id)
        stream_id = 100
        for candidate in (
            event.metadata.get("stream_id"),
            raw_session_id,
        ):
            try:
                if candidate is not None:
                    stream_id = int(candidate)
                    break
            except (TypeError, ValueError):
                continue
        else:
            self.ten_env.log_warn(
                f"[ASR] Unable to parse stream_id from metadata; defaulting to {stream_id}. metadata={event.metadata}"
            )

        # Extract speaker information for diarization
        speaker = event.metadata.get("speaker", "")
        channel = event.metadata.get("channel", "")

        # 详细日志：打印提取的 speaker 和 channel
        self.ten_env.log_info(
            f"[ASR 提取] speaker='{speaker}' (类型={type(speaker)}), "
            f"channel='{channel}' (类型={type(channel)}), speaker为空={not speaker}"
        )

        speaker_str = game.normalize_label(speaker)
        channel_str = game.normalize_label(channel)
        speaker_key = game.build_speaker_key(speaker_str, channel_str)

        # Log speechmatics diarization result (both partial and final)
        result_type = "最终结果" if event.final else "部分结果"
        if event.text:
            self.ten_env.log_info(
                f"[处理speechmatics diarization 结果] {result_type} - speaker='{speaker}', channel='{channel}', text='{event.text}', 原始metadata={event.metadata}"
            )

        if not event.text:
            return
        if event.final or len(event.text) > 2:
            await self._interrupt()

        # Send transcript with speaker info (S1, S2, or NONE if empty)
        await self._send_transcript(
            "user", event.text, event.final, stream_id, speaker=speaker
        )

    @agent_event_handler(LLMResponseEvent)
    async def _on_llm_response(self, event: LLMResponseEvent):
        target_player = self.pending_response_target
        if not event.is_final and event.type == "message":
            sentences, self.sentence_fragment = parse_sentences(
                self.sentence_fragment, event.delta
            )
            for s in sentences:
                if target_player:
                    await self._send_to_tts(s, False, target_player)

        if event.is_final and event.type == "message":
            remaining_text = self.sentence_fragment or ""
            self.sentence_fragment = ""
            if target_player and remaining_text:
                await self._send_to_tts(remaining_text, True, target_player)
            # Clear target when the turn is done
            self.pending_response_target = None

        # No label for assistant responses
        display_text = event.text
        if target_player and display_text:
            display_text = f"[{target_player}] {display_text}"
        await self._send_transcript(
            "assistant",
            display_text,
            event.is_final,
            100,
            data_type=("reasoning" if event.type == "reasoning" else "text"),
        )

    async def on_start(self, ten_env: AsyncTenEnv):
        pass

    async def on_stop(self, ten_env: AsyncTenEnv):
        self.stopped = True
        self.pending_response_target = None
        if self.game:
            self.game.reset_state()

        # Defensive check: only stop agent if it was successfully created
        if self.agent is not None:
            await self.agent.stop()

    async def on_cmd(self, ten_env: AsyncTenEnv, cmd: Cmd):
        if self.agent is not None:
            await self.agent.on_cmd(cmd)

    async def on_data(self, ten_env: AsyncTenEnv, data: Data):
        if self.agent is not None:
            await self.agent.on_data(data)

    # === helpers ===
    async def _send_transcript(
        self,
        role: str,
        text: str,
        final: bool,
        stream_id: int,
        data_type: Literal["text", "reasoning"] = "text",
        speaker: Optional[str] = None,
    ):
        """
        Sends the transcript (ASR or LLM output) to the message collector.
        """
        if data_type == "text":
            data = {
                "data_type": "transcribe",
                "role": role,
                "text": text,
                "text_ts": int(time.time() * 1000),
                "is_final": final,
                "stream_id": stream_id,
            }
            # Add speaker field for user role (S1, S2 from Speechmatics, or NONE if empty)
            if role == "user":
                speaker_value = speaker if speaker else "NONE"
                data["speaker"] = speaker_value
                # 详细日志：打印发送给前端的数据
                self.ten_env.log_info(
                    f"[发送给前端] 准备发送数据: role={role}, text='{text}', "
                    f"原始speaker参数='{speaker}' (类型={type(speaker)}), "
                    f"最终speaker字段='{speaker_value}', 完整data={data}"
                )
            await _send_data(
                self.ten_env,
                "message",
                "message_collector",
                data,
            )
        elif data_type == "reasoning":
            await _send_data(
                self.ten_env,
                "message",
                "message_collector",
                {
                    "data_type": "raw",
                    "role": role,
                    "text": json.dumps(
                        {
                            "type": "reasoning",
                            "data": {
                                "text": text,
                            },
                        }
                    ),
                    "text_ts": int(time.time() * 1000),
                    "is_final": final,
                    "stream_id": stream_id,
                },
            )
        # Simplified log without framework details
        if final and role == "user":
            self.ten_env.log_info(f"[发送给前端] role={role}, text='{text}', speaker={speaker if speaker else 'NONE'}")

    async def _send_to_tts(
        self, text: str, is_final: bool, target_player: Optional[str] = None
    ):
        """
        Sends a sentence to the TTS system.
        """
        request_id = f"tts-request-{self.turn_id}-{uuid.uuid4().hex[:8]}"
        metadata = self._current_metadata()
        if target_player:
            metadata = {**metadata, "target_player": target_player}
        await _send_data(
            self.ten_env,
            "tts_text_input",
            "tts",
            {
                "request_id": request_id,
                "text": text,
                "text_input_end": is_final,
                "metadata": metadata,
            },
        )

    async def _interrupt(self):
        """
        Interrupts ongoing LLM and TTS generation. Typically called when user speech is detected.
        """
        self.sentence_fragment = ""
        if self.agent is not None:
            await self.agent.flush_llm()
        await _send_data(
            self.ten_env, "tts_flush", "tts", {"flush_id": str(uuid.uuid4())}
        )
        await _send_cmd(self.ten_env, "flush", "agora_rtc")

    def _player_pronoun(self, player_name: str) -> tuple[str, str]:
        pronoun_map = {
            "Elliot": ("he", "loves"),
            "Musk": ("he", "loves"),
            "Taytay": ("she", "loves"),
        }
        return pronoun_map.get(player_name, ("they", "love"))
