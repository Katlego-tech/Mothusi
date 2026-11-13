#!/bin/bash
# MothusiModelHost first-boot bootstrap (deployment-aws.md §6.2). Idempotent: safe to run again.
# Installs Ollama from its release archive with the checksum checked, runs it as a systemd
# service, pulls the three models, and writes /opt/mothusi/model-ready.sh for ops.sh to poll.
set -euo pipefail
# cloud-init runs this without HOME, and the Ollama CLI panics without it ("$HOME is not
# defined"); the server keeps its own models under /usr/share/ollama either way.
export HOME="${HOME:-/root}"

OLLAMA_VERSION="0.40.15"
# sha256 of ollama-linux-amd64.tar.zst, from the release's own sha256sum.txt (checked 2026-11-11).
OLLAMA_SHA256="628c018404a4206493efd38e46251c154ede5141b8b8dc85c659b3b027c47379"
MODELS=("qwen3-vl:8b-instruct" "qwen3:14b" "phi4")

log() { echo "mothusi-model-host: $*"; }

if ! /usr/local/bin/ollama --version 2>/dev/null | grep -q "$OLLAMA_VERSION"; then
  command -v zstd >/dev/null || { apt-get update -q && apt-get install -y -q zstd; }
  work="$(mktemp -d)"
  log "downloading Ollama $OLLAMA_VERSION"
  curl -fsSL -o "$work/ollama.tar.zst" \
    "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst"
  echo "${OLLAMA_SHA256}  $work/ollama.tar.zst" | sha256sum -c -
  tar --zstd -xf "$work/ollama.tar.zst" -C /usr/local
  rm -rf "$work"
fi

id ollama >/dev/null 2>&1 || useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama

cat > /etc/systemd/system/ollama.service <<'UNIT'
[Unit]
Description=Ollama (Mothusi model host)
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
# qwen3:14b (9.3 GB) read at ~50 MB/s from gp3 after a stop/start: past the 5-minute default.
Environment="OLLAMA_LOAD_TIMEOUT=15m"
# Four requests decode together: the Planner marks in batches of 4 (marking-pipeline.md).
Environment="OLLAMA_NUM_PARALLEL=4"

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now ollama

for _ in $(seq 1 60); do
  curl -fsS localhost:11434/api/version >/dev/null 2>&1 && break
  sleep 2
done

for model in "${MODELS[@]}"; do
  log "pulling $model"
  /usr/local/bin/ollama pull "$model"
done

mkdir -p /opt/mothusi
cat > /opt/mothusi/model-ready.sh <<'READY'
#!/bin/bash
# Exits 0 only when the T4 is visible and Ollama lists all three models (deployment-aws.md §6.2).
# With --digests it also prints each model's digest, for deployment-aws.md §6.7.
set -euo pipefail
nvidia-smi --query-gpu=name --format=csv,noheader | grep -q "T4"
tags="$(curl -fsS localhost:11434/api/tags)"
python3 - "$tags" "${1:-}" <<'PY'
import json, sys
models = {m["name"]: m.get("digest", "") for m in json.loads(sys.argv[1])["models"]}
want = ["qwen3-vl:8b-instruct", "qwen3:14b", "phi4:latest"]
missing = [w for w in want if w not in models]
if missing:
    sys.exit("missing: " + ", ".join(missing))
if sys.argv[2] == "--digests":
    for w in want:
        print(w, models[w])
PY
READY
chmod 0755 /opt/mothusi/model-ready.sh
log "bootstrap done"
