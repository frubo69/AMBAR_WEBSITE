#!/usr/bin/env bash
# AMBAR — проверка связи тем же путём, которым идёт телефон.
#
# Дважды подряд звонки «работали» на сервере и не работали на устройстве:
# сначала nginx не пропускал вебсокет, потом блок апгрейда оказался прописан
# не тому домену — приложения раздаются не с ambar-delivery.com, а с адреса
# из DRIVER_WEBAPP_URL. Оба раза причина одна: проверка ходила напрямую в
# 127.0.0.1:8080, мимо nginx и мимо настоящего адреса.
#
# Поэтому здесь адреса берутся из .env — тех самых переменных, по которым
# телеграм открывает приложения, — и проверяется полный путь: TLS, nginx,
# апгрейд, подпись.
#
#     bash /opt/ambar/deploy/call-check.sh
set -uo pipefail
cd /opt/ambar || exit 1
FAIL=0
ok(){ echo "  [ок]    $1"; }
bad(){ echo "  [ПЛОХО] $1"; FAIL=1; }

# shellcheck disable=SC2046
set -a; . ./.env 2>/dev/null; set +a

for VAR in DRIVER_WEBAPP_URL OPERATOR_WEBAPP_URL OWNER_WEBAPP_URL; do
  URL="${!VAR:-}"
  [ -n "$URL" ] || { echo "── $VAR не задан, пропускаю"; continue; }
  HOST=$(echo "$URL" | sed -E 's#^https?://([^/]+).*#\1#')
  echo "── $VAR → $HOST ──"

  CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    --resolve "$HOST:443:127.0.0.1" "https://$HOST/api/call/ws" 2>/dev/null)
  case "$CODE" in
    101) ok "вебсокет проходит через nginx" ;;
    400) bad "nginx не пропускает апгрейд — добавьте location /api/call/ws в его server-блок"
         echo "        готовый блок: deploy/nginx-websocket.conf" ;;
    *)   bad "неожиданный ответ $CODE" ;;
  esac
done

echo "── ретранслятор ──"
ICE=$(curl -s http://127.0.0.1:8080/api/call/ice)
echo "$ICE" | grep -q '"username"' && ok "логин к ретранслятору выдаётся" \
  || bad "ретранслятора нет — проверьте AMBAR_TURN_* в .env и deploy/turn-check.sh"

echo
[ "$FAIL" -eq 0 ] && echo "ИТОГ: связь доступна тем же путём, что и с телефона" \
                  || echo "ИТОГ: есть проблемы, смотрите строки [ПЛОХО]"
exit "$FAIL"
