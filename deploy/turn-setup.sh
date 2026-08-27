#!/usr/bin/env bash
# AMBAR — ретранслятор голоса (coturn).
#
# Зачем. Звонок между приложением водителя и панелью оператора идёт напрямую,
# и в большинстве сетей этого достаточно. Но у сотовых операторов в Эмиратах
# адреса раздаются так, что прямое соединение складывается не всегда: телефон
# и панель друг друга просто не находят. Тогда голос идёт через ретранслятор
# на нашем же сервере. Он не нужен для каждого звонка — он нужен для того
# звонка, который иначе молчал бы.
#
# Что делает скрипт: ставит coturn, кладёт конфиг, генерирует общий ключ,
# прописывает его в .env и перезапускает API. Запускать один раз:
#
#     sudo bash /opt/ambar/deploy/turn-setup.sh
#
# Ключ нигде не печатается и в репозиторий не попадает: он лежит только в
# двух файлах на сервере — конфиге coturn и .env.
set -euo pipefail

ENV_FILE=/opt/ambar/.env
CONF=/etc/turnserver.conf
PORT=3478
MIN_PORT=49500
MAX_PORT=49600

[ "$(id -u)" -eq 0 ] || { echo "нужен root: sudo bash $0"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "не вижу $ENV_FILE — это точно наш сервер?"; exit 1; }

IP=$(ip -4 addr show scope global | grep -oE 'inet [0-9.]+' | head -1 | cut -d' ' -f2)
[ -n "$IP" ] || { echo "не смог определить внешний адрес"; exit 1; }
echo "внешний адрес: $IP"

echo "── ставим coturn ──"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y coturn >/dev/null

# Ключ переиспользуем, если уже был: смена ключа рвёт живые звонки.
if grep -q '^AMBAR_TURN_SECRET=' "$ENV_FILE"; then
  SECRET=$(grep '^AMBAR_TURN_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2-)
  echo "ключ уже есть — оставляем прежний"
else
  SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)
  echo "сгенерирован новый ключ"
fi

echo "── конфиг ──"
cat > "$CONF" <<EOF
# AMBAR. Ставится скриптом deploy/turn-setup.sh — правки здесь переживут
# перезапуск, но не повторный запуск скрипта.
listening-port=$PORT
fingerprint
use-auth-secret
static-auth-secret=$SECRET
realm=ambar
external-ip=$IP

# Узкий диапазон для ретрансляции. Сотня портов — это сотня одновременных
# разговоров; открывать весь верхний диапазон ради пяти водителей незачем.
min-port=$MIN_PORT
max-port=$MAX_PORT

# Ретранслятор не должен ходить внутрь машины и в частные сети: иначе он
# становится дверью в локальную сеть для любого, у кого есть логин.
denied-peer-ip=0.0.0.0-0.255.255.255
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=127.0.0.0-127.255.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=::1
denied-peer-ip=fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff

# Логин живёт десять минут и выдаётся сервером на каждый звонок, поэтому
# подобрать его неоткуда. Квоты — на случай, если всё же утёк.
user-quota=12
total-quota=100

no-cli
no-tlsv1
no-tlsv1_1
syslog
verbose
EOF
# Файл читает не root, а пользователь turnserver. При 600 с владельцем root
# демон молча не прочитает конфиг и поднимется на настройках по умолчанию —
# то есть станет открытым ретранслятором для всего интернета, оставаясь при
# этом «активным» и слушающим порт. Отсюда 640 и группа turnserver.
chown root:turnserver "$CONF"
chmod 640 "$CONF"

# В убунте пакет по умолчанию выключен отдельным файлом.
if [ -f /etc/default/coturn ]; then
  sed -i 's/^#*TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn
  grep -q '^TURNSERVER_ENABLED=' /etc/default/coturn || echo 'TURNSERVER_ENABLED=1' >> /etc/default/coturn
fi

echo "── настройки приложения ──"
sed -i '/^AMBAR_TURN_HOST=/d; /^AMBAR_TURN_SECRET=/d' "$ENV_FILE"
{
  echo ""
  echo "# Ретранслятор голоса (deploy/turn-setup.sh)"
  echo "AMBAR_TURN_HOST=$IP:$PORT"
  echo "AMBAR_TURN_SECRET=$SECRET"
} >> "$ENV_FILE"

echo "── запуск ──"
systemctl enable coturn >/dev/null 2>&1 || true
systemctl restart coturn
systemctl restart ambar-api
sleep 3

echo
echo "coturn:    $(systemctl is-active coturn)"
echo "ambar-api: $(systemctl is-active ambar-api)"
echo "слушает:   $(ss -lnu 2>/dev/null | grep -c ":$PORT") сокетов на $PORT/udp"
echo
echo "что отдаётся приложениям:"
curl -s http://127.0.0.1:8080/api/call/ice | sed 's/"credential":"[^"]*"/"credential":"…"/'
echo
echo "Если провайдер держит внешний файрвол — открыть $PORT/udp, $PORT/tcp"
echo "и $MIN_PORT-$MAX_PORT/udp."
echo
# «Сервис активен» ничего не доказывает: при неверных правах на конфиг coturn
# поднимается открытым ретранслятором и выглядит точно так же. Поэтому в конце
# установки — не отчёт, а проверка.
echo "════ проверка ════"
bash "$(dirname "$0")/turn-check.sh"
