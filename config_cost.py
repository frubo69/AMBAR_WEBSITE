"""Закупочные цены — то, что мы платим магазину за учётную единицу.

Откуда
------
С листа прайса B1 на 122 строки (снимок от 5 сентября 2026), колонка
«Закупка». Цены здесь за ТУ ЖЕ единицу, в которой приложение ведёт остаток:
у крепкого это бутылка, у пива — ящик. Поэтому Heineken стоит 79.80, а не
3.30: это ящик.

Числа взяты как в листе, вплоть до копеек. Округлять их — значит на складе в
несколько тысяч бутылок потерять сотни дирхам на ровном месте.

Этот лист заменил прежние два: те были рукописные, местами с зачёркнутым, и
девять позиций в них читались двояко. Здесь все девять однозначны. Заодно
выяснилось, что Jose Cuervo Silver дороже Gold — это не описка, а так и есть,
и подтверждено уже вторым листом.

Чего здесь нет и почему
-----------------------
Пустая клетка в прайсе — не ноль, а «цену не знаем». Такие позиции сюда не
внесены вовсе: в оценке склада они честно выпадают, и видно, какую долю полок
цена покрывает. Выдуманная себестоимость хуже отсутствующей — она неотличима
от настоящей и врёт молча.

Без цены остались:
    Chivas Regal 25Y   — в листе строка есть, клетка пустая
    Patron XO Cafe     — в листе нули по всем трём колонкам, позиция снята
    табак (3 позиции)  — в этом листе его нет вовсе

Как поправить
-------------
Меняется цена — правится строка здесь. Файл читает `stock_value.py`; если
позиции нет в словаре, склад считается по остальным, а покрытие падает.
Поверх этого файла ложатся цены, введённые владельцем руками, — они сильнее.
"""

