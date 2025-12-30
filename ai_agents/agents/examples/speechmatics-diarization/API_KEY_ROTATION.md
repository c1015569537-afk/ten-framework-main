# API Key 自动切换配置说明

## 📋 功能概述

当 Speechmatics API 额度不足时，系统会自动切换到下一个可用的 API key，确保服务不中断。

---

## 🚀 快速开始

### 方式一：使用环境变量（推荐）

```bash
# 设置多个 Speechmatics API key
export SPEECHMATICS_API_KEY_1="your_first_key_here"
export SPEECHMATICS_API_KEY_2="your_second_key_here"
export SPEECHMATICS_API_KEY_3="your_third_key_here"
```

### 方式二：直接配置

编辑 `tenapp/property.json`:

```json
{
  "params": {
    "api_keys": [
      "your_first_key_here",
      "your_second_key_here",
      "your_third_key_here"
    ]
  }
}
```

---

## 📖 配置说明

### 单 API Key 配置（向后兼容）

```json
{
  "params": {
    "key": "your_api_key_here"
  }
}
```

### 多 API Key 配置（推荐）

```json
{
  "params": {
    "api_keys": [
      "key_1_priority_1",
      "key_2_priority_2",
      "key_3_priority_3"
    ]
  }
}
```

**优先级**：
- 如果配置了 `api_keys`，系统将优先使用它
- 如果 `api_keys` 为空或未配置，将回退到使用 `key`

---

## 🔄 工作原理

### 1️⃣ **初始连接**

```
系统启动 → 使用 api_keys[0] → 连接 Speechmatics
```

**日志示例**：
```
[API密钥] 使用密钥 #1/3
[连接] 正在启动 Speechmatics ASR 连接
```

---

### 2️⃣ **检测额度不足**

当以下错误发生时，系统会自动触发切换：

- HTTP 401 (Unauthorized)
- HTTP 403 (Forbidden)
- HTTP 402 (Payment Required)
- 错误消息包含：`quota`, `credit`, `limit`, `exceeded`, `insufficient`, `balance`

**日志示例**：
```
[错误] 供应商错误: Insufficient credits, 错误代码: 403
[API密钥] 检测到额度不足错误，尝试切换到下一个API密钥
```

---

### 3️⃣ **自动切换**

```
检测错误 → 停止当前连接 → 切换到下一个 key → 重新连接
```

**日志示例**：
```
[API密钥] 切换API密钥: #1 → #2
[断开连接] 正在停止 Speechmatics ASR 连接
[API密钥] 使用密钥 #2/3
[连接] 正在启动 Speechmatics ASR 连接
[API密钥] ✅ 成功切换到API密钥 #2 并重新连接
```

---

### 4️⃣ **循环重试**

如果所有 key 都失败：

```
尝试 key #1 → 失败
尝试 key #2 → 失败
尝试 key #3 → 失败
→ 停止并报告"所有API密钥额度已用尽"
```

**日志示例**：
```
[API密钥] 切换API密钥: #3 → #1
[API密钥] ❌ 切换后重新连接失败: ...
[API密钥] 尝试下一个API密钥...
[API密钥] 所有API密钥都已尝试，无法连接
```

---

## 🛠️ 配置示例

### 示例 1：开发环境

```json
{
  "params": {
    "api_keys": [
      "${env:SPEECHMATICS_API_KEY_DEV_1}",
      "${env:SPEECHMATICS_API_KEY_DEV_2}"
    ],
    "language": "en"
  }
}
```

### 示例 2：生产环境

```json
{
  "params": {
    "api_keys": [
      "${env:SPEECHMATICS_API_KEY_PROD_1}",
      "${env:SPEECHMATICS_API_KEY_PROD_2}",
      "${env:SPEECHMATICS_API_KEY_PROD_3}",
      "${env:SPEECHMATICS_API_KEY_PROD_4}"
    ],
    "language": "en",
    "diarization": "speaker"
  }
}
```

### 示例 3：混合配置（多账号）

```json
{
  "params": {
    "api_keys": [
      "account_a_key_1",  // Account A - Key 1
      "account_a_key_2",  // Account A - Key 2
      "account_b_key_1"   // Account B - Key 1
    ]
  }
}
```

