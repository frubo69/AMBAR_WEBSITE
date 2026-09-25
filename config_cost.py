"""Закупочные цены — то, что мы платим магазину за учётную единицу.

Откуда
------
С 25 сентября 2026 — прайс магазина, действующий с этой даты: файл
`prices-clean.xlsx`, лист Sheet1, жёлтая колонка «Price, AED» (владелец:
«и кстати вот теперь все наши закупочные цены»). 122 позиции.

Единица — та же, в которой приложение ведёт остаток: у крепкого бутылка, у
пива ЯЩИК на 24. В прайсе пиво стоит за 24 штуки, то есть полящика (12) —
вдвое дешевле (владелец, 25 сен 2026). Поэтому Heineken здесь 95, а не 4: это
ящик.

Числа взяты как в файле, до копеек. Округлять их — значит на складе в
несколько тысяч бутылок потерять сотни дирхам на ровном месте.

Что изменилось против прежнего листа: 53 цены поправлены, 5 позиций впервые
получили закупку (Patron XO Cafe, Chivas Regal 25Y, Tequila Rose, Malfy
Arancia, Malfy Rosa) — раньше они считались без себестоимости. Позиций без
цены не осталось ни одной.

Главное подорожание — пиво: Corona 85.05 → 142, Budweiser 78.75 → 115,
Heineken 79.8 → 95, Carlsberg 76.65 → 100. Разбор этого прайса против их же
счетов — в отчёте по Баракуде от 25 сентября.

Чего в файле нет: `Louis Moreau Chablis 0.75` — оставлен с прежней ценой.
Зато в файле есть `Laroche Chablis St. Martin`, которого нет в нашем
каталоге: это другая позиция, наугад её к Moreau не привязываем.

Как поправить
-------------
Меняется цена — правится строка здесь. Файл читает `stock_value.py`; если
позиции нет в словаре, склад считается по остальным, а покрытие падает.
Поверх этого файла ложатся цены, введённые владельцем руками, — они сильнее.
"""

