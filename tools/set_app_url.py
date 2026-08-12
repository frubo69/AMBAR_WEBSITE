#!/usr/bin/env python3
"""Прописать адрес мини-приложений в .env.

Адрес входа меняется (домен заблокировали, туннель поднялся с новым именем) —
меняться должно ровно в одном месте, а не в четырёх ботах руками."""
import re, sys, pathlib

ENV = pathlib.Path("/opt/ambar/.env")
base = sys.argv[1].rstrip("/")
vals = {
    "WEBAPP_URL":          base + "/",
    "OPERATOR_WEBAPP_URL": base + "/operator/",
    "DRIVER_WEBAPP_URL":   base + "/driver/",
    "OWNER_WEBAPP_URL":    base + "/owner/",
    # Пятый адрес, про который легко забыть: по нему Telegram ходит за
    # картинками рекламных карточек. Ходит он со своих серверов, не с телефона
    # клиента, но оставлять его на мёртвом домене — мина.
    "AMBAR_PUBLIC_ORIGIN": base,
}
s = ENV.read_text()
for k, v in vals.items():
    if re.search(rf"(?m)^{k}=", s):
        s = re.sub(rf"(?m)^{k}=.*$", f"{k}={v}", s)
    else:
        s = s.rstrip("\n") + f"\n{k}={v}\n"
ENV.write_text(s)
print("адрес приложений:", base)
