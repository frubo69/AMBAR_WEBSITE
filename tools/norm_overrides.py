"""Ручные правки владельца поверх расчёта (18 сен 2026).

Владелец с операторами прошёл норму, посчитанную tools/norm_rule.py, и по 393
клеткам поставил свои числа: «вместо твоей новой нормы отредактировали и где-то
вписали свои цифры». Эти числа сильнее расчёта и переживают пересчёт — иначе
через две недели правило затёрло бы решение, принятое глазами и опытом.

Что они поменяли:
  • вернули товар в 168 клеток, где за 48 дней не было ни одной продажи
    (89 530 AED), из них дорогое от 600 AED — 43 клетки на 53 788: Clase Azul,
    Hennessy XO, Macallan 18, Don Julio 1942, Dom Perignon, Blue Label;
  • срезали 19 ходовых клеток обратно к цифре листа операторов — Absolut в
    Бизнес Бей 60 → 40, Red Label 60 → 40, Corona 22,5 → 15.

Второе означает, что запас по этим позициям стал 5,6–7 дней, и норма держится
только на трёх заездах в неделю. При одном заезде Бизнес Бей встанет по ходовым.

Итог: 2818 единиц на 298 735 AED против 2296 / 196 882 у чистого расчёта.

Правка живёт, пока её не убрали отсюда. Если по клетке решено вернуться к
расчёту — строку надо удалить, а не выставлять в ней «как считает правило».
"""

