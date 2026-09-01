#!/usr/bin/env bash
# Прогон проверки безопасности по копии репозитория.
#
# Strix — не линтер, а агент: он умеет ходить по сети и пробовать цель на зуб.
# Поэтому здесь у него нет цели, кроме папки с кодом, и папка эта — копия.
# Из копии выпадает всё, чего нет под гитом, то есть и .env со всеми ключами:
# его не «исключают фильтром», он просто не попадает внутрь.
#
#   bash tools/security-scan/run.sh            весь код
#   bash tools/security-scan/run.sh --quick    быстрый проход, дешевле
#
# Нужны: docker (запущенный) и ключ модели в окружении:
#   export STRIX_LLM="anthropic/claude-sonnet-4-6"
#   export LLM_API_KEY="…"
set -euo pipefail

КОРЕНЬ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
МЕТКА="ambar-$(date +%Y%m%d-%H%M)"
КОПИЯ="${TMPDIR:-/tmp}/$МЕТКА"

if ! command -v strix >/dev/null 2>&1 && [ ! -x "$HOME/.strix/bin/strix" ]; then
  echo "strix не установлен: curl -sSL https://strix.ai/install | bash" >&2
  exit 1
fi
STRIX="$(command -v strix || echo "$HOME/.strix/bin/strix")"

if ! docker info >/dev/null 2>&1; then
  echo "Docker не запущен — Strix держит агента в контейнере и без него не пойдёт." >&2
  exit 1
fi
if [ -z "${LLM_API_KEY:-}" ]; then
  echo "Нет LLM_API_KEY — прогон платный, ключ задаёт тот, кто платит." >&2
  exit 1
fi

echo "Готовлю копию: $КОПИЯ"
mkdir -p "$КОПИЯ"
# Только то, что под гитом. Картинки, шрифты и вендор тоже уедут, но они
# ничего не стоят агенту: он их не читает, а искать секреты в них незачем.
(cd "$КОРЕНЬ" && git ls-files -z | rsync -a --files-from=- --from0 ./ "$КОПИЯ/")

# Пояс поверх подтяжек: если .env однажды перестанет быть в .gitignore, эта
# строка всё равно не даст ключам уехать в чужую модель.
find "$КОПИЯ" -name '.env*' -delete
if [ -e "$КОПИЯ/.env" ]; then echo "В копии остался .env — останавливаюсь." >&2; exit 1; fi

echo "Файлов в копии: $(find "$КОПИЯ" -type f | wc -l | tr -d ' ')"
echo

РЕЖИМ="standard"
[ "${1:-}" = "--quick" ] && РЕЖИМ="quick"

cd "$КОРЕНЬ"
"$STRIX" \
  --target "$КОПИЯ" \
  --instruction-file "$КОРЕНЬ/tools/security-scan/scope.md" \
  --scan-mode "$РЕЖИМ" \
  --non-interactive

echo
echo "Отчёт: $КОРЕНЬ/strix_runs/ — открыть: $STRIX view"
echo "Копию можно удалить: rm -rf \"$КОПИЯ\""