# id каталога → цена закупки за учётную единицу, AED
COST = {
    # ── Водка ─────────────────────────────────────────────────────────
    "p1":   34.65,   # Absolut 1 ltr
    "p2":   31.5,    # Stolichnaya 1 ltr
    "p3":   42,      # Russian Standard 1 ltr
    "p4":   35.7,    # Skyy Vodka 1 ltr
    "p5":   31.5,    # Smirnoff Vodka 1 ltr
    "p6":   126,     # Beluga 0.7 ltr
    "p7":   94.5,    # Grey Goose 1 ltr
    "p8":   99.75,   # Belvedere 1 ltr
    "p9":   140.7,   # Ciroc 1 ltr
    # ── Виски ─────────────────────────────────────────────────────────
    "p10":  42,      # Red Label 1 ltr
    "p11":  85.05,   # Black Label 1 ltr
    "p12":  74.55,   # Jack Daniels 1 ltr
    "p13":  80.85,   # Chivas Regal 12Y 1 ltr
    "p14":  67.2,    # Jameson 1 ltr
    "p16":  136.5,   # Double Black 1 ltr
    "p17":  186.9,   # Gold Label 1 ltr
    "p18":  255.15,  # Chivas Regal 18Y 1 ltr
    "p19":  120.75,  # Jack Daniels Honey 1 ltr
    "p20":  120,     # Gentleman Jack 1 ltr
    "p21":  990,     # Blue Label 1 ltr
    "p22":  630,     # Chivas Royal Salute 21Y 1 ltr
    "p23":  45.15,   # J&B 1 ltr
    "p25":  168,     # Glenfiddich 12Y 1 ltr
    "p26":  241.5,   # Glenfiddich 15Y 1 ltr
    "p27":  315,     # Glenfiddich 18Y 0.75 ltr
    "p28":  243.6,   # Macallan 12Y 0.7 ltr
    "p29":  567,     # Macallan 15Y 0.7 ltr
    "p30":  1134,    # Macallan 18Y 0.75 ltr
    # ── Пиво ──────────────────────────────────────────────────────────
    "p31":  79.8,    # Heineken 0.33 can
    "p32":  99.75,   # Heineken 0.33 bottle
    "p33":  78.75,   # Budweiser 0.33 can
    "p34":  99.75,   # Budweiser 0.33 bottle
    "p35":  100,     # Stella Artois 0.33 can
    "p36":  105,     # Stella Artois 0.33 bottle
    "p37":  76.65,   # Red Horse 0.5 can
    "p38":  73.5,    # Amstel Light 0.33 can
    "p47":  76.65,   # Carlsberg 0.5 can
    "p39":  215,     # Guinness 0.44 can
    "p40":  116.55,  # XXL Vodka 0.25 can
    "p41":  126,     # Asahi Super Dry 0.33 bottle
    "p42":  135,     # Hoegaarden 0.33 bottle
    "p43":  85.05,   # Corona Extra 0.355 bottle
    "p44":  110,     # Peroni Nastro Azzurro 0.33 bottle
    "p45":  147,     # Smirnoff Ice 0.275 bottle
    "p46":  125,     # Bacardi Breezer Melon 0.275 bottle
    # ── Ром ───────────────────────────────────────────────────────────
    "p48":  39.9,    # Bacardi White 1 ltr
    "p49":  63,      # Bacardi Black 1 ltr
    "p50":  63,      # Bacardi Gold 1 ltr
    "p51":  39.9,    # Captain Morgan Black 1 ltr
    "p52":  39.9,    # Captain Morgan Gold 1 ltr
    "p53":  53,      # Malibu 1 ltr
    # ── Вермут ────────────────────────────────────────────────────────
    "p54":  30.45,   # Martini Bianco 1 ltr
    # ── Джин ──────────────────────────────────────────────────────────
    "p55":  42,      # Gordon's 1 ltr
    "p56":  63,      # Bombay Sapphire 1 ltr
    "p57":  147,     # Hendrick's 1 ltr
    "p58":  58,      # Gordon Pink 0.7 ltr
    "p59":  100,     # Tanqueray 1 ltr
    "p60":  207,     # Monkey 47 0.5 ltr
    "p63":  151,     # Drumshanbo Gunpowder 0.7 ltr
    # ── Текила ────────────────────────────────────────────────────────
    "p64":  65,      # Jose Cuervo Silver 1 ltr
    "p65":  50,      # Jose Cuervo Gold 1 ltr
    "p67":  150,     # Patron Silver 0.75 ltr
    "p68":  252,     # Patron Gold 0.75 ltr
    "p69":  250,     # Don Julio Blanco 70/75cl
    "p70":  280.35,  # Don Julio Reposado 70/75cl
    "p71":  360,     # Don Julio Anejo 70/75cl
    "p72":  999,     # Don Julio 1942 70/75cl
    "p73":  1050,    # Clase Azul Reposado 70/75cl
    # ── Коньяк ────────────────────────────────────────────────────────
    "p74":  199.5,   # Hennessy VS 1 ltr
    "p75":  336,     # Hennessy VSOP 1 ltr
    "p76":  1340,    # Hennessy XO 1 ltr
    "p77":  265,     # Remy Martin VSOP 1 ltr
    # ── Ликёр ─────────────────────────────────────────────────────────
    "p78":  79.8,    # Baileys 1 ltr
    "p79":  73.5,    # Amarula 1 ltr
    "p80":  63,      # Jagermeister 1 ltr
    "p81":  84,      # Aperol 1 ltr
    # ── Арак ──────────────────────────────────────────────────────────
    "p83":  33.6,    # Arak Touma 0.75 ltr
    "p84":  78,      # Efe Raki 1 ltr
    # ── Шампанское ────────────────────────────────────────────────────
    "p85":  168,     # Moet Brut 0.75
    "p86":  210,     # Moet Rose 0.75
    "p87":  270,     # Moet Ice 0.75
    "p88":  235,     # Veuve Clicquot 0.75
    "p89":  439,     # Ruinart Blanc 0.75
    "p90":  999,     # Dom Perignon 0.75
    # ── Просекко ──────────────────────────────────────────────────────
    "p91":  36.75,   # Bottega Prosecco 0.75
    "p92":  58,      # Bottega Rose 0.75
    "p93":  125,     # Bottega Gold 0.75
    "p94":  44.1,    # Martini Asti 0.75
    "p95":  40,      # Zonin Prosecco 0.75
    # ── Вино ──────────────────────────────────────────────────────────
    "p96":  42,      # Jacob Creek Chardonnay Pinot Noir 0.75
    "p123": 23.1,    # Jacob Creek Chardonnay 0.75
    "p97":  27.3,    # Pinot Grigio Cesari 0.75
    "p98":  35,      # Le Grand Noir Sauvignon Blanc 0.75
    "p99":  105,     # Rimapere Sauvignon Blanc 0.75
    "p100": 88,      # Calvet Sancerre 0.75
    "p101": 115,     # Louis Moreau Chablis 0.75
    "p102": 119,     # Bourgogne Louis Jadot 0.75
    "p103": 116,     # Gavi Di Gavi 0.75
    "p104": 63,      # Oyster Bay Sauvignon Blanc 0.75
    "p105": 23.1,    # Jacob Creek Shiraz 0.75
    "p106": 35,      # Le Grand Noir Merlot 0.75
    "p107": 103,     # Castel Barreyres 0.75
    "p108": 121,     # Chateau Perron 0.75
    "p109": 48.3,    # Chateau Saint Leon 0.75
    "p110": 65,      # Campo Viejo Reserva 0.75
    "p112": 44,      # La Celia Malbec 0.75
    "p113": 125,     # Campo Viejo Gran Reserva 0.75
    "p114": 386,     # Chateau Lagrange 0.75
    "p115": 21,      # Mateus Rose 0.75
    "p116": 63,      # Minuty Cotes De Provence 0.75
    "p117": 45,      # Chateau Ksara Rose 0.75
    "p118": 99.75,   # Whispering Angel 0.75
    "p119": 106,     # Saint Maur Rose 0.75
    "p120": 74,      # MiP Collection Rose 0.75
    "p121": 59.85,   # Drostdy Hof Premier Grand Cru 5 ltr
    "p122": 59.85,   # Drostdy Hof Claret Select 5 ltr
}

# Спорных больше нет: лист B1 разрешил все девять, что оставались от рукописных
# прайсов. Если появится новое расхождение — место для него здесь.
СПОРНЫЕ = {}