# район → {позиция: норма}. Ноль — «в этом районе не держим».
OVERRIDE = {
    "jvc": {     # B1, правок 63
        "p123": 10,       # расчёт давал 1 · Jacob Creek Chardonnay 0.75
        "p106": 12,       # расчёт давал 3 · Le Grand Noir Merlot 0.75
        "p117": 10,       # расчёт давал 2 · Chateau Ksara Rose 0.75
        "p115": 10,       # расчёт давал 6 · Mateus Rose 0.75
        "p49": 3,         # расчёт давал 1 · Bacardi Black 1 ltr
        "p64": 6,         # расчёт давал 9 · Jose Cuervo Silver 1 ltr
        "p56": 6,         # расчёт давал 7 · Bombay Sapphire 1 ltr
        "p51": 5,         # расчёт давал 0 · Captain Morgan Black 1 ltr
        "p52": 5,         # расчёт давал 3 · Captain Morgan Gold 1 ltr
        "p23": 4,         # расчёт давал 1 · J&B 1 ltr
        "p2": 5,          # расчёт давал 1 · Stolichnaya 1 ltr
        "p3": 5,          # расчёт давал 2 · Russian Standard 1 ltr
        "p8": 9,          # расчёт давал 3 · Belvedere 1 ltr
        "p4": 3,          # расчёт давал 1 · Skyy Vodka 1 ltr
        "p16": 3,         # расчёт давал 2 · Double Black 1 ltr
        "p74": 3,         # расчёт давал 2 · Hennessy VS 1 ltr
        "p75": 3,         # расчёт давал 2 · Hennessy VSOP 1 ltr
        "p76": 2,         # расчёт давал 0 · Hennessy XO 1 ltr
        "p77": 2,         # расчёт давал 0 · Remy Martin VSOP 1 ltr
        "p22": 2,         # расчёт давал 0 · Chivas Royal Salute 21Y 1 ltr
        "p68": 3,         # расчёт давал 0 · Patron Gold 0.75 ltr
        "p69": 3,         # расчёт давал 2 · Don Julio Blanco 70/75cl
        "p71": 3,         # расчёт давал 0 · Don Julio Anejo 70/75cl
        "p72": 2,         # расчёт давал 0 · Don Julio 1942 70/75cl
        "p94": 5,         # расчёт давал 3 · Martini Asti 0.75
        "p91": 8,         # расчёт давал 5 · Bottega Prosecco 0.75
        "p92": 4,         # расчёт давал 2 · Bottega Rose 0.75
        "p93": 4,         # расчёт давал 2 · Bottega Gold 0.75
        "p88": 4,         # расчёт давал 2 · Veuve Clicquot 0.75
        "p85": 7,         # расчёт давал 3 · Moet Brut 0.75
        "p86": 5,         # расчёт давал 0 · Moet Rose 0.75
        "p87": 4,         # расчёт давал 0 · Moet Ice 0.75
        "p25": 3,         # расчёт давал 2 · Glenfiddich 12Y 1 ltr
        "p27": 4,         # расчёт давал 5 · Glenfiddich 18Y 0.75 ltr
        "p99": 5,         # расчёт давал 1 · Rimapere Sauvignon Blanc 0.75
        "p103": 5,        # расчёт давал 2 · Gavi Di Gavi 0.75
        "p101": 5,        # расчёт давал 0 · Louis Moreau Chablis 0.75
        "p102": 5,        # расчёт давал 2 · Bourgogne Louis Jadot 0.75
        "p107": 5,        # расчёт давал 2 · Castel Barreyres 0.75
        "p109": 10,       # расчёт давал 6 · Chateau Saint Leon 0.75
        "p110": 5,        # расчёт давал 1 · Campo Viejo Reserva 0.75
        "p113": 3,        # расчёт давал 2 · Campo Viejo Gran Reserva 0.75
        "p19": 2,         # расчёт давал 1 · Jack Daniels Honey 1 ltr
        "p46": 2,         # расчёт давал 0.5 · Bacardi Breezer Melon 0.275 bottle
        "p24": 1,         # расчёт давал 0 · Chivas Regal 25Y 0.7 ltr
        "p73": 2,         # расчёт давал 0 · Clase Azul Reposado 70/75cl
        "p108": 2,        # расчёт давал 1 · Chateau Perron 0.75
        "p100": 5,        # расчёт давал 2 · Calvet Sancerre 0.75
        "p111": 3,        # расчёт давал 2 · Chateau Des Laurets 0.75
        "p39": 2,         # расчёт давал 1 · Guinness 0.44 can
        "p40": 3,         # расчёт давал 2.5 · XXL Vodka 0.25 can
        "p60": 3,         # расчёт давал 0 · Monkey 47 0.5 ltr
        "p28": 3,         # расчёт давал 0 · Macallan 12Y 0.7 ltr
        "p29": 3,         # расчёт давал 0 · Macallan 15Y 0.7 ltr
        "p30": 2,         # расчёт давал 0 · Macallan 18Y 0.75 ltr
        "p82": 1,         # расчёт давал 0 · Tequila Rose Strawberry Cream 0.7 
        "p61": 1,         # расчёт давал 0 · Malfy Con Arancia 0.7 ltr
        "p63": 2,         # расчёт давал 0 · Drumshanbo Gunpowder 0.7 ltr
        "p119": 2,        # расчёт давал 0 · Saint Maur Rose 0.75
        "p114": 2,        # расчёт давал 0 · Chateau Lagrange 0.75
        "p89": 2,         # расчёт давал 0 · Ruinart Blanc 0.75
        "p104": 10,       # расчёт давал 6 · Oyster Bay Sauvignon Blanc 0.75
        "p15": 6,         # расчёт давал 8 · Ballantines Finest 1 ltr
    },
    "bbay": {     # B2, правок 97
        "p1": 40,         # расчёт давал 60 · Absolut 1 ltr
        "p10": 40,        # расчёт давал 60 · Red Label 1 ltr
        "p11": 14,        # расчёт давал 19 · Black Label 1 ltr
        "p12": 14,        # расчёт давал 7 · Jack Daniels 1 ltr
        "p13": 14,        # расчёт давал 19 · Chivas Regal 12Y 1 ltr
        "p31": 15,        # расчёт давал 22.5 · Heineken 0.33 can
        "p33": 12,        # расчёт давал 16.5 · Budweiser 0.33 can
        "p47": 15,        # расчёт давал 14.5 · Carlsberg 0.5 can
        "p37": 5,         # расчёт давал 7.5 · Red Horse 0.5 can
        "p38": 3,         # расчёт давал 2 · Amstel Light 0.33 can
        "p43": 15,        # расчёт давал 22.5 · Corona Extra 0.355 bottle
        "p36": 2.5,       # расчёт давал 1.5 · Stella Artois 0.33 bottle
        "p34": 2.5,       # расчёт давал 1.5 · Budweiser 0.33 bottle
        "p45": 2.5,       # расчёт давал 1.5 · Smirnoff Ice 0.275 bottle
        "p44": 2.5,       # расчёт давал 2 · Peroni Nastro Azzurro 0.33 bottle
        "p123": 15,       # расчёт давал 1 · Jacob Creek Chardonnay 0.75
        "p105": 15,       # расчёт давал 11 · Jacob Creek Shiraz 0.75
        "p97": 10,        # расчёт давал 8 · Pinot Grigio Cesari 0.75
        "p98": 20,        # расчёт давал 30 · Le Grand Noir Sauvignon Blanc 0.75
        "p106": 10,       # расчёт давал 7 · Le Grand Noir Merlot 0.75
        "p120": 4,        # расчёт давал 1 · MiP Collection Rose 0.75
        "p117": 10,       # расчёт давал 4 · Chateau Ksara Rose 0.75
        "p115": 15,       # расчёт давал 12 · Mateus Rose 0.75
        "p48": 5,         # расчёт давал 4 · Bacardi White 1 ltr
        "p49": 5,         # расчёт давал 3 · Bacardi Black 1 ltr
        "p50": 5,         # расчёт давал 2 · Bacardi Gold 1 ltr
        "p65": 8,         # расчёт давал 6 · Jose Cuervo Gold 1 ltr
        "p64": 10,        # расчёт давал 13 · Jose Cuervo Silver 1 ltr
        "p59": 6,         # расчёт давал 2 · Tanqueray 1 ltr
        "p56": 8,         # расчёт давал 9 · Bombay Sapphire 1 ltr
        "p57": 8,         # расчёт давал 9 · Hendrick's 1 ltr
        "p51": 6,         # расчёт давал 0 · Captain Morgan Black 1 ltr
        "p52": 6,         # расчёт давал 2 · Captain Morgan Gold 1 ltr
        "p53": 4,         # расчёт давал 1 · Malibu 1 ltr
        "p78": 4,         # расчёт давал 2 · Baileys 1 ltr
        "p79": 2,         # расчёт давал 1 · Amarula 1 ltr
        "p23": 5,         # расчёт давал 2 · J&B 1 ltr
        "p121": 2,        # расчёт давал 1 · Drostdy Hof Premier Grand Cru 5 lt
        "p54": 3,         # расчёт давал 1 · Martini Bianco 1 ltr
        "p5": 10,         # расчёт давал 12 · Smirnoff Vodka 1 ltr
        "p2": 5,          # расчёт давал 4 · Stolichnaya 1 ltr
        "p3": 5,          # расчёт давал 3 · Russian Standard 1 ltr
        "p8": 10,         # расчёт давал 8 · Belvedere 1 ltr
        "p7": 15,         # расчёт давал 21 · Grey Goose 1 ltr
        "p6": 5,          # расчёт давал 4 · Beluga 0.7 ltr
        "p9": 5,          # расчёт давал 2 · Ciroc 1 ltr
        "p4": 3,          # расчёт давал 2 · Skyy Vodka 1 ltr
        "p16": 5,         # расчёт давал 2 · Double Black 1 ltr
        "p21": 2,         # расчёт давал 0 · Blue Label 1 ltr
        "p75": 4,         # расчёт давал 2 · Hennessy VSOP 1 ltr
        "p76": 2,         # расчёт давал 0 · Hennessy XO 1 ltr
        "p77": 4,         # расчёт давал 0 · Remy Martin VSOP 1 ltr
        "p18": 6,         # расчёт давал 9 · Chivas Regal 18Y 1 ltr
        "p22": 2,         # расчёт давал 0 · Chivas Royal Salute 21Y 1 ltr
        "p68": 4,         # расчёт давал 2 · Patron Gold 0.75 ltr
        "p69": 4,         # расчёт давал 3 · Don Julio Blanco 70/75cl
        "p70": 4,         # расчёт давал 3 · Don Julio Reposado 70/75cl
        "p72": 2,         # расчёт давал 0 · Don Julio 1942 70/75cl
        "p94": 8,         # расчёт давал 4 · Martini Asti 0.75
        "p96": 8,         # расчёт давал 7 · Jacob Creek Chardonnay Pinot Noir 
        "p91": 8,         # расчёт давал 4 · Bottega Prosecco 0.75
        "p92": 6,         # расчёт давал 1 · Bottega Rose 0.75
        "p93": 6,         # расчёт давал 2 · Bottega Gold 0.75
        "p88": 6,         # расчёт давал 4 · Veuve Clicquot 0.75
        "p85": 15,        # расчёт давал 18 · Moet Brut 0.75
        "p87": 6,         # расчёт давал 0 · Moet Ice 0.75
        "p90": 2,         # расчёт давал 0 · Dom Perignon 0.75
        "p25": 3,         # расчёт давал 2 · Glenfiddich 12Y 1 ltr
        "p99": 5,         # расчёт давал 3 · Rimapere Sauvignon Blanc 0.75
        "p103": 5,        # расчёт давал 3 · Gavi Di Gavi 0.75
        "p101": 5,        # расчёт давал 0 · Louis Moreau Chablis 0.75
        "p102": 5,        # расчёт давал 6 · Bourgogne Louis Jadot 0.75
        "p110": 5,        # расчёт давал 3 · Campo Viejo Reserva 0.75
        "p113": 5,        # расчёт давал 4 · Campo Viejo Gran Reserva 0.75
        "p116": 5,        # расчёт давал 4 · Minuty Cotes De Provence 0.75
        "p19": 2,         # расчёт давал 1 · Jack Daniels Honey 1 ltr
        "p46": 2.5,       # расчёт давал 1 · Bacardi Breezer Melon 0.275 bottle
        "p41": 2.5,       # расчёт давал 2 · Asahi Super Dry 0.33 bottle
        "p81": 3,         # расчёт давал 2 · Aperol 1 ltr
        "p24": 2,         # расчёт давал 0 · Chivas Regal 25Y 0.7 ltr
        "p73": 2,         # расчёт давал 0 · Clase Azul Reposado 70/75cl
        "p108": 3,        # расчёт давал 1 · Chateau Perron 0.75
        "p112": 3,        # расчёт давал 0 · La Celia Malbec 0.75
        "p100": 3,        # расчёт давал 1 · Calvet Sancerre 0.75
        "p39": 3,         # расчёт давал 2 · Guinness 0.44 can
        "p40": 5,         # расчёт давал 2.5 · XXL Vodka 0.25 can
        "p60": 3,         # расчёт давал 2 · Monkey 47 0.5 ltr
        "p20": 2,         # расчёт давал 1 · Gentleman Jack 1 ltr
        "p28": 3,         # расчёт давал 0 · Macallan 12Y 0.7 ltr
        "p30": 3,         # расчёт давал 5 · Macallan 18Y 0.75 ltr
        "p82": 2,         # расчёт давал 0 · Tequila Rose Strawberry Cream 0.7 
        "p61": 2,         # расчёт давал 0 · Malfy Con Arancia 0.7 ltr
        "p63": 2,         # расчёт давал 0 · Drumshanbo Gunpowder 0.7 ltr
        "p119": 3,        # расчёт давал 0 · Saint Maur Rose 0.75
        "p114": 2,        # расчёт давал 0 · Chateau Lagrange 0.75
        "p89": 3,         # расчёт давал 2 · Ruinart Blanc 0.75
        "p104": 12,       # расчёт давал 16 · Oyster Bay Sauvignon Blanc 0.75
    },
    "silicon": {     # B3, правок 76
        "p1": 50,         # расчёт давал 67 · Absolut 1 ltr
        "p10": 35,        # расчёт давал 34 · Red Label 1 ltr
        "p11": 10,        # расчёт давал 9 · Black Label 1 ltr
        "p12": 10,        # расчёт давал 4 · Jack Daniels 1 ltr
        "p13": 12,        # расчёт давал 14 · Chivas Regal 12Y 1 ltr
        "p31": 15,        # расчёт давал 22.5 · Heineken 0.33 can
        "p47": 10,        # расчёт давал 5 · Carlsberg 0.5 can
        "p37": 5,         # расчёт давал 4 · Red Horse 0.5 can
        "p38": 7,         # расчёт давал 7.5 · Amstel Light 0.33 can
        "p43": 5,         # расчёт давал 7.5 · Corona Extra 0.355 bottle
        "p36": 2.5,       # расчёт давал 1.5 · Stella Artois 0.33 bottle
        "p32": 3,         # расчёт давал 1.5 · Heineken 0.33 bottle
        "p34": 3,         # расчёт давал 1 · Budweiser 0.33 bottle
        "p45": 3,         # расчёт давал 1.5 · Smirnoff Ice 0.275 bottle
        "p44": 3,         # расчёт давал 1.5 · Peroni Nastro Azzurro 0.33 bottle
        "p123": 10,       # расчёт давал 1 · Jacob Creek Chardonnay 0.75
        "p105": 10,       # расчёт давал 6 · Jacob Creek Shiraz 0.75
        "p97": 12,        # расчёт давал 11 · Pinot Grigio Cesari 0.75
        "p98": 10,        # расчёт давал 8 · Le Grand Noir Sauvignon Blanc 0.75
        "p106": 10,       # расчёт давал 8 · Le Grand Noir Merlot 0.75
        "p117": 10,       # расчёт давал 2 · Chateau Ksara Rose 0.75
        "p48": 8,         # расчёт давал 9 · Bacardi White 1 ltr
        "p49": 5,         # расчёт давал 2 · Bacardi Black 1 ltr
        "p50": 5,         # расчёт давал 3 · Bacardi Gold 1 ltr
        "p64": 8,         # расчёт давал 6 · Jose Cuervo Silver 1 ltr
        "p59": 4,         # расчёт давал 1 · Tanqueray 1 ltr
        "p58": 4,         # расчёт давал 3 · Gordon Pink 0.7 ltr
        "p56": 8,         # расчёт давал 11 · Bombay Sapphire 1 ltr
        "p57": 4,         # расчёт давал 5 · Hendrick's 1 ltr
        "p51": 4,         # расчёт давал 0 · Captain Morgan Black 1 ltr
        "p52": 4,         # расчёт давал 2 · Captain Morgan Gold 1 ltr
        "p53": 4,         # расчёт давал 2 · Malibu 1 ltr
        "p79": 2,         # расчёт давал 1 · Amarula 1 ltr
        "p23": 3,         # расчёт давал 1 · J&B 1 ltr
        "p54": 3,         # расчёт давал 1 · Martini Bianco 1 ltr
        "p5": 5,          # расчёт давал 9 · Smirnoff Vodka 1 ltr
        "p2": 5,          # расчёт давал 4 · Stolichnaya 1 ltr
        "p3": 3,          # расчёт давал 1 · Russian Standard 1 ltr
        "p4": 2,          # расчёт давал 1 · Skyy Vodka 1 ltr
        "p84": 2,         # расчёт давал 1 · Efe Raki 1 ltr
        "p16": 3,         # расчёт давал 2 · Double Black 1 ltr
        "p76": 1,         # расчёт давал 0 · Hennessy XO 1 ltr
        "p77": 2,         # расчёт давал 0 · Remy Martin VSOP 1 ltr
        "p22": 1,         # расчёт давал 0 · Chivas Royal Salute 21Y 1 ltr
        "p67": 4,         # расчёт давал 2 · Patron Silver 0.75 ltr
        "p69": 3,         # расчёт давал 0 · Don Julio Blanco 70/75cl
        "p72": 1,         # расчёт давал 0 · Don Julio 1942 70/75cl
        "p94": 3,         # расчёт давал 2 · Martini Asti 0.75
        "p96": 4,         # расчёт давал 6 · Jacob Creek Chardonnay Pinot Noir 
        "p88": 2,         # расчёт давал 0 · Veuve Clicquot 0.75
        "p85": 3,         # расчёт давал 1 · Moet Brut 0.75
        "p86": 2,         # расчёт давал 0 · Moet Rose 0.75
        "p87": 2,         # расчёт давал 0 · Moet Ice 0.75
        "p90": 1,         # расчёт давал 0 · Dom Perignon 0.75
        "p27": 2,         # расчёт давал 0 · Glenfiddich 18Y 0.75 ltr
        "p103": 3,        # расчёт давал 2 · Gavi Di Gavi 0.75
        "p101": 4,        # расчёт давал 0 · Louis Moreau Chablis 0.75
        "p102": 3,        # расчёт давал 2 · Bourgogne Louis Jadot 0.75
        "p19": 1,         # расчёт давал 2 · Jack Daniels Honey 1 ltr
        "p41": 2,         # расчёт давал 1 · Asahi Super Dry 0.33 bottle
        "p81": 2,         # расчёт давал 1 · Aperol 1 ltr
        "p24": 1,         # расчёт давал 0 · Chivas Regal 25Y 0.7 ltr
        "p73": 1,         # расчёт давал 2 · Clase Azul Reposado 70/75cl
        "p112": 2,        # расчёт давал 0 · La Celia Malbec 0.75
        "p100": 2,        # расчёт давал 1 · Calvet Sancerre 0.75
        "p39": 1,         # расчёт давал 0 · Guinness 0.44 can
        "p40": 5,         # расчёт давал 3.5 · XXL Vodka 0.25 can
        "p60": 1,         # расчёт давал 0 · Monkey 47 0.5 ltr
        "p28": 1,         # расчёт давал 0 · Macallan 12Y 0.7 ltr
        "p29": 1,         # расчёт давал 0 · Macallan 15Y 0.7 ltr
        "p30": 1,         # расчёт давал 0 · Macallan 18Y 0.75 ltr
        "p42": 3,         # расчёт давал 1 · Hoegaarden 0.33 bottle
        "p119": 1,        # расчёт давал 0 · Saint Maur Rose 0.75
        "p89": 2,         # расчёт давал 0 · Ruinart Blanc 0.75
        "p104": 5,        # расчёт давал 3 · Oyster Bay Sauvignon Blanc 0.75
        "p15": 5,         # расчёт давал 3 · Ballantines Finest 1 ltr
    },
    "alguses": {     # B4, правок 92
        "p10": 45,        # расчёт давал 47 · Red Label 1 ltr
        "p12": 8,         # расчёт давал 5 · Jack Daniels 1 ltr
        "p31": 15,        # расчёт давал 16 · Heineken 0.33 can
        "p47": 10,        # расчёт давал 13.5 · Carlsberg 0.5 can
        "p37": 5,         # расчёт давал 3.5 · Red Horse 0.5 can
        "p38": 3,         # расчёт давал 1.5 · Amstel Light 0.33 can
        "p43": 5,         # расчёт давал 7.5 · Corona Extra 0.355 bottle
        "p36": 2.5,       # расчёт давал 0.5 · Stella Artois 0.33 bottle
        "p35": 3,         # расчёт давал 1 · Stella Artois 0.33 can
        "p34": 3,         # расчёт давал 1 · Budweiser 0.33 bottle
        "p45": 2,         # расчёт давал 0.5 · Smirnoff Ice 0.275 bottle
        "p44": 2,         # расчёт давал 0.5 · Peroni Nastro Azzurro 0.33 bottle
        "p123": 7,        # расчёт давал 1 · Jacob Creek Chardonnay 0.75
        "p105": 7,        # расчёт давал 5 · Jacob Creek Shiraz 0.75
        "p97": 7,         # расчёт давал 4 · Pinot Grigio Cesari 0.75
        "p98": 7,         # расчёт давал 3 · Le Grand Noir Sauvignon Blanc 0.75
        "p106": 7,        # расчёт давал 3 · Le Grand Noir Merlot 0.75
        "p117": 7,        # расчёт давал 2 · Chateau Ksara Rose 0.75
        "p115": 7,        # расчёт давал 4 · Mateus Rose 0.75
        "p48": 6,         # расчёт давал 4 · Bacardi White 1 ltr
        "p49": 5,         # расчёт давал 2 · Bacardi Black 1 ltr
        "p50": 5,         # расчёт давал 1 · Bacardi Gold 1 ltr
        "p64": 5,         # расчёт давал 4 · Jose Cuervo Silver 1 ltr
        "p58": 2,         # расчёт давал 1 · Gordon Pink 0.7 ltr
        "p55": 5,         # расчёт давал 6 · Gordon's 1 ltr
        "p51": 3,         # расчёт давал 0 · Captain Morgan Black 1 ltr
        "p52": 3,         # расчёт давал 1 · Captain Morgan Gold 1 ltr
        "p53": 3,         # расчёт давал 1 · Malibu 1 ltr
        "p78": 3,         # расчёт давал 1 · Baileys 1 ltr
        "p79": 2,         # расчёт давал 1 · Amarula 1 ltr
        "p14": 5,         # расчёт давал 3 · Jameson 1 ltr
        "p23": 3,         # расчёт давал 2 · J&B 1 ltr
        "p54": 2,         # расчёт давал 1 · Martini Bianco 1 ltr
        "p2": 5,          # расчёт давал 3 · Stolichnaya 1 ltr
        "p3": 3,          # расчёт давал 2 · Russian Standard 1 ltr
        "p8": 4,          # расчёт давал 2 · Belvedere 1 ltr
        "p7": 8,          # расчёт давал 6 · Grey Goose 1 ltr
        "p6": 2,          # расчёт давал 1 · Beluga 0.7 ltr
        "p9": 2,          # расчёт давал 1 · Ciroc 1 ltr
        "p4": 2,          # расчёт давал 1 · Skyy Vodka 1 ltr
        "p16": 3,         # расчёт давал 2 · Double Black 1 ltr
        "p21": 2,         # расчёт давал 0 · Blue Label 1 ltr
        "p75": 2,         # расчёт давал 0 · Hennessy VSOP 1 ltr
        "p76": 1,         # расчёт давал 0 · Hennessy XO 1 ltr
        "p77": 2,         # расчёт давал 0 · Remy Martin VSOP 1 ltr
        "p22": 1,         # расчёт давал 0 · Chivas Royal Salute 21Y 1 ltr
        "p67": 3,         # расчёт давал 0 · Patron Silver 0.75 ltr
        "p68": 3,         # расчёт давал 2 · Patron Gold 0.75 ltr
        "p69": 2,         # расчёт давал 0 · Don Julio Blanco 70/75cl
        "p70": 2,         # расчёт давал 0 · Don Julio Reposado 70/75cl
        "p71": 2,         # расчёт давал 0 · Don Julio Anejo 70/75cl
        "p72": 1,         # расчёт давал 0 · Don Julio 1942 70/75cl
        "p91": 4,         # расчёт давал 1 · Bottega Prosecco 0.75
        "p92": 3,         # расчёт давал 1 · Bottega Rose 0.75
        "p93": 3,         # расчёт давал 1 · Bottega Gold 0.75
        "p88": 2,         # расчёт давал 0 · Veuve Clicquot 0.75
        "p85": 4,         # расчёт давал 1 · Moet Brut 0.75
        "p86": 2,         # расчёт давал 0 · Moet Rose 0.75
        "p87": 2,         # расчёт давал 0 · Moet Ice 0.75
        "p90": 1,         # расчёт давал 0 · Dom Perignon 0.75
        "p26": 2,         # расчёт давал 3 · Glenfiddich 15Y 1 ltr
        "p99": 2,         # расчёт давал 1 · Rimapere Sauvignon Blanc 0.75
        "p103": 3,        # расчёт давал 1 · Gavi Di Gavi 0.75
        "p101": 3,        # расчёт давал 0 · Louis Moreau Chablis 0.75
        "p102": 3,        # расчёт давал 1 · Bourgogne Louis Jadot 0.75
        "p107": 3,        # расчёт давал 1 · Castel Barreyres 0.75
        "p109": 4,        # расчёт давал 2 · Chateau Saint Leon 0.75
        "p110": 3,        # расчёт давал 2 · Campo Viejo Reserva 0.75
        "p113": 3,        # расчёт давал 1 · Campo Viejo Gran Reserva 0.75
        "p116": 3,        # расчёт давал 1 · Minuty Cotes De Provence 0.75
        "p118": 3,        # расчёт давал 2 · Whispering Angel 0.75
        "p19": 1,         # расчёт давал 2 · Jack Daniels Honey 1 ltr
        "p46": 2,         # расчёт давал 1 · Bacardi Breezer Melon 0.275 bottle
        "p41": 2,         # расчёт давал 1 · Asahi Super Dry 0.33 bottle
        "p24": 1,         # расчёт давал 0 · Chivas Regal 25Y 0.7 ltr
        "p73": 1,         # расчёт давал 0 · Clase Azul Reposado 70/75cl
        "p108": 2,        # расчёт давал 1 · Chateau Perron 0.75
        "p112": 2,        # расчёт давал 0 · La Celia Malbec 0.75
        "p100": 2,        # расчёт давал 1 · Calvet Sancerre 0.75
        "p111": 2,        # расчёт давал 1 · Chateau Des Laurets 0.75
        "p39": 1,         # расчёт давал 0 · Guinness 0.44 can
        "p40": 5,         # расчёт давал 2.5 · XXL Vodka 0.25 can
        "p60": 1,         # расчёт давал 0 · Monkey 47 0.5 ltr
        "p28": 1,         # расчёт давал 0 · Macallan 12Y 0.7 ltr
        "p29": 1,         # расчёт давал 0 · Macallan 15Y 0.7 ltr
        "p30": 1,         # расчёт давал 0 · Macallan 18Y 0.75 ltr
        "p42": 3,         # расчёт давал 2.5 · Hoegaarden 0.33 bottle
        "p114": 2,        # расчёт давал 0 · Chateau Lagrange 0.75
        "p89": 3,         # расчёт давал 0 · Ruinart Blanc 0.75
        "p95": 2,         # расчёт давал 1 · Zonin Prosecco 0.75
        "p104": 10,       # расчёт давал 9 · Oyster Bay Sauvignon Blanc 0.75
        "p15": 6,         # расчёт давал 8 · Ballantines Finest 1 ltr
    },
    "tecom": {     # B5, правок 65
        "p1": 30,         # расчёт давал 43 · Absolut 1 ltr
        "p10": 25,        # расчёт давал 26 · Red Label 1 ltr
        "p12": 5,         # расчёт давал 3 · Jack Daniels 1 ltr
        "p31": 8,         # расчёт давал 12 · Heineken 0.33 can
        "p33": 8,         # расчёт давал 6 · Budweiser 0.33 can
        "p47": 8,         # расчёт давал 7 · Carlsberg 0.5 can
        "p43": 7,         # расчёт давал 10.5 · Corona Extra 0.355 bottle
        "p123": 10,       # расчёт давал 2 · Jacob Creek Chardonnay 0.75
        "p105": 10,       # расчёт давал 8 · Jacob Creek Shiraz 0.75
        "p97": 12,        # расчёт давал 13 · Pinot Grigio Cesari 0.75
        "p98": 12,        # расчёт давал 18 · Le Grand Noir Sauvignon Blanc 0.75
        "p106": 10,       # расчёт давал 4 · Le Grand Noir Merlot 0.75
        "p120": 3,        # расчёт давал 2 · MiP Collection Rose 0.75
        "p117": 3,        # расчёт давал 2 · Chateau Ksara Rose 0.75
        "p65": 4,         # расчёт давал 2 · Jose Cuervo Gold 1 ltr
        "p64": 5,         # расчёт давал 7 · Jose Cuervo Silver 1 ltr
        "p59": 3,         # расчёт давал 2 · Tanqueray 1 ltr
        "p57": 6,         # расчёт давал 5 · Hendrick's 1 ltr
        "p51": 3,         # расчёт давал 0 · Captain Morgan Black 1 ltr
        "p52": 3,         # расчёт давал 2 · Captain Morgan Gold 1 ltr
        "p53": 2,         # расчёт давал 1 · Malibu 1 ltr
        "p23": 3,         # расчёт давал 1 · J&B 1 ltr
        "p2": 3,          # расчёт давал 1 · Stolichnaya 1 ltr
        "p3": 3,          # расчёт давал 2 · Russian Standard 1 ltr
        "p80": 3,         # расчёт давал 2 · Jagermeister 1 ltr
        "p8": 3,          # расчёт давал 2 · Belvedere 1 ltr
        "p7": 12,         # расчёт давал 18 · Grey Goose 1 ltr
        "p6": 3,          # расчёт давал 2 · Beluga 0.7 ltr
        "p9": 2,          # расчёт давал 1 · Ciroc 1 ltr
        "p16": 1,         # расчёт давал 2 · Double Black 1 ltr
        "p74": 2,         # расчёт давал 0 · Hennessy VS 1 ltr
        "p76": 1,         # расчёт давал 0 · Hennessy XO 1 ltr
        "p77": 1,         # расчёт давал 0 · Remy Martin VSOP 1 ltr
        "p18": 4,         # расчёт давал 3 · Chivas Regal 18Y 1 ltr
        "p22": 1,         # расчёт давал 0 · Chivas Royal Salute 21Y 1 ltr
        "p68": 3,         # расчёт давал 0 · Patron Gold 0.75 ltr
        "p72": 2,         # расчёт давал 0 · Don Julio 1942 70/75cl
        "p92": 3,         # расчёт давал 2 · Bottega Rose 0.75
        "p93": 3,         # расчёт давал 2 · Bottega Gold 0.75
        "p88": 3,         # расчёт давал 2 · Veuve Clicquot 0.75
        "p85": 10,        # расчёт давал 5 · Moet Brut 0.75
        "p26": 2,         # расчёт давал 0 · Glenfiddich 15Y 1 ltr
        "p99": 3,         # расчёт давал 2 · Rimapere Sauvignon Blanc 0.75
        "p103": 3,        # расчёт давал 2 · Gavi Di Gavi 0.75
        "p101": 4,        # расчёт давал 0 · Louis Moreau Chablis 0.75
        "p102": 4,        # расчёт давал 3 · Bourgogne Louis Jadot 0.75
        "p107": 2,        # расчёт давал 3 · Castel Barreyres 0.75
        "p109": 10,       # расчёт давал 6 · Chateau Saint Leon 0.75
        "p110": 6,        # расчёт давал 2 · Campo Viejo Reserva 0.75
        "p113": 6,        # расчёт давал 3 · Campo Viejo Gran Reserva 0.75
        "p116": 4,        # расчёт давал 3 · Minuty Cotes De Provence 0.75
        "p118": 3,        # расчёт давал 4 · Whispering Angel 0.75
        "p19": 2,         # расчёт давал 3 · Jack Daniels Honey 1 ltr
        "p81": 2,         # расчёт давал 1 · Aperol 1 ltr
        "p24": 2,         # расчёт давал 0 · Chivas Regal 25Y 0.7 ltr
        "p73": 3,         # расчёт давал 0 · Clase Azul Reposado 70/75cl
        "p60": 1,         # расчёт давал 2 · Monkey 47 0.5 ltr
        "p28": 2,         # расчёт давал 0 · Macallan 12Y 0.7 ltr
        "p30": 1,         # расчёт давал 0 · Macallan 18Y 0.75 ltr
        "p42": 5,         # расчёт давал 0 · Hoegaarden 0.33 bottle
        "p61": 1,         # расчёт давал 0 · Malfy Con Arancia 0.7 ltr
        "p89": 4,         # расчёт давал 0 · Ruinart Blanc 0.75
        "p95": 6,         # расчёт давал 7 · Zonin Prosecco 0.75
        "p104": 6,        # расчёт давал 9 · Oyster Bay Sauvignon Blanc 0.75
        "p15": 3,         # расчёт давал 1 · Ballantines Finest 1 ltr
    },
}
