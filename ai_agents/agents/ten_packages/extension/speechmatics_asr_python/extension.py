from datetime import datetime
import os
from typing import Optional
import asyncio

from typing_extensions import override
from .const import DUMP_FILE_NAME
from ten_ai_base.asr import (
    ASRBufferConfig,
    ASRBufferConfigModeDiscard,
    ASRResult,
    AsyncASRBaseExtension,
)
from ten_ai_base.message import (
    ModuleError,
    ModuleErrorVendorInfo,
    ModuleErrorCode,
    ModuleType,
)
from ten_runtime import (
    AsyncTenEnv,
    AudioFrame,
)
from ten_ai_base.const import (
    LOG_CATEGORY_VENDOR,
    LOG_CATEGORY_KEY_POINT,
)

from ten_ai_base.dumper import Dumper
from .reconnect_manager import ReconnectManager
from .config import SpeechmaticsASRConfig
from .asr_client import SpeechmaticsASRClient


class SpeechmaticsASRExtension(AsyncASRBaseExtension):
    """Speechmatics ASR Extension"""

    def __init__(self, name: str):
        super().__init__(name)
        self.ten_env: AsyncTenEnv = None
        self.audio_dumper: Optional[Dumper] = None
        self.last_finalize_timestamp: int = 0

        self.client: SpeechmaticsASRClient | None = None
        self.config: SpeechmaticsASRConfig | None = None

        # Reconnection manager
        self.reconnect_manager: Optional[ReconnectManager] = None

    @override
    async def on_deinit(self, ten_env: AsyncTenEnv) -> None:
        """Deinitialize extension"""
        await super().on_deinit(ten_env)
        if self.audio_dumper:
            await self.audio_dumper.stop()
            self.audio_dumper = None

    @override
    def vendor(self) -> str:
        """Get ASR vendor name"""
        return "speechmatics"

    @override
    async def on_init(self, ten_env: AsyncTenEnv) -> None:
        """Initialize extension"""
        await super().on_init(ten_env)
        self.ten_env = ten_env
        # Initialize reconnection manager
        self.reconnect_manager = ReconnectManager(logger=ten_env)

        # Counter for audio frames (每100帧打印一次)
        self.audio_frame_count = 0

        # 保存运行时语言配置（如果存在）
        self.runtime_language = None

        # 在初始化时尝试读取运行时语言配置
        try:
            language_prop, language_err = await ten_env.get_property_string("language")
            if language_err is None and language_prop and language_prop.strip():
                self.runtime_language = language_prop.strip()
        except Exception:
            pass

        config_json, _ = await ten_env.get_property_to_json("")
        ten_env.log_info(f"[配置调试] 原始 property JSON: {config_json[:500]}")

        try:
            temp_config = SpeechmaticsASRConfig.model_validate_json(config_json)
            ten_env.log_info(f"[配置调试] 解析后的 config.language (默认): {temp_config.language}")
            ten_env.log_info(f"[配置调试] 解析后的 config.params: {temp_config.params}")

            if temp_config.uri == "":
                temp_config.uri = "wss://eu2.rt.speechmatics.com/v2"

            self.config = temp_config
            self.config.update(self.config.params)

            ten_env.log_info(f"[配置调试] update() 之后的 config.language: {self.config.language}")
            ten_env.log_info(f"[配置调试] runtime_language: {self.runtime_language}")

            # 重新应用运行时语言配置（防止被 params 默认值覆盖）
            if self.runtime_language:
                self.config.language = self.runtime_language
                ten_env.log_info(f"[配置调试] 应用 runtime_language 后的 config.language: {self.config.language}")
            if self.config.dump:
                dump_file_path = os.path.join(
                    self.config.dump_path, DUMP_FILE_NAME
                )
                self.audio_dumper = Dumper(dump_file_path)

        except Exception as e:
            ten_env.log_error(f"无效的 Speechmatics ASR 配置: {e}")
            import traceback
            ten_env.log_error(f"错误详情: {traceback.format_exc()}")
            self.config = SpeechmaticsASRConfig.model_validate_json("{}")
            await self.send_asr_error(
                ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.FATAL_ERROR.value,
                    message=str(e),
                ),
            )

    @override
    async def on_audio_frame(self, ten_env: AsyncTenEnv, audio_frame: AudioFrame) -> None:
        """Override to add debug logging"""
        self.audio_frame_count += 1
        # Call parent implementation
        await super().on_audio_frame(ten_env, audio_frame)

    @override
    async def on_configure(self, ten_env: AsyncTenEnv) -> None:
        """Handle runtime configuration changes - support hot reload language"""
        await super().on_configure(ten_env)

        # 尝试从运行时属性获取语言配置
        try:
            language_prop = await ten_env.get_property_string("language")
            if language_prop and language_prop.strip():
                new_language = language_prop.strip()
                old_language = self.config.language if self.config else ""

                # 检查语言是否真的改变了
                if new_language != old_language:
                    # 更新语言配置
                    self.runtime_language = new_language

                    # 如果已有活跃连接，需要重新连接
                    if self.is_connected():
                        try:
                            # 断开旧连接
                            await self.stop_connection()

                            # 等待短暂时间确保资源清理
                            await asyncio.sleep(0.5)

                            # 重新建立连接（会使用新的语言配置）
                            await self.start_connection()

                            ten_env.log_info(f"[配置] 语言已切换: {new_language}")
                        except Exception as e:
                            ten_env.log_error(f"[配置] 语言切换失败: {e}")
                            import traceback
                            ten_env.log_error(f"错误详情: {traceback.format_exc()}")
        except Exception:
            # 如果没有语言属性，忽略
            pass

    @override
    async def start_connection(self) -> None:
        """Start ASR connection"""
        assert self.config is not None
        self.ten_env.log_info("[连接] 正在启动 Speechmatics ASR 连接")

        # 重置音频帧计数器
        self.audio_frame_count = 0

        try:
            # 检查是否有运行时语言配置
            language_to_use = self.config.language
            self.ten_env.log_info(f"[连接调试] 初始 config.language: {language_to_use}")

            if self.runtime_language:
                language_to_use = self.runtime_language
                self.ten_env.log_info(f"[连接调试] 使用 runtime_language: {language_to_use}")

            # 更新配置语言
            self.config.language = language_to_use
            self.ten_env.log_info(f"[连接调试] 最终使用的语言: {self.config.language}")

            # Check required credentials using current API key
            current_key = self.config.get_current_key()
            if not current_key or current_key.strip() == "":
                error_msg = "Speechmatics API 密钥未提供或为空"
                self.ten_env.log_error(error_msg)
                await self.send_asr_error(
                    ModuleError(
                        module=ModuleType.ASR,
                        code=ModuleErrorCode.FATAL_ERROR.value,
                        message=error_msg,
                    ),
                )
                return

            # Log current API key info
            if self.config.has_multiple_keys():
                self.ten_env.log_info(f"[API密钥] 使用密钥 #{self.config.current_key_index + 1}/{len(self.config.api_keys)}")

            # Start audio dumper
            if self.audio_dumper:
                await self.audio_dumper.start()

            if self.client is None:
                self.client = SpeechmaticsASRClient(
                    self.config,
                    self.ten_env,
                    self.audio_timeline,  # Duration getter
                )
                self.client.on_asr_open = self.on_asr_open
                self.client.on_asr_close = self.on_asr_close
                self.client.on_asr_result = self.on_asr_result
                self.client.on_asr_error = self.on_asr_error
            return await self.client.start()

        except Exception as e:
            self.ten_env.log_error(
                f"启动 Speechmatics ASR 连接失败: {e}"
            )
            import traceback
            self.ten_env.log_error(
                f"错误详情: {traceback.format_exc()}"
            )
            await self.send_asr_error(
                ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.NON_FATAL_ERROR.value,
                    message=str(e),
                ),
            )

    async def on_asr_open(self) -> None:
        """Handle callback when connection is established"""
        # Notify reconnect manager of successful connection
        if self.reconnect_manager:
            self.reconnect_manager.mark_connection_successful()

    async def on_asr_result(self, message_data: ASRResult) -> None:
        """Handle recognition result callback"""
        await self._handle_asr_result(
            text=message_data.text,
            final=message_data.final,
            start_ms=message_data.start_ms,
            duration_ms=message_data.duration_ms,
            language=message_data.language,
            metadata=message_data.metadata,
        )

    async def on_asr_error(
        self, error_msg: ModuleError, error_code: Optional[int] = None
    ) -> None:
        """Handle error callback with API key rotation on quota errors"""
        self.ten_env.log_error(
            f"[错误] 供应商错误: {error_msg.message}, 错误代码: {error_code}"
        )

        # Check if error indicates quota/credit issues
        quota_error = False
        error_lower = error_msg.message.lower()

        # Speechmatics quota error indicators
        quota_keywords = [
            "quota", "credit", "limit", "exceeded",
            "401", "403", "402",  # HTTP status codes
            "unauthorized", "forbidden", "payment required",
            "no credits", "insufficient", "balance"
        ]

        if any(keyword in error_lower for keyword in quota_keywords):
            quota_error = True

        if quota_error and self.config.has_multiple_keys():
            self.ten_env.log_warn(f"[API密钥] 检测到额度不足错误，尝试切换到下一个API密钥")
            await self._rotate_api_key_and_reconnect()
        else:
            # Send error information if not handled by rotation
            await self.send_asr_error(
                ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.NON_FATAL_ERROR.value,
                    message=error_msg.message,
                ),
                ModuleErrorVendorInfo(
                    vendor=self.vendor(),
                    code=str(error_code) if error_code else "unknown",
                    message=error_msg.message,
                ),
            )

    async def _rotate_api_key_and_reconnect(self) -> None:
        """Rotate to next API key and reconnect"""
        if not self.config.has_multiple_keys():
            self.ten_env.log_error("[API密钥] 没有可用的备用API密钥")
            return

        # Check if there are unattempted keys
        if not self.config.has_unattempted_keys():
            self.ten_env.log_error("[API密钥] 所有API密钥都已尝试，无法继续切换")
            await self.send_asr_error(
                ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.FATAL_ERROR.value,
                    message="所有API密钥都已尝试过且无法连接",
                ),
            )
            return

        old_index = self.config.current_key_index

        try:
            # Get next key (this will mark current as attempted)
            next_key = self.config.get_next_key()
            new_index = self.config.current_key_index

            self.ten_env.log_info(f"[API密钥] 切换API密钥: #{old_index + 1} → #{new_index + 1}")

            # Stop current connection
            if self.is_connected():
                await self.stop_connection()
                await asyncio.sleep(0.5)

            # Start new connection with new key
            await self.start_connection()
            self.ten_env.log_info(f"[API密钥] ✅ 成功切换到API密钥 #{new_index + 1} 并重新连接")

        except ValueError as e:
            # All keys have been attempted
            self.ten_env.log_error(f"[API密钥] {str(e)}")
            await self.send_asr_error(
                ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.FATAL_ERROR.value,
                    message=f"所有API密钥额度已用尽或无效: {str(e)}",
                ),
            )
        except Exception as e:
            # Connection failed, try next key if available
            self.ten_env.log_error(f"[API密钥] ❌ 切换后重新连接失败: {e}")

            # Recursively try next key if there are unattempted ones
            if self.config.has_unattempted_keys():
                self.ten_env.log_info(f"[API密钥] 尝试下一个API密钥...")
                await self._rotate_api_key_and_reconnect()
            else:
                self.ten_env.log_error("[API密钥] 所有API密钥都已尝试，无法连接")
                await self.send_asr_error(
                    ModuleError(
                        module=ModuleType.ASR,
                        code=ModuleErrorCode.FATAL_ERROR.value,
                        message=f"所有API密钥额度已用尽或无效: {str(e)}",
                    ),
                )

    async def on_asr_close(self) -> None:
        """Handle callback when connection is closed"""
        pass  # 删除连接关闭日志

    @override
    async def finalize(self, _session_id: Optional[str]) -> None:
        """Finalize recognition"""
        assert self.config is not None

        self.last_finalize_timestamp = int(datetime.now().timestamp() * 1000)

        if self.config.drain_mode == "mute_pkg":
            return await self._handle_finalize_mute_pkg()
        return await self._handle_finalize_disconnect()

    async def _handle_asr_result(
        self,
        text: str,
        final: bool,
        start_ms: int = 0,
        duration_ms: int = 0,
        language: str = "",
        metadata: Optional[dict] = None,
    ):
        """Process ASR recognition result - 每次都打印说话人分离结果"""
        assert self.config is not None

        # 提取说话人信息
        speaker = metadata.get("speaker", "") if metadata else ""
        channel = metadata.get("channel", "") if metadata else ""

        # 打印说话人分离结果（只在有文本时）
        if text and text.strip():
            speaker_info = f"[{speaker}]" if speaker else ""
            channel_info = f"(ch:{channel})" if channel else ""
            self.ten_env.log_info(f"[ASR返回] {speaker_info} {channel_info} {text.strip()}")

        asr_result = ASRResult(
            text=text,
            final=final,
            start_ms=start_ms,
            duration_ms=duration_ms,
            language=language,
            words=[],
            metadata=metadata if metadata is not None else {},
        )

        if final:
            await self._finalize_end()

        await self.send_asr_result(asr_result)

    async def _handle_finalize_disconnect(self):
        """Handle disconnect mode finalization"""
        if self.client:
            await self.client.internal_drain_disconnect()

    async def _handle_finalize_mute_pkg(self):
        """Handle mute package mode finalization"""
        if self.client:
            await self.client.internal_drain_mute_pkg()

    async def _handle_reconnect(self):
        """Handle reconnection with proper cleanup"""
        if not self.reconnect_manager:
            self.ten_env.log_error("重连管理器未初始化")
            return

        # Check if retry is still possible
        if not self.reconnect_manager.can_retry():
            self.ten_env.log_error("不再允许重连尝试")
            await self.send_asr_error(
                ModuleError(
                    module=ModuleType.ASR,
                    code=ModuleErrorCode.NON_FATAL_ERROR.value,
                    message="不再允许重连尝试",
                )
            )
            return

        # Ensure old connection is fully cleaned up
        if self.client:
            await self.stop_connection()
            # Wait additional time to ensure resource release
            await asyncio.sleep(1.0)

        # Attempt reconnection
        await self.reconnect_manager.handle_reconnect(
            connection_func=self.start_connection,
            error_handler=self.send_asr_error,
        )

    async def _finalize_end(self) -> None:
        """Handle finalization end logic"""
        if self.last_finalize_timestamp != 0:
            self.last_finalize_timestamp = 0
            await self.send_asr_finalize_end()

    async def stop_connection(self) -> None:
        """Stop ASR connection with enhanced cleanup"""
        try:
            if self.client:
                self.ten_env.log_info("[断开连接] 正在停止 Speechmatics ASR 连接")

                # Stop the client and wait for completion
                await self.client.stop()

                # Wait a short time to ensure cleanup is complete
                await asyncio.sleep(0.1)

            self.ten_env.log_info("[断开连接] Speechmatics ASR 连接已停止")

        except Exception as e:
            self.ten_env.log_error(
                f"[断开连接] 停止 Speechmatics ASR 连接时发生错误: {e}"
            )
            import traceback
            self.ten_env.log_error(
                f"错误详情: {traceback.format_exc()}"
            )

    @override
    def is_connected(self) -> bool:
        """Check connection status"""
        if self.client is None:
            return False

        return self.client.is_connected()

    @override
    def buffer_strategy(self) -> ASRBufferConfig:
        """Buffer strategy configuration"""
        return ASRBufferConfigModeDiscard()

    @override
    def input_audio_sample_rate(self) -> int:
        """Input audio sample rate"""
        assert self.config is not None
        return self.config.sample_rate

    @override
    async def send_audio(
        self, frame: AudioFrame, _session_id: Optional[str]
    ) -> bool:
        """Send audio data"""
        assert self.config is not None

        # 统计发送的音频帧
        if not hasattr(self, '_send_audio_count'):
            self._send_audio_count = 0

        self._send_audio_count += 1

        try:
            # Only send audio if connected
            if not self.is_connected():
                # 每100帧打印一次丢弃日志
                if self._send_audio_count % 100 == 1:
                    self.ten_env.log_warn(
                        f"[音频发送] 第 {self._send_audio_count} 帧被丢弃 - ASR未连接"
                    )
                return False

            buf = frame.lock_buf()
            audio_data = bytes(buf)

            # 每100帧打印一次发送日志
            if self._send_audio_count % 100 == 1:
                self.ten_env.log_info(
                    f"[音频发送] 第 {self._send_audio_count} 帧已发送到Speechmatics, "
                    f"大小={len(audio_data)}字节"
                )

            # Dump audio data
            if self.audio_dumper:
                await self.audio_dumper.push_bytes(audio_data)

            if self.client:
                await self.client.recv_audio_frame(frame, _session_id)

            frame.unlock_buf(buf)
            return True

        except Exception as e:
            self.ten_env.log_error(
                f"发送音频到 Speechmatics ASR 时发生错误: {e}"
            )
            import traceback
            self.ten_env.log_error(
                f"错误详情: {traceback.format_exc()}"
            )
            # Try to unlock even if there's an error
            try:
                frame.unlock_buf(buf)
            except:
                pass
            return False
