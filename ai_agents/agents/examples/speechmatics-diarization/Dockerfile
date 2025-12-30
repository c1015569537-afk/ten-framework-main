FROM ghcr.io/ten-framework/ten_agent_build:0.7.12 AS builder

ARG USE_AGENT=agents/examples/speechmatics-diarization

WORKDIR /app

COPY .env.example .env
COPY server/ server/
COPY agents/scripts/ agents/scripts/
# Fix: Add execute permissions to shell scripts
RUN find agents/scripts -type f -name "*.sh" -exec chmod +x {} +
COPY agents/ten_packages/ agents/ten_packages/
COPY ${USE_AGENT}/frontend/ ${USE_AGENT}/frontend/

# Copy tenapp files explicitly to avoid symlink issues
COPY ${USE_AGENT}/tenapp/go.* ${USE_AGENT}/tenapp/
COPY ${USE_AGENT}/tenapp/main.go ${USE_AGENT}/tenapp/
COPY ${USE_AGENT}/tenapp/manifest* ${USE_AGENT}/tenapp/
COPY ${USE_AGENT}/tenapp/property.json ${USE_AGENT}/tenapp/
COPY ${USE_AGENT}/tenapp/scripts/ ${USE_AGENT}/tenapp/scripts/
# Fix: Add execute permissions to tenapp scripts
RUN find ${USE_AGENT}/tenapp/scripts -type f -name "*.sh" -exec chmod +x {} +

# Copy extension directories that are actual directories (not symlinks)
COPY ${USE_AGENT}/tenapp/ten_packages/extension/main_python/ ${USE_AGENT}/tenapp/ten_packages/extension/main_python/

# Copy other example files
COPY ${USE_AGENT}/README.md ${USE_AGENT}/
COPY ${USE_AGENT}/Taskfile*.yml ${USE_AGENT}/

RUN cd /app/${USE_AGENT} && \
  task install && task release

# Build frontend in builder stage (bun already done by task install)
WORKDIR /app/${USE_AGENT}/frontend
RUN NEXT_PUBLIC_EDIT_GRAPH_MODE=false bun run build
WORKDIR /app

FROM ubuntu:22.04

ARG USE_AGENT=agents/examples/speechmatics-diarization

RUN apt-get update && apt-get install -y --no-install-recommends \
  libasound2 \
  libgstreamer1.0-dev \
  libunwind-dev \
  libc++1 \
  libssl-dev \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  jq vim \
  ca-certificates \
  curl \
  unzip \
  procps \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Install Node.js 20
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get install -y nodejs

# Install Bun using the official install script with retry
RUN for i in 1 2 3 4 5; do \
    curl --http1.1 -fsSL https://bun.com/install | bash && break || sleep 10; \
    done
# Add Bun to the PATH (if not already added by the install script)
ENV PATH="/root/.bun/bin:$PATH"

# Install Task
RUN sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

WORKDIR /app

COPY --from=builder /app/${USE_AGENT}/tenapp/.release/ /app/agents/
COPY --from=builder /app/server/bin/api /app/server/bin/api
COPY --from=builder /usr/local/lib /usr/local/lib
COPY --from=builder /usr/lib/python3 /usr/lib/python3
COPY --from=builder /usr/local/bin/tman /usr/local/bin/tman

# 添加权限
RUN find /app -type d -name "bin" -exec chmod -R +x {} + && \
  chmod +x /usr/local/bin/tman

# Ensure pgrep is executable for all users (关键修复)
RUN chmod +x /bin/pgrep  # 这是添加的行 - 确保pgrep有执行权限

# Copy built frontend from builder stage
COPY --from=builder /app/${USE_AGENT}/frontend/ /app/frontend/

# Copy Docker Taskfile
COPY --from=builder /app/${USE_AGENT}/Taskfile.docker.yml /app/Taskfile.docker.yml

EXPOSE 8881  3000

ENTRYPOINT ["task", "-t", "Taskfile.docker.yml", "run-prod"]
