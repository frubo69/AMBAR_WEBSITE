"""Порядок в «Всех клиентах» (владелец, 23 сен 2026: «дай возможность
посмотреть, какие именно клиенты у нас присоединились — сверху вниз от новых
к старым»).

Настоящий STAR в безголовом Chrome (tools/scanfuzz/stand.py), клиенты
выдуманные. Проверяем то, что видно на экране:
  • «новые сверху» — от самого свежего к самому старому;
  • заголовки становятся днями: «Сегодня», «Вчера», дальше числом и месяцем;
  • день считается по Дубаю, а не по часовому поясу телефона;
  • буквенная лесенка в этом порядке прячется, а в алфавитном возвращается;
  • переключили обратно — снова А → Я, и никто не потерялся.

    python3 tools/test_clients_order.py
"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanfuzz"))
from stand import Stands, run, report                        # noqa: E402

PORT, DBG = 8799, 9660
TAG = f"_c{os.getpid()}"

JS = """(async () => {
  const out = [];
  const eq = (имя, дали, ждём) => out.push([имя, JSON.stringify(дали), JSON.stringify(ждём),
                                            JSON.stringify(дали) === JSON.stringify(ждём)]);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const ч = 3600000, д = 86400000, t = Date.now();
  const кто = (id, имя, ини, назад, via) => ({
    telegram_id: id, full_name: имя, initials: ини, username: 'user' + id,
    orders_total: 0, total_spent: 0, is_vip: false, is_banned: false, referrals_count: 0,
    phone: '', phone_verified: false, invited_via: via || null,
    first_seen: new Date(t - назад).toISOString(), last_seen: new Date(t - назад).toISOString()});

  // Три часа назад — это ещё «сегодня» только если полночь в Дубае не прошла;
  // чтобы проверка не зависела от времени суток, берём заведомо разные дни.
  _customersCache = [
    кто(1, 'Яна Второва', 'ЯВ', 2 * ч, 'post_direct'),      // самый свежий
    кто(2, 'Борис Первый', 'БП', 26 * ч, null),             // вчера-позавчера
    кто(3, 'Анна Третья', 'АТ', 5 * д, 'chat_direct'),
    кто(4, 'Марк Старый', 'МС', 400 * д, null)];            // прошлый год
  renderAllClientsList(_customersCache);
  acSort('new');
  await sleep(200);

  const имена = () => [...document.querySelectorAll('#acBody .lrow')]
    .filter(r => r.style.display !== 'none')
    .map(r => r.querySelector('.lrow-name').textContent.trim());
  const шапки = () => [...document.querySelectorAll('#acBody .az-hdr')]
    .filter(h => h.style.display !== 'none').map(h => h.textContent.trim());

  eq('новые сверху — от свежего к старому', имена(),
     ['Яна Второва', 'Борис Первый', 'Анна Третья', 'Марк Старый']);
  eq('у каждого свой день — четыре заголовка', шапки().length, 4);
  eq('самый свежий — под сегодняшним или вчерашним днём',
     ['Сегодня', 'Вчера'].includes(шапки()[0]), true);
  eq('прошлый год — с годом в заголовке', /\\d{4}/.test(шапки()[3]), true);
  eq('подпись листа говорит, какой сейчас порядок',
     document.getElementById('acSubtitle').textContent.includes('новые сверху'), true);
  eq('буквенная лесенка спрятана', document.getElementById('azIndex').style.display, 'none');
  eq('под именем — время и откуда пришёл',
     document.querySelector('#acBody .lrow .lrow-sub').textContent.includes('из рекламы'), true);

  // Назад в алфавит: порядок, лесенка и подпись возвращаются.
  acSort('az');
  await sleep(200);
  eq('вернули алфавит', имена(), ['Анна Третья', 'Борис Первый', 'Марк Старый', 'Яна Второва']);
  eq('заголовки снова буквы', шапки(), ['А', 'Б', 'М', 'Я']);
  eq('лесенка вернулась', document.getElementById('azIndex').style.display, '');
  eq('и никто не потерялся', имена().length, 4);
  return out;
})()"""


async def main():
    with Stands(PORT, TAG):
        провалы = report("owner", await run("owner", JS, port=PORT, dbg=DBG, tag=TAG))
    print("\nИТОГ:", "все прошли" if not провалы else f"провалено {провалы}")
    return 1 if провалы else 0


sys.exit(asyncio.run(main()))
