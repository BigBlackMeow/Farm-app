#!/usr/bin/env bash
# Instalace aplikace na server s Ubuntu (22.04 nebo 24.04).
# Spustí se jednou jako root:
#   curl -fsSL https://raw.githubusercontent.com/BigBlackMeow/Farm-app/main/deploy/instalace-serveru.sh | bash -s -- TOKEN_TUNELU
# TOKEN_TUNELU je z Cloudflare (Zero Trust > Networks > Tunnels). Bez tokenu se tunel nenastaví.
# Opakované spuštění aplikaci aktualizuje; data ve /opt/farm-app/data zůstanou.
set -euo pipefail

REPO="https://github.com/BigBlackMeow/Farm-app.git"
DIR="/opt/farm-app"
TOKEN="${1:-}"

[ "$(id -u)" -eq 0 ] || { echo "Spusťte jako root (sudo)."; exit 1; }

echo "== 1/5 Systémové balíčky"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv python3-pip sqlite3 curl

echo "== 2/5 Kód aplikace"
id farmapp >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin farmapp
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin main && git -C "$DIR" reset -q --hard origin/main
else
  git clone -q "$REPO" "$DIR"
fi
mkdir -p "$DIR/data" "$DIR/zalohy"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
chown -R farmapp:farmapp "$DIR"

echo "== 3/5 Registr přípravků (poprvé asi 4 minuty)"
if [ ! -f "$DIR/data/registry.sqlite" ]; then
  # Když služba MZe zrovna nejede, nevadí: registr se nahraje spolu se složkou data z vašeho počítače.
  (cd "$DIR" && sudo -u farmapp .venv/bin/python -m app.registry_import) \
    || echo "Registr se teď stáhnout nepodařilo. Nahrajte složku data z počítače (viz README)."
fi

echo "== 4/5 Služby (aplikace, měsíční registr, denní záloha)"
cp "$DIR/deploy/farm-app.service" "$DIR/deploy/farm-app-registr.service" "$DIR/deploy/farm-app-registr.timer" \
   "$DIR/deploy/farm-app-zaloha.service" "$DIR/deploy/farm-app-zaloha.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q --now farm-app.service farm-app-registr.timer farm-app-zaloha.timer
systemctl restart farm-app.service

echo "== 5/5 Cloudflare Tunnel"
if [ -n "$TOKEN" ]; then
  if ! command -v cloudflared >/dev/null; then
    curl -fsSL -o /tmp/cloudflared.deb \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$(dpkg --print-architecture).deb"
    dpkg -i /tmp/cloudflared.deb
  fi
  cloudflared service uninstall >/dev/null 2>&1 || true
  cloudflared service install "$TOKEN"
else
  echo "Token tunelu nezadán, tunel přeskočen (aplikace běží jen uvnitř serveru na portu 8000)."
fi

sleep 3
if curl -fsS -o /dev/null http://127.0.0.1:8000/; then
  echo "HOTOVO: aplikace běží."
else
  echo "Aplikace neodpovídá. Výpis chyb: journalctl -u farm-app -n 50"
  exit 1
fi