# id каталога → цена закупки за учётную единицу, AED
COST = {
    # ── Водка ─────────────────────────────────────────────────────────
    "p1":     35,       # Absolut 1 ltr
    "p2":     45,       # Stolichnaya 1 ltr
    "p3":     42,       # Russian Standard 1 ltr
    "p4":     48,       # Skyy Vodka 1 ltr
    "p5":     32,       # Smirnoff Vodka 1 ltr
    "p6":     105,      # Beluga 0.7 ltr — по их счёту; в листе было 126
    "p7":     94,       # Grey Goose 1 ltr — по их счёту; в листе было 94.5
    "p8":     100,      # Belvedere 1 ltr
    "p9":     141,      # Ciroc 1 ltr
    # ── Виски ─────────────────────────────────────────────────────────
    "p10":    42,       # Red Label 1 ltr
    "p11":    86,       # Black Label 1 ltr
    "p12":    75,       # Jack Daniels 1 ltr
    "p13":    81,       # Chivas Regal 12Y 1 ltr
    "p14":    68,       # Jameson 1 ltr
    "p15":    42,       # Ballantines Finest 1 ltr — по их счёту; в листе не было (стояла рукой в STAR)
    "p16":    137,      # Double Black 1 ltr
    "p17":    227,      # Gold Label 1 ltr
    "p18":    256,      # Chivas Regal 18Y 1 ltr
    "p19":    116,      # Jack Daniels Honey 1 ltr — по их счёту; в листе было 120.75
    "p20":    120,      # Gentleman Jack 1 ltr
    "p21":    850,      # Blue Label 1 ltr — по их счёту; в листе было 990
    "p22":    629,      # Chivas Royal Salute 21Y 1 ltr
    "p23":    46,       # J&B 1 ltr
    "p25":    179,      # Glenfiddich 12Y 1 ltr
    "p26":    242,      # Glenfiddich 15Y 1 ltr
    "p27":    315,      # Glenfiddich 18Y 0.75 ltr
    "p28":    290,      # Macallan 12Y 0.7 ltr
    "p29":    625,      # Macallan 15Y 0.7 ltr
    "p30":    1134,     # Macallan 18Y 0.75 ltr
    # ── Пиво ──────────────────────────────────────────────────────────
    "p31":    95,       # Heineken 0.33 can
    "p32":    105,      # Heineken 0.33 bottle — по их счёту; в листе было 99.75
    "p33":    115,      # Budweiser 0.33 can
    "p34":    120,      # Budweiser 0.33 bottle
    "p35":    100,      # Stella Artois 0.33 can
    "p36":    135,      # Stella Artois 0.33 bottle
    "p37":    84,       # Red Horse 0.5 can
    "p38":    78,       # Amstel Light 0.33 can
    "p47":    100,      # Carlsberg 0.5 can
    "p39":    209,      # Guinness 0.44 can — по их счёту; в листе было 215
    "p40":    117,      # XXL Vodka 0.25 can
    "p41":    159,      # Asahi Super Dry 0.33 bottle
    "p42":    140,      # Hoegaarden 0.33 bottle
    "p43":    142,      # Corona Extra 0.355 bottle
    "p44":    135,      # Peroni Nastro Azzurro 0.33 bottle
    "p45":    147,      # Smirnoff Ice 0.275 bottle
    "p46":    139,      # Bacardi Breezer Melon 0.275 bottle
    # ── Ром ───────────────────────────────────────────────────────────
    "p48":    39,       # Bacardi White 1 ltr — по их счёту; в листе было 39.9
    "p49":    63,       # Bacardi Black 1 ltr
    "p50":    63,       # Bacardi Gold 1 ltr
    "p51":    39,       # Captain Morgan Black 1 ltr
    "p52":    39,       # Captain Morgan Gold 1 ltr — по их счёту; в листе было 39.9
    "p53":    53,       # Malibu 1 ltr
    # ── Вермут ────────────────────────────────────────────────────────
    "p54":    41,       # Martini Bianco 1 ltr
    # ── Джин ──────────────────────────────────────────────────────────
    "p55":    42,       # Gordon's 1 ltr
    "p56":    63,       # Bombay Sapphire 1 ltr
    "p57":    147,      # Hendrick's 1 ltr
    "p58":    74,       # Gordon Pink 0.7 ltr
    "p59":    100,      # Tanqueray 1 ltr
    "p60":    207,      # Monkey 47 0.5 ltr
    "p63":    151,      # Drumshanbo Gunpowder 0.7 ltr
    # ── Текила ────────────────────────────────────────────────────────
    "p64":    58,       # Jose Cuervo Silver 1 ltr — по их счёту; в листе было 65
    "p65":    50,       # Jose Cuervo Gold 1 ltr
    "p67":    144,      # Patron Silver 0.75 ltr — по их счёту; в листе было 150
    "p68":    165,      # Patron Gold 0.75 ltr — по их счёту; в листе было 252
    "p69":    250,      # Don Julio Blanco 70/75cl
    "p70":    250,      # Don Julio Reposado 70/75cl
    "p71":    360,      # Don Julio Anejo 70/75cl
    "p72":    649,      # Don Julio 1942 70/75cl
    "p73":    1134,     # Clase Azul Reposado 70/75cl
    # ── Коньяк ────────────────────────────────────────────────────────
    "p74":    199,      # Hennessy VS 1 ltr — по их счёту; в листе было 199.5
    "p75":    336,      # Hennessy VSOP 1 ltr
    "p76":    1340,     # Hennessy XO 1 ltr
    "p77":    265,      # Remy Martin VSOP 1 ltr
    # ── Ликёр ─────────────────────────────────────────────────────────
    "p78":    79,       # Baileys 1 ltr — по их счёту; в листе было 79.8
    "p79":    74,       # Amarula 1 ltr
    "p80":    69,       # Jagermeister 1 ltr
    "p81":    73,       # Aperol 1 ltr — по их счёту; в листе было 84
    # ── Арак ──────────────────────────────────────────────────────────
    "p83":    34,       # Arak Touma 0.75 ltr
    "p84":    78,       # Efe Raki 1 ltr
    # ── Шампанское ────────────────────────────────────────────────────
    "p85":    168,      # Moet Brut 0.75
    "p86":    209,      # Moet Rose 0.75 — по их счёту; в листе было 210
    "p87":    270,      # Moet Ice 0.75
    "p88":    220,      # Veuve Clicquot 0.75 — по их счёту; в листе было 235
    "p89":    439,      # Ruinart Blanc 0.75
    "p90":    1050,     # Dom Perignon 0.75
    # ── Просекко ──────────────────────────────────────────────────────
    "p91":    37,       # Bottega Prosecco 0.75
    "p92":    58,       # Bottega Rose 0.75
    "p93":    122,      # Bottega Gold 0.75 — по их счёту; в листе было 125
    "p94":    45,       # Martini Asti 0.75
    "p95":    37,       # Zonin Prosecco 0.75 — по их счёту; в листе было 40
    # ── Вино ──────────────────────────────────────────────────────────
    "p96":    51,       # Jacob Creek Chardonnay Pinot Noir 0.75
    "p123":   30,       # Jacob Creek Chardonnay 0.75
    "p97":    28,       # Pinot Grigio Cesari 0.75
    "p98":    35,       # Le Grand Noir Sauvignon Blanc 0.75
    "p99":    105,      # Rimapere Sauvignon Blanc 0.75
    "p100":   88,       # Calvet Sancerre 0.75
    "p101": 115,     # Louis Moreau Chablis 0.75
    "p102":   119,      # Bourgogne Louis Jadot 0.75
    "p103":   116,      # Gavi Di Gavi 0.75
    "p104":   54,       # Oyster Bay Sauvignon Blanc 0.75 — по их счёту; в листе было 63
    "p105":   30,       # Jacob Creek Shiraz 0.75
    "p106":   35,       # Le Grand Noir Merlot 0.75
    "p107":   103,      # Castel Barreyres 0.75
    "p108":   121,      # Chateau Perron 0.75
    "p109":   44,       # Chateau Saint Leon 0.75 — по их счёту; в листе было 48.3
    "p110":   72,       # Campo Viejo Reserva 0.75
    "p111":   113,      # Chateau Des Laurets 0.75 — по их счёту; в листе не было
    "p112":   44,       # La Celia Malbec 0.75
    "p113":   105,      # Campo Viejo Gran Reserva 0.75 — по их счёту; в листе было 125
    "p114":   386,      # Chateau Lagrange 0.75
    "p115":   23,       # Mateus Rose 0.75
    "p116":   63,       # Minuty Cotes De Provence 0.75
    "p117":   36,       # Chateau Ksara Rose 0.75 — по их счёту; в листе было 45
    "p118":   82,       # Whispering Angel 0.75 — по их счёту; в листе было 99.75
    "p119":   106,      # Saint Maur Rose 0.75
    "p120":   74,       # MiP Collection Rose 0.75
    "p121":   59,       # Drostdy Hof Premier Grand Cru 5 ltr — по их счёту; в листе было 59.85
    "p122":   59,       # Drostdy Hof Claret Select 5 ltr
    # ── Появились в прайсе 25 сентября 2026 ───────────────────────────
    "p66":    119,      # Patron XO Cafe 0.75 ltr
    "p24":    975,      # Chivas Regal 25Y 0.7 ltr
    "p82":    90,       # Tequila Rose Strawberry Cream 0.7 ltr
    "p61":    136,      # Malfy Con Arancia 0.7 ltr
    "p62":    136,      # Malfy Rosa 0.7 ltr
}

# Спорных больше нет: лист B1 разрешил все девять, что оставались от рукописных
# прайсов. Если появится новое расхождение — место для него здесь.
СПОРНЫЕ = {}
