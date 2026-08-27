#!/usr/bin/env bash
# AMBAR — проверка ретранслятора голоса.
#
# Проверяет не «сервис запущен», а то, ради чего он нужен: пускает ли он своих
# и отбивает ли чужих. Разница не теоретическая — при неверных правах на файл
# конфига coturn молча поднимается на настройках по умолчанию и становится
# открытым ретранслятором для всего интернета. Снаружи это выглядит как
# «работает»: сервис активен, порт слушает, звонки идут.
#
#     sudo bash /opt/ambar/deploy/turn-check.sh
set -uo pipefail

CONF=/etc/turnserver.conf
IP=$(ip -4 addr show scope global | grep -oE 'inet [0-9.]+' | head -1 | cut -d' ' -f2)
FAIL=0
ok(){ echo "  [ок]    $1"; }
bad(){ echo "  [ПЛОХО] $1"; FAIL=1; }

echo "── служба ──"
[ "$(systemctl is-active coturn)" = active ] && ok "coturn запущен" || bad "coturn не запущен"

echo "── конфиг ──"
if [ -r "$CONF" ]; then
  # Главная ловушка: файл читает не root, а пользователь turnserver.
  if sudo -u turnserver test -r "$CONF"; then
    ok "демон может прочитать $CONF"
  else
    bad "демон НЕ читает $CONF — поднимется на настройках по умолчанию, то есть без пароля"
    echo "        чинится так: chown root:turnserver $CONF && chmod 640 $CONF"
  fi
  grep -q '^use-auth-secret' "$CONF" && ok "включена проверка пароля" || bad "нет use-auth-secret"
  grep -q '^denied-peer-ip' "$CONF" && ok "частные сети закрыты" || bad "нет denied-peer-ip"
else
  bad "нет $CONF"
fi

echo "── доступ ──"
CREDS=$(cd /opt/ambar && ./venv/bin/python -c "
import call_routes as C
s = [i for i in C.ice_servers() if 'username' in i]
print(s[0]['username'], s[0]['credential']) if s else print('', '')" 2>/dev/null)
set -- $CREDS
if [ -z "${1:-}" ]; then
  bad "приложение не отдаёт логин к ретранслятору — проверьте AMBAR_TURN_* в .env"
else
  ok "приложение выдаёт логин ($1)"
  RELAY=$(timeout 25 turnutils_uclient -v -y -n 1 -m 1 -u "$1" -w "$2" -p 3478 "$IP" 2>&1 \
          | grep -oE 'Received relay addr: [0-9.]+:[0-9]+' | head -1)
  [ -n "$RELAY" ] && ok "свой логин пускают — $RELAY" || bad "свой логин НЕ пускают, звонки через ретранслятор не пойдут"

  # Порт ретрансляции обязан попасть в заданный диапазон. Не попал — значит
  # конфиг не читается, даже если всё остальное выглядит нормально.
  MIN=$(grep -oE '^min-port=[0-9]+' "$CONF" 2>/dev/null | cut -d= -f2)
  MAX=$(grep -oE '^max-port=[0-9]+' "$CONF" 2>/dev/null | cut -d= -f2)
  PORT=$(echo "$RELAY" | grep -oE ':[0-9]+$' | tr -d ':')
  if [ -n "$PORT" ] && [ -n "$MIN" ]; then
    if [ "$PORT" -ge "$MIN" ] && [ "$PORT" -le "$MAX" ]; then
      ok "порт ретрансляции в заданном диапазоне ($MIN-$MAX)"
    else
      bad "порт $PORT вне диапазона $MIN-$MAX — конфиг НЕ применён"
    fi
  fi

  # Самое важное. Чужого пускать нельзя.
  BAD=$(timeout 25 turnutils_uclient -v -y -n 1 -m 1 -u "$1" -w "заведомо-неверный" -p 3478 "$IP" 2>&1 \
        | grep -oE 'Received relay addr' | head -1)
  [ -z "$BAD" ] && ok "чужой пароль отбивается" \
                || bad "ЧУЖОЙ ПАРОЛЬ ПУСКАЮТ — это открытый ретранслятор, выключите coturn немедленно"
  ANON=$(timeout 25 turnutils_uclient -v -y -n 1 -m 1 -p 3478 "$IP" 2>&1 \
         | grep -oE 'Received relay addr' | head -1)
  [ -z "$ANON" ] && ok "без пароля не пускают" \
                 || bad "БЕЗ ПАРОЛЯ ПУСКАЮТ — это открытый ретранслятор, выключите coturn немедленно"
fi

echo "── учёт ──"
grep -qE '^(verbose|syslog)' "$CONF" 2>/dev/null && ok "сессии пишутся в журнал" \
  || bad "сессии не пишутся — злоупотребление будет незаметно"

echo
[ "$FAIL" -eq 0 ] && echo "ИТОГ: ретранслятор в порядке" || echo "ИТОГ: есть проблемы, смотрите строки [ПЛОХО]"
exit "$FAIL"
