FROM ubuntu:24.04

# 1. Tools und Bibliotheken installieren
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    curl ca-certificates jq bzip2 tar sudo \
    libxcb1 libx11-6 file \
    git python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# 2. Dynamischer Download mit Validierung
RUN DOWNLOAD_URL=$(curl -s https://api.github.com/repos/block/goose/releases/latest | jq -r '.assets[] | select(.name | contains("aarch64-unknown-linux")) | .browser_download_url') && \
    curl -L "$DOWNLOAD_URL" | tar -xj -C /usr/local/bin --strip-components=1 && \
    # Check: Wenn es nicht 'ARM aarch64' ist, bricht der Build hier ab
    file /usr/local/bin/goose | grep -q "ARM aarch64" || (echo "Falsche Architektur geladen!" && exit 1)

# 3. Restliche Konfiguration
RUN chmod +x /usr/local/bin/goose && \
    useradd -m -s /bin/bash devuser && \
    echo "devuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER devuser
WORKDIR /home/devuser/smart-home

# 4. Finaler Test
RUN goose --version
