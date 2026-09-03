"""Закупочные цены — то, что мы платим магазину за учётную единицу.

Откуда
------
С двух листов прайса Barracuda (снимки от 3 сентября 2026): рукописный на
84 строки и печатный «BAKA» на 33. Цены здесь за ТУ ЖЕ единицу, в которой
приложение ведёт остаток: у крепкого это бутылка, у пива — ящик. Поэтому
Amstel стоит 78, а не 3.25: это ящик.

Чего здесь нет и почему
-----------------------
Пустая строка в прайсе — не ноль, а «цену не знаем». Такие позиции сюда не
внесены вовсе: в оценке склада они честно выпадают, и видно, какую долю
полок цена покрывает. Выдуманная себестоимость хуже отсутствующей — она
неотличима от настоящей и врёт молча.

Отдельно не внесены места, где на листе две цены и непонятно, какая живая:
зачёркнутое и переписанное сверху. Они перечислены в СПОРНЫЕ и ждут одного
слова от владельца.

Как поправить
-------------
Меняется цена — правится строка здесь. Файл читает `stock_value.py`; если
позиции нет в словаре, склад считается по остальным, а покрытие падает.
"""

# id каталога → цена закупки за учётную единицу, AED
COST = {
    # ── водка ────────────────────────────────────────────────────────────
    "p1":  35,     # Absolut 1 ltr
    "p2":  45,     # Stolichnaya 1 ltr
    "p3":  42,     # Russian Standard 1 ltr
    "p4":  48,     # Skyy Vodka 1 ltr
    "p5":  32,     # Smirnoff Vodka 1 ltr
    "p6":  105,    # Beluga 0.7
    "p8":  100,    # Belvedere 1 ltr
    "p9":  119,    # Ciroc 1 ltr
    # ── виски ────────────────────────────────────────────────────────────
    "p10": 42,     # Red Label 1 ltr
    "p11": 86,     # Black Label 1 ltr
    "p12": 75,     # Jack Daniels 1 ltr
    "p13": 81,     # Chivas Regal 12Y 1 ltr
    "p14": 68,     # Jameson 1 ltr
    "p15": 42,     # Ballantines Finest 1 ltr
    "p17": 252,    # Gold Label 1 ltr
    "p18": 256,    # Chivas Regal 18Y 1 ltr
    "p19": 116,    # Jack Daniels Honey 1 ltr
    "p20": 120,    # Gentleman Jack 1 ltr
    "p21": 850,    # Blue Label 1 ltr
    "p22": 629,    # Royal Salute 21Y 1 ltr
    "p23": 46,     # J&B 1 ltr
    "p25": 179,    # Glenfiddich 12Y 1 ltr
    # ── пиво: цена за ЯЩИК, как и остаток ────────────────────────────────
    "p35": 100,    # Stella Artois 0.33 can
    "p36": 129,    # Stella Artois 0.33 bottle
    "p37": 79,     # Red Horse 0.5 can
    "p38": 78,     # Amstel Light 0.33 can
    "p39": 215,    # Guinness 0.44 can
    "p42": 130,    # Hoegaarden 0.33 bottle
    "p43": 125,    # Corona Extra 0.355 bottle
    "p44": 115,    # Peroni 0.33 bottle
    "p45": 147,    # Smirnoff Ice 0.275 bottle
    "p46": 126,    # Bacardi Breezer 0.275 bottle
    "p47": 77,     # Carlsberg 0.5 can
    # ── ром, вермут ──────────────────────────────────────────────────────
    "p48": 39,     # Bacardi White 1 ltr
    "p49": 63,     # Bacardi Black 1 ltr
    "p50": 63,     # Bacardi Gold 1 ltr
    "p51": 39,     # Captain Morgan Black 1 ltr
    "p52": 39,     # Captain Morgan Gold 1 ltr
    "p53": 53,     # Malibu 1 ltr
    "p54": 31,     # Martini Bianco 1 ltr
    # ── джин ─────────────────────────────────────────────────────────────
    "p55": 42,     # Gordon's 1 ltr
    "p56": 63,     # Bombay Sapphire 1 ltr
    "p57": 147,    # Hendrick's 1 ltr
    "p60": 207,    # Monkey 47 0.5
    "p61": 136,    # Malfy Con Arancia 0.7
    "p62": 136,    # Malfy Rosa 0.7
    "p63": 151,    # Drumshanbo Gunpowder 0.7
    # ── текила ───────────────────────────────────────────────────────────
    "p67": 150,    # Patron Silver 0.75
    # ── коньяк ───────────────────────────────────────────────────────────
    "p74": 199,    # Hennessy VS 1 ltr
    "p75": 336,    # Hennessy VSOP 1 ltr
    # ── ликёры ───────────────────────────────────────────────────────────
    "p78": 79,     # Baileys 1 ltr
    "p80": 63,     # Jagermeister 1 ltr
    "p81": 73,     # Aperol 1 ltr
    "p82": 88,     # Tequila Rose 0.7
    # ── арак ─────────────────────────────────────────────────────────────
    "p83": 34,     # Arak Touma
    "p84": 78,     # Efe Raki 1 ltr
    # ── шампанское, просекко ─────────────────────────────────────────────
    "p85": 168,    # Moet Brut 0.75
    "p86": 209,    # Moet Rose 0.75
    "p87": 270,    # Moet Ice 0.75
    "p89": 439,    # Ruinart Blanc 0.75
    "p94": 45,     # Martini Asti 0.75
    # ── вино ─────────────────────────────────────────────────────────────
    "p97":  28,    # Pinot Grigio Cesari 0.75
    "p98":  35,    # Le Grand Noir Sauvignon Blanc 0.75
    "p99":  105,   # Rimapere Sauvignon Blanc 0.75
    "p100": 88,    # Calvet Sancerre 0.75
    "p101": 115,   # Louis Moreau Chablis 0.75
    "p102": 119,   # Bourgogne Louis Jadot 0.75
    "p103": 116,   # Gavi Di Gavi 0.75
    "p105": 24,    # Jacob Creek Shiraz 0.75
    "p106": 35,    # Le Grand Noir Merlot 0.75
    "p107": 103,   # Castel Barreyres 0.75
    "p108": 121,   # Chateau Perron 0.75
    "p111": 113,   # Chateau Des Laurets 0.75
    "p114": 386,   # Chateau Lagrange 0.75
    "p115": 24,    # Mateus Rose 0.75
    "p116": 66,    # Minuty 0.75
    "p117": 36,    # Chateau Ksara Rose 0.75
    "p118": 82,    # Whispering Angel 0.75
    "p119": 106,   # Saint Maur Rose 0.75
    "p120": 74,    # MiP Collection Rose 0.75
    "p121": 59,    # Drostdy Hof Premier Grand Cru 5 ltr
    "p122": 59,    # Drostdy Hof Claret Select 5 ltr
    "p123": 24,    # Jacob Creek Chardonnay 0.75
}

