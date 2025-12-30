# Who Likes What – Speaker Diarization Demo

This example demonstrates TEN Framework's speaker diarization capabilities using Speechmatics ASR in a conversational game called **Who Likes What**, where the agent figures out “who said what” across multiple voices.

## Features

- **Real-time speaker identification**: Automatically detects and labels different speakers (S1, S2, S3, etc.)
- **Configurable sensitivity**: Adjust how aggressively the system detects new speakers
- **Multi-speaker conversations**: Supports up to 100 speakers (configurable) and powers the Who Likes What game loop
- **Visual speaker labels**: Speaker information is displayed in the transcript UI so the agent can call players by name
- **Automatic API key rotation**: Seamlessly switches to backup API keys when quota is exceeded ✨ **NEW**

## Prerequisites

1. **Speechmatics API Key**: Get one from [Speechmatics](https://www.speechmatics.com/)
2. **Agora credentials**: For real-time audio streaming

## Setup

### 1. Set Environment Variables

Add to your `.env` file:

```bash
# Speechmatics API Keys (required for diarization)
# Quick start: All keys use the same value
SPEECHMATICS_API_KEY=your_speechmatics_api_key_here

# Production: Use different API keys for automatic failover
# SPEECHMATICS_API_KEY=key1  # First API key (primary)
# SPEECHMATICS_API_KEY=key2  # Second API key (backup)
# SPEECHMATICS_API_KEY=key3  # Third API key (backup)

# Agora (for RTC)
AGORA_APP_ID=your_agora_app_id_here
AGORA_APP_CERTIFICATE=your_agora_certificate_here
```

**💡 Tip**: The system is configured with 3 API key slots. Currently all use the same `SPEECHMATICS_API_KEY`. To enable automatic failover, set different values for each key slot in your `.env` file. See [API_KEY_ROTATION.md](./API_KEY_ROTATION.md) for details.

### 2. Install Dependencies

```bash
cd agents/examples/speechmatics-diarization
task install
```

This command will:
- Install required dependencies
- Configure the agent for speaker diarization
- Set up the graph with Speechmatics ASR

### 3. Run the Agent

```bash
cd agents/examples/speechmatics-diarization
task run
```

The agent will start with speaker diarization enabled.

4. **Access the application:**
   - Frontend: http://localhost:3000
   - API Server: http://localhost:8080
   - TMAN Designer: http://localhost:49483

## Configuration

You can customize diarization settings in `property.json`:

```json
{
  "params": {
    "key": "${env:SPEECHMATICS_API_KEY}",
    "language": "en",
    "sample_rate": 16000,
    "diarization": "speaker",
    "speaker_sensitivity": 0.5,
    "max_speakers": 10,
    "prefer_current_speaker": false
  }
}
```

### Diarization Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `diarization` | string | `"none"` | Diarization mode: `"none"`, `"speaker"`, `"channel"`, or `"channel_and_speaker"` |
| `max_speakers` | int | `50` | Maximum number of speakers (2-100) |
| `speaker_sensitivity` | float | `0.5` | Range 0-1. Higher values detect more unique speakers ✅ |
| `prefer_current_speaker` | bool | `false` | Reduce false speaker switches between similar voices ✅ |

**Note**: The current implementation uses `speechmatics-python==4.0.0`, which supports full diarization configuration including:
- `max_speakers`: Maximum number of speakers (2-100)
- `speaker_sensitivity`: Speaker detection sensitivity (0.0-1.0)
- `prefer_current_speaker`: Reduce false speaker switches

## How It Works

1. **Audio Input**: User speaks through the microphone
2. **Speechmatics ASR**: Transcribes audio AND identifies speakers
3. **Speaker Labels**: Each transcription includes speaker labels like `[S1]`, `[S2]`
4. **LLM Context**: Speaker information is passed to the LLM
5. **Response**: The agent responds, acknowledging different speakers

## Example Interaction

**Elliot**: "Hello, this is Elliot."

**Transcript**: "[Elliot] Hello, this is Elliot."

**Musk**: "This is Elon."

**Transcript**: "[Musk] This is Elon."

**Agent**: "Elliot's voice is locked in. Waiting for Taytay to give me a quick hello so I can lock in their voice."

## Troubleshooting

### No speaker labels appearing

- Verify `SPEECHMATICS_API_KEY` is set correctly
- Check that `diarization` is set to `"speaker"` in property.json
- Ensure multiple people are speaking (single speaker might always be labeled S1)

### Too many false speaker switches

- Increase `speaker_sensitivity` to detect speakers more aggressively
- Enable `prefer_current_speaker` to reduce false switches between similar voices
- Consider adjusting `max_speakers` to limit the number of detected speakers

### Not enough speakers detected

- Increase `max_speakers` if you expect more than the default number of speakers

## UI Customization

The playground UI automatically displays speaker labels in the transcript. To further customize the display, you can modify the `main_python` extension's `_on_asr_result` method in `extension.py`.

---

## Release as Docker image

**Note**: The following commands need to be executed outside of any Docker container.

### Build image
```bash
# Run at project root
cd ai_agents
docker build -f agents/examples/speechmatics-diarization/Dockerfile -t speechmatics-diarization-app .
```

### Run container
```bash
# Use local .env (optional)
docker run --rm -it \
  --env-file .env \
  -p 8080:8080 \
  -p 3000:3000 \
  speechmatics-diarization-app
```

### Access
- Frontend: http://localhost:3000
- API Server: http://localhost:8080

## Learn More

- [Speechmatics Diarization Docs](https://docs.speechmatics.com/speech-to-text/features/diarization)
- [TEN Framework Documentation](https://doc.theten.ai)
- [Voice Assistant Example](../voice-assistant/) for the base architecture

## License

Apache License 2.0