---

## 🔍 监控与调试

### 查看当前使用的 API Key

**日志中会显示**：
```
[API密钥] 使用密钥 #2/4
```

这表示正在使用第 2 个 key（共 4 个）。

---

### 查看 API Key 状态

通过日志可以追踪：
- ✅ 连接成功时的 key 索引
- ❌ 连接失败时的错误信息
- 🔄 自动切换时的 key 变更

---

## ⚙️ 高级配置

### 环境变量配置

创建 `.env` 文件：

```bash
# Speechmatics API Keys (多个备用)
SPEECHMATICS_API_KEY_1=abcd1234key1...
SPEECHMATICS_API_KEY_2=abcd1234key2...
SPEECHMATICS_API_KEY_3=abcd1234key3...

# Legacy support (single key)
SPEECHMATICS_API_KEY=abcd1234key1...
```

### Docker Compose 配置

```yaml
version: '3.8'
services:
  speechmatics-asr:
    environment:
      - SPEECHMATICS_API_KEY_1=${SPEECHMATICS_KEY_1}
      - SPEECHMATICS_API_KEY_2=${SPEECHMATICS_KEY_2}
      - SPEECHMATICS_API_KEY_3=${SPEECHMATICS_KEY_3}
```

---

## 📊 最佳实践

### ✅ 推荐做法

1. **使用环境变量**：不要在代码中硬编码 API key
2. **至少配置 3 个 key**：确保有足够的备用
3. **定期轮换 key**：避免单个 key 额度用尽
4. **监控使用量**：及时充值或添加新的 key
5. **不同账号**：使用多个 Speechmatics 账号的 key

### ❌ 避免做法

1. ❌ 将 API key 提交到 Git 仓库
2. ❌ 只配置一个 key（无法自动切换）
3. ❌ 使用已过期的 key
4. ❌ 混用测试和生产环境的 key

---

## 🔧 故障排查

### 问题 1：所有 key 都无法连接

**症状**：
```
[API密钥] 所有API密钥都已尝试，无法连接
```

**解决方案**：
1. 检查所有 key 是否有效
2. 检查网络连接
3. 检查 Speechmatics 服务状态
4. 验证 key 格式是否正确

---

### 问题 2：自动切换后服务中断

**症状**：切换后音频处理停止

**解决方案**：
1. 检查切换日志，确认连接成功
2. 增加重连等待时间（修改 `asyncio.sleep(0.5)`）
3. 检查客户端状态，确认音频流是否正常

---

### 问题 3：配置了多个 key 但没有切换

**症状**：额度不足时直接报错，没有切换

**原因**：
- `api_keys` 配置格式错误
- 使用了 `key` 而不是 `api_keys`

**解决方案**：
```json
// ✅ 正确
{
  "api_keys": ["key1", "key2", "key3"]
}

// ❌ 错误
{
  "key": "key1"  // 单 key 模式，不会切换
}
```

---

## 📝 代码示例

### 检查当前配置

```python
# 在 extension.py 中
current_key = self.config.get_current_key()
has_multiple = self.config.has_multiple_keys()
current_index = self.config.current_key_index

print(f"当前使用: key #{current_index + 1}")
print(f"总 key 数量: {len(self.config.api_keys)}")
```

### 手动切换 key

```python
# 强制切换到下一个 key
next_key = self.config.get_next_key()
await self._rotate_api_key_and_reconnect()
```

---

## 🔐 安全建议

1. **使用密钥管理服务**：
   - AWS Secrets Manager
   - HashiCorp Vault
   - Azure Key Vault

2. **定期轮换密钥**：
   - 每 30-90 天更换一次
   - 使用密钥轮换自动化工具

3. **最小权限原则**：
   - 为每个环境使用单独的 key
   - 限制 key 的访问权限

---

## 📄 相关文档

- [Speechmatics API 文档](https://speechmatics.com/docs)
- [LARGE_MEETING_OPTIMIZATION.md](./LARGE_MEETING_OPTIMIZATION.md)
- [README.md](./README.md)

---

**文档版本**: 1.0.0
**最后更新**: 2025-01-28
**维护者**: TEN Framework Team