# На листе две цены, и какая живая — решает владелец. Пока не решено, позиции
# в расчёт не идут: угадывать себестоимость нельзя.
СПОРНЫЕ = {
    "p7":  "Grey Goose 1 ltr — на листе 94 и зачёркнутое рядом",
    "p16": "Double Black 1 ltr — 137 и переписанное сверху",
    "p31": "Heineken 0.33 can — 86 за ящик, но строка «Heineken Btl/Bud Btl» даёт 89 и 199",
    "p32": "Heineken 0.33 bottle — та же строка, две цены",
    "p33": "Budweiser 0.33 can — 79 на строке «Budweiser 35.5cl», банка или бутылка неясно",
    "p34": "Budweiser 0.33 bottle — та же неясность",
    "p64": "Jose Cuervo Silver — 65, а Gold 50: обычно наоборот, похоже на описку",
    "p65": "Jose Cuervo Gold — 50, см. выше",
    "p112": "La Celia Malbec — 39 на рукописном листе и 44 на печатном",
}

# Позиций каталога, которых в прайсе нет вовсе, — они выпадают из оценки:
# Macallan, Glenfiddich 15/18, Chivas 25Y, Tanqueray, Gordon Pink, Don Julio,
# Clase Azul, Hennessy XO, Remy Martin, Amarula, Veuve Clicquot, Dom Perignon,
# Bottega, Zonin, Oyster Bay, Campo Viejo, Saint Leon, XXL, Asahi, сигареты.


def cost_of(pid: str):
    """Цена закупки или None, если её не знаем."""
    v = COST.get(pid)
    return float(v) if v else None
