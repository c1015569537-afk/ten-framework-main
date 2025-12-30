#
# This file is part of TEN Framework, an open source project.
# Licensed under the Apache License, Version 2.0.
# See the LICENSE file for more information.
#
import json
from ten_runtime import (
    AudioFrame,
    AsyncExtension,
    AsyncTenEnv,
)


class StreamIdAdapterExtension(AsyncExtension):
    def __init__(self, name: str):
        super().__init__(name)
        self._frame_count = 0

    async def on_init(self, ten_env: AsyncTenEnv) -> None:
        ten_env.log_info("[StreamIdAdapter] 初始化")

    async def on_start(self, ten_env: AsyncTenEnv) -> None:
        ten_env.log_info("[StreamIdAdapter] 启动")

        # TODO: read properties, initialize resources

    async def on_stop(self, ten_env: AsyncTenEnv) -> None:
        ten_env.log_debug("on_stop")

        # TODO: clean up resources

    async def on_deinit(self, ten_env: AsyncTenEnv) -> None:
        ten_env.log_debug("on_deinit")

    async def on_audio_frame(
        self, ten_env: AsyncTenEnv, frame: AudioFrame
    ) -> None:
        audio_frame_name = frame.get_name()

        stream_id, stream_err = frame.get_property_int("stream_id")

        # 处理 stream_id 为 None 或错误的情况
        if stream_err or stream_id is None:
            stream_id = 0  # 默认为 0

        # Try multiple methods to extract remote_user_id
        remote_user_id_str = None
        found_remote_id = False

        # Method 1: Try as string (from manifest.json definition)
        try:
            user_id_str, user_err = frame.get_property_string("remote_user_id")
            if user_err is None and user_id_str:
                remote_user_id_str = user_id_str
                found_remote_id = True
        except Exception as e:
            ten_env.log_debug(f"[StreamIdAdapter] get_property_string failed: {e}")

        # Method 2: Try as integer (fallback)
        if not found_remote_id:
            try:
                user_id_int, user_err = frame.get_property_int("remote_user_id")
                if user_err is None and user_id_int != 0:
                    remote_user_id_str = str(user_id_int)
                    found_remote_id = True
            except Exception as e:
                ten_env.log_debug(f"[StreamIdAdapter] get_property_int failed: {e}")

        # Method 3: Try as uint64 (if available)
        if not found_remote_id:
            try:
                user_id_int, user_err = frame.get_property_uint64("remote_user_id")
                if user_err is None and user_id_int != 0:
                    remote_user_id_str = str(user_id_int)
                    found_remote_id = True
            except Exception as e:
                ten_env.log_debug(f"[StreamIdAdapter] get_property_uint64 failed: {e}")

        # Fallback: use stream_id as speaker identifier
        if not found_remote_id:
            remote_user_id_str = str(stream_id) if stream_id is not None else "0"

        metadata = {"session_id": f"{stream_id}"}
        metadata["speaker"] = remote_user_id_str

        # 每100帧打印一次日志
        self._frame_count += 1
        if self._frame_count % 100 == 1:
            ten_env.log_debug(f"[StreamIdAdapter] 已处理 {self._frame_count} 帧, stream_id={stream_id}")

        frame.set_property_from_json(
            "metadata",
            json.dumps(metadata),
        )

        await ten_env.send_audio_frame(audio_frame=frame)
