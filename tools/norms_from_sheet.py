"""Нормы склада с бумажки операторов (владелец, 17 сен 2026).

Откуда
------
Два листа каталога на 123 строки, на которых операторы от руки проставили норму
по каждому району: первый — колонки B1 B2 B3, второй — B4 B5. Снимки разобраны,
сверены с владельцем и приняты как итоговые. Строки листа соответствуют
config_stock_order.STOCK_ORDER — тот же порядок обхода полок, что и в пересчёте.

Прочерк на бумаге — это ноль, а не «не знаем»: «в этом районе не держим». База
такой ноль хранит как норму (db.get_stock_norms), и заявка по нулю ничего не
просит.

Чего здесь нет: строк 124–126 каталога (табак). На листе их нет, и нормы по ним
инструмент не трогает — они закупаются мимо магазина.

Что делает
----------
Заменяет ручные нормы в stock_norms на эти и ставит stock_norm_rule kind=sheet —
чтобы в STAR под заявкой было написано, откуда взяты нормы. Прежние нормы и
правило сохраняются в файл: откат возвращает ровно то, что было.

Запуск на сервере из /opt/ambar:
    PYTHONPATH=/opt/ambar venv/bin/python tools/norms_from_sheet.py            # что изменится
    PYTHONPATH=/opt/ambar venv/bin/python tools/norms_from_sheet.py --apply    # записать
    PYTHONPATH=/opt/ambar venv/bin/python tools/norms_from_sheet.py --rollback <копия.json>
"""
import asyncio, json, sys, time
from datetime import datetime, timezone

DAY = "2026-09-17"          # день, которым датирована бумажка
NOTE = "нормы с листа операторов 17.09 — норма района по каждой позиции"

# район → {позиция: норма}. Прочерк на бумаге — ноль: «в этом районе не держим».
SHEET = {
    "jvc": {     # B1
        "p1": 36,       #   1 ABSOLUT BLUE LTR               Absolut 1 ltr
        "p10": 36,      #   2 J/W RED LABEL 1 LTR            Red Label 1 ltr
        "p11": 12,      #   3 J/W BLAK LABEL 1 LTR           Black Label 1 ltr
        "p12": 12,      #   4 JACK DANIELS LTR               Jack Daniels 1 ltr
        "p13": 12,      #   5 CHIVAS REGAL 1 LTR             Chivas Regal 12Y 1 ltr
        "p31": 12,      #   6 HEINEKEN BEER CANS 33CL        Heineken 0.33 can
        "p33": 10,      #   7 BUDWEISER BEER CAN 33/35       Budweiser 0.33 can
        "p47": 10,      #   8 CARLSBERG 50CL Can             Carlsberg 0.5 can
        "p37": 4,       #   9 RED HORSE 50CL Can             Red Horse 0.5 can
        "p38": 2.5,     #  10 AMSTEL LIGHT Slim Can 35       Amstel Light 0.33 can
        "p43": 7,       #  11 CORONA BEER BTL 35.5CL         Corona Extra 0.355 bottle
        "p36": 2.5,     #  12 STELLA 33CL BTLS               Stella Artois 0.33 bottle
        "p35": 2.5,     #  13 STELLA ARTOIS 33 CL cans       Stella Artois 0.33 can
        "p32": 2.5,     #  14 HEINEKEN BEER BTL 33CL         Heineken 0.33 bottle
        "p34": 2.5,     #  15 BUDWEISER BERR BTL 33cl        Budweiser 0.33 bottle
        "p45": 2.5,     #  16 SMIRNOFF ICE RED 27,5CL        Smirnoff Ice 0.275 bottle
        "p44": 2.5,     #  17 PERONI NASTRO AZURO BEER       Peroni Nastro Azzurro 0.33 b
        "p123": 18,     #  18 JC CHARDONNAY 75CL             Jacob Creek Chardonnay 0.75
        "p105": 18,     #  19 JC SHIRAZ CABARNET 75CL        Jacob Creek Shiraz 0.75
        "p97": 12,      #  20 CES PINOT GRIG D VEN FIO       Pinot Grigio Cesari 0.75
        "p98": 12,      #  21 Le GRAND Noir SAUV BLANC       Le Grand Noir Sauvignon Blan
        "p106": 12,     #  22 Le GRAND Noir MERLOT 75C       Le Grand Noir Merlot 0.75
        "p120": 3,      #  23 MIP Collection ROSE Provienc 7 MiP Collection Rose 0.75
        "p117": 12,     #  24 CH KSARA SUNSET ROSE 75C       Chateau Ksara Rose 0.75
        "p115": 17,     #  25 MATEUS ROSE 75CL               Mateus Rose 0.75
        "p48": 6,       #  26 BACARDI WHITE RUM LTR          Bacardi White 1 ltr
        "p49": 6,       #  27 BACARDI BLACK. 1 LTR           Bacardi Black 1 ltr
        "p50": 6,       #  28 BACARDI GOLD LTR               Bacardi Gold 1 ltr
        "p65": 6,       #  29 JOSE CUERVO GOLD LTR           Jose Cuervo Gold 1 ltr
        "p64": 6,       #  30 JOSE CUERVO SILVER Espec       Jose Cuervo Silver 1 ltr
        "p59": 4,       #  31 TANQUERAY GIN LTR              Tanqueray 1 ltr
        "p58": 4,       #  32 GORDONS PINK GIN Ltr           Gordon Pink 0.7 ltr
        "p55": 6,       #  33 GORDONS GIN LTR                Gordon's 1 ltr
        "p56": 6,       #  34 BOMBAY SAPPHIRE GIN LTR        Bombay Sapphire 1 ltr
        "p57": 6,       #  35 HENDRICKS GIN 1 LTR            Hendrick's 1 ltr
        "p51": 6,       #  36 CAPTAIN MORGAN BLK LTR         Captain Morgan Black 1 ltr
        "p52": 6,       #  37 CAPTAIN MORGAN SPICED GO       Captain Morgan Gold 1 ltr
        "p53": 4,       #  38 MALIBU WHITHE RUM LTR          Malibu 1 ltr
        "p78": 4,       #  39 BAILEYS IRISH CREAM LTR        Baileys 1 ltr
        "p79": 2,       #  40 AMARULA CREAM LTR              Amarula 1 ltr
        "p14": 6,       #  41 JAMESON IRISH WSK LTR          Jameson 1 ltr
        "p23": 4,       #  42 J&B RARE SCOTCH 1 LTR          J&B 1 ltr
        "p122": 1,      #  43 D/H CLARNET SELECT 5LTR        Drostdy Hof Claret Select 5 
        "p121": 1,      #  44 D/H PREM GRN CRU 5LTR          Drostdy Hof Premier Grand Cr
        "p54": 4,       #  45 MARTINI BIANCO 1 LTR           Martini Bianco 1 ltr
        "p5": 6,        #  46 SMIRNOFF R/L 1 LTR             Smirnoff Vodka 1 ltr
        "p2": 6,        #  47 STOLICHNAYA VODKA LTR          Stolichnaya 1 ltr
        "p3": 6,        #  48 RUSSIAN STD. PETERS L          Russian Standard 1 ltr
        "p80": 6,       #  49 JAGERMEISTER 1 LTR             Jagermeister 1 ltr
        "p8": 12,       #  50 BELVEDERE VODKA LTR            Belvedere 1 ltr
        "p7": 12,       #  51 GREY GOOSE VODKA LTR           Grey Goose 1 ltr
        "p6": 6,        #  52 BELUGA NOBLE VODKA 70CL        Beluga 0.7 ltr
        "p9": 6,        #  53 CIROK VODKA LTR                Ciroc 1 ltr
        "p4": 4,        #  54 SKYY VODKA 1 LTR               Skyy Vodka 1 ltr
        "p83": 4,       #  55 ARAK TOUMA 50/54CL             Arak Touma 0.75 ltr
        "p84": 4,       #  56 EFE Fresh Grape RAKI LTR Green Efe Raki 1 ltr
        "p17": 6,       #  57 J/W GOLD LABEL RESERV 1        Gold Label 1 ltr
        "p16": 6,       #  58 J/W DOUBLE BLACK LTR           Double Black 1 ltr
        "p21": 2,       #  59 J/W BLUE LABEL 1 LTR           Blue Label 1 ltr
        "p74": 4,       #  60 HENNESSY VS LTR                Hennessy VS 1 ltr
        "p75": 4,       #  61 HENNESSY V.S.O.P 1 LTR Pr      Hennessy VSOP 1 ltr
        "p76": 2,       #  62 HENNESSY XO LTR                Hennessy XO 1 ltr
        "p77": 2,       #  63 REMY MARTIN VSOP LTR           Remy Martin VSOP 1 ltr
        "p18": 4,       #  64 CHIVAS 18 YRS LTR              Chivas Regal 18Y 1 ltr
        "p22": 2,       #  65 ROYAL SALUTE 21 YRS LTR        Chivas Royal Salute 21Y 1 lt
        "p66": 0,       #  66 PATRON COFFE                   Patron XO Cafe 0.75 ltr
        "p67": 4,       #  67 PATRON SILVER 75CL TEQUI       Patron Silver 0.75 ltr
        "p68": 4,       #  68 PATRON ANEJO 75CL GOLD T       Patron Gold 0.75 ltr
        "p69": 4,       #  69 DON JULIO BLANCO 70/75CL       Don Julio Blanco 70/75cl
        "p70": 4,       #  70 DON JULIO REPOSADO 70/75       Don Julio Reposado 70/75cl
        "p71": 4,       #  71 DON JULIO ANEJO 70/75CL        Don Julio Anejo 70/75cl
        "p72": 2,       #  72 DON JULIO 1942 ANEJO 70        Don Julio 1942 70/75cl
        "p94": 6,       #  73 ASTI MARTINI 75CL              Martini Asti 0.75
        "p96": 6,       #  74 JC CHARDONNAY PINOT NOIR       Jacob Creek Chardonnay Pinot
        "p91": 8,       #  75 BOTTEGA VINO D POET PROS       Bottega Prosecco 0.75
        "p92": 4,       #  76 BOTTEGA ROSE Proseco POE       Bottega Rose 0.75
        "p93": 4,       #  77 BOTTEGA GOLD BRUT 75C vi       Bottega Gold 0.75
        "p88": 4,       #  78 VEUVE CLICQUOT Y/L PONSR       Veuve Clicquot 0.75
        "p85": 10,      #  79 MOET & CHANDON BRUT IMP        Moet Brut 0.75
        "p86": 6,       #  80 MOET & CHANDON ROSE 75CL       Moet Rose 0.75
        "p87": 4,       #  81 MOET ICE IMPERIAL 75cl         Moet Ice 0.75
        "p90": 2,       #  82 DOM PERIGNON M&C 75CL          Dom Perignon 0.75
        "p25": 4,       #  83 GLENDFIDICH SPL R12YRS         Glenfiddich 12Y 1 ltr
        "p26": 4,       #  84 GLENDFIDICH 15 YRS LTR         Glenfiddich 15Y 1 ltr
        "p27": 4,       #  85 GLENDFIDICH 18Y Smal Bat       Glenfiddich 18Y 0.75 ltr
        "p99": 6,       #  86 BARON RIMAPERE SAUV BLAN       Rimapere Sauvignon Blanc 0.7
        "p103": 6,      #  87 MARCHESI GAVI D GAVI 75C       Gavi Di Gavi 0.75
        "p101": 6,      #  88 LAROCHE CHABLIS ST MARTI       Louis Moreau Chablis 0.75
        "p102": 6,      #  89 L J BOURGOGNE BL Cuv D ja      Bourgogne Louis Jadot 0.75
        "p107": 6,      #  90 CASTEL CH. BARREYRES HAUT M 75 Castel Barreyres 0.75
        "p109": 12,     #  91 CH SAINT LEON BOX SUP 75       Chateau Saint Leon 0.75
        "p110": 6,      #  92 CAMPO VIEJO RESERVA RIOJ       Campo Viejo Reserva 0.75
        "p113": 6,      #  93 CAMPO VIEJO GRAN RESERVA       Campo Viejo Gran Reserva 0.7
        "p116": 6,      #  94 M MINUTY ROSE PROVENCE         Minuty Cotes De Provence 0.7
        "p118": 6,      #  95 Cav D ESCLN WHISPERING         Whispering Angel 0.75
        "p19": 2,       #  96 JACK DANIELS HONEY LTR         Jack Daniels Honey 1 ltr
        "p46": 2.5,     #  97 BACCARDI BREEZER W/MELON       Bacardi Breezer Melon 0.275 
        "p41": 2.5,     #  98 ASAHI BEER BTLS SUPER DR       Asahi Super Dry 0.33 bottle
        "p81": 3,       #  99 APEROLE Aperitivo LTR          Aperol 1 ltr
        "p24": 1,       # 100 CHIVAS 25 YRS                  Chivas Regal 25Y 0.7 ltr
        "p73": 2,       # 101 CLASE AZUL Reposado 70/7       Clase Azul Reposado 70/75cl
        "p108": 4,      # 102 MS CH PERRON LALANDE D POMEROL Chateau Perron 0.75
        "p112": 0,      # 103 LA CELIA RESERVA MALBEC 75CL   La Celia Malbec 0.75
        "p100": 6,      # 104 CALVET SANCERRE Les Hautes     Calvet Sancerre 0.75
        "p111": 4,      # 105 CHATEAU des LAURETS Saint Emil Chateau Des Laurets 0.75
        "p39": 2,       # 106 GUINNESS BEER CANS 44cl        Guinness 0.44 can
        "p40": 4,       # 107 XXL VODKA MIX ENERGY CAN       XXL Vodka 0.25 can
        "p60": 4,       # 108 MONKEY 47 DRY GIN 50CL         Monkey 47 0.5 ltr
        "p20": 2,       # 109 GENTLEMAN JACK 1 LTR JD        Gentleman Jack 1 ltr
        "p28": 3,       # 110 MACALLAN 12 YR FIN TRIP        Macallan 12Y 0.7 ltr
        "p29": 3,       # 111 MACALLAN 15 YRS Double Ca      Macallan 15Y 0.7 ltr
        "p30": 2,       # 112 MACALLAN 18 YRS                Macallan 18Y 0.75 ltr
        "p42": 2.5,     # 113 HOEGARDEN BLANCHE 33CL B       Hoegaarden 0.33 bottle
        "p82": 2,       # 114 TEQUILA ROSE LIQUER 70C S/Bery Tequila Rose Strawberry Crea
        "p61": 2,       # 115 MALFY Con Ara Blood Orange GIN Malfy Con Arancia 0.7 ltr
        "p62": 2,       # 116 MALFY GIN ROSA 70cl GrapfruitE Malfy Rosa 0.7 ltr
        "p63": 2,       # 117 Drumshanb GUNPODER GIN         Drumshanbo Gunpowder 0.7 ltr
        "p119": 2,      # 118 CH SAINT MAUR L Exelenc ROS 7  Saint Maur Rose 0.75
        "p114": 2,      # 119 CH LAGRANGE 2010 St Julien     Chateau Lagrange 0.75
        "p89": 4,       # 120 RUINART BLANC D BLANC 75 CL    Ruinart Blanc 0.75
        "p95": 6,       # 121 ZONIN PROSECCO 75CL            Zonin Prosecco 0.75
        "p104": 12,     # 122 OYSTER BAY SAUVIGNON           Oyster Bay Sauvignon Blanc 0
        "p15": 6,       # 123 BAIANTINES  (дописано от руки) Ballantines Finest 1 ltr
    },
    "bbay": {     # B2
        "p1": 40,       #   1 ABSOLUT BLUE LTR               Absolut 1 ltr
        "p10": 40,      #   2 J/W RED LABEL 1 LTR            Red Label 1 ltr
        "p11": 14,      #   3 J/W BLAK LABEL 1 LTR           Black Label 1 ltr
        "p12": 14,      #   4 JACK DANIELS LTR               Jack Daniels 1 ltr
        "p13": 14,      #   5 CHIVAS REGAL 1 LTR             Chivas Regal 12Y 1 ltr
        "p31": 15,      #   6 HEINEKEN BEER CANS 33CL        Heineken 0.33 can
        "p33": 12,      #   7 BUDWEISER BEER CAN 33/35       Budweiser 0.33 can
        "p47": 15,      #   8 CARLSBERG 50CL Can             Carlsberg 0.5 can
        "p37": 5,       #   9 RED HORSE 50CL Can             Red Horse 0.5 can
        "p38": 2.5,     #  10 AMSTEL LIGHT Slim Can 35       Amstel Light 0.33 can
        "p43": 15,      #  11 CORONA BEER BTL 35.5CL         Corona Extra 0.355 bottle
        "p36": 2.5,     #  12 STELLA 33CL BTLS               Stella Artois 0.33 bottle
        "p35": 2.5,     #  13 STELLA ARTOIS 33 CL cans       Stella Artois 0.33 can
        "p32": 2.5,     #  14 HEINEKEN BEER BTL 33CL         Heineken 0.33 bottle
        "p34": 2.5,     #  15 BUDWEISER BERR BTL 33cl        Budweiser 0.33 bottle
        "p45": 2.5,     #  16 SMIRNOFF ICE RED 27,5CL        Smirnoff Ice 0.275 bottle
        "p44": 2.5,     #  17 PERONI NASTRO AZURO BEER       Peroni Nastro Azzurro 0.33 b
        "p123": 20,     #  18 JC CHARDONNAY 75CL             Jacob Creek Chardonnay 0.75
        "p105": 20,     #  19 JC SHIRAZ CABARNET 75CL        Jacob Creek Shiraz 0.75
        "p97": 14,      #  20 CES PINOT GRIG D VEN FIO       Pinot Grigio Cesari 0.75
        "p98": 20,      #  21 Le GRAND Noir SAUV BLANC       Le Grand Noir Sauvignon Blan
        "p106": 14,     #  22 Le GRAND Noir MERLOT 75C       Le Grand Noir Merlot 0.75
        "p120": 4,      #  23 MIP Collection ROSE Provienc 7 MiP Collection Rose 0.75
        "p117": 14,     #  24 CH KSARA SUNSET ROSE 75C       Chateau Ksara Rose 0.75
        "p115": 16,     #  25 MATEUS ROSE 75CL               Mateus Rose 0.75
        "p48": 6,       #  26 BACARDI WHITE RUM LTR          Bacardi White 1 ltr
        "p49": 6,       #  27 BACARDI BLACK. 1 LTR           Bacardi Black 1 ltr
        "p50": 6,       #  28 BACARDI GOLD LTR               Bacardi Gold 1 ltr
        "p65": 10,      #  29 JOSE CUERVO GOLD LTR           Jose Cuervo Gold 1 ltr
        "p64": 10,      #  30 JOSE CUERVO SILVER Espec       Jose Cuervo Silver 1 ltr
        "p59": 6,       #  31 TANQUERAY GIN LTR              Tanqueray 1 ltr
        "p58": 4,       #  32 GORDONS PINK GIN Ltr           Gordon Pink 0.7 ltr
        "p55": 8,       #  33 GORDONS GIN LTR                Gordon's 1 ltr
        "p56": 8,       #  34 BOMBAY SAPPHIRE GIN LTR        Bombay Sapphire 1 ltr
        "p57": 8,       #  35 HENDRICKS GIN 1 LTR            Hendrick's 1 ltr
        "p51": 6,       #  36 CAPTAIN MORGAN BLK LTR         Captain Morgan Black 1 ltr
        "p52": 6,       #  37 CAPTAIN MORGAN SPICED GO       Captain Morgan Gold 1 ltr
        "p53": 4,       #  38 MALIBU WHITHE RUM LTR          Malibu 1 ltr
        "p78": 4,       #  39 BAILEYS IRISH CREAM LTR        Baileys 1 ltr
        "p79": 2,       #  40 AMARULA CREAM LTR              Amarula 1 ltr
        "p14": 8,       #  41 JAMESON IRISH WSK LTR          Jameson 1 ltr
        "p23": 6,       #  42 J&B RARE SCOTCH 1 LTR          J&B 1 ltr
        "p122": 2,      #  43 D/H CLARNET SELECT 5LTR        Drostdy Hof Claret Select 5 
        "p121": 2,      #  44 D/H PREM GRN CRU 5LTR          Drostdy Hof Premier Grand Cr
        "p54": 4,       #  45 MARTINI BIANCO 1 LTR           Martini Bianco 1 ltr
        "p5": 10,       #  46 SMIRNOFF R/L 1 LTR             Smirnoff Vodka 1 ltr
        "p2": 8,        #  47 STOLICHNAYA VODKA LTR          Stolichnaya 1 ltr
        "p3": 8,        #  48 RUSSIAN STD. PETERS L          Russian Standard 1 ltr
        "p80": 10,      #  49 JAGERMEISTER 1 LTR             Jagermeister 1 ltr
        "p8": 14,       #  50 BELVEDERE VODKA LTR            Belvedere 1 ltr
        "p7": 14,       #  51 GREY GOOSE VODKA LTR           Grey Goose 1 ltr
        "p6": 6,        #  52 BELUGA NOBLE VODKA 70CL        Beluga 0.7 ltr
        "p9": 6,        #  53 CIROK VODKA LTR                Ciroc 1 ltr
        "p4": 4,        #  54 SKYY VODKA 1 LTR               Skyy Vodka 1 ltr
        "p83": 4,       #  55 ARAK TOUMA 50/54CL             Arak Touma 0.75 ltr
        "p84": 4,       #  56 EFE Fresh Grape RAKI LTR Green Efe Raki 1 ltr
        "p17": 6,       #  57 J/W GOLD LABEL RESERV 1        Gold Label 1 ltr
        "p16": 6,       #  58 J/W DOUBLE BLACK LTR           Double Black 1 ltr
        "p21": 2,       #  59 J/W BLUE LABEL 1 LTR           Blue Label 1 ltr
        "p74": 5,       #  60 HENNESSY VS LTR                Hennessy VS 1 ltr
        "p75": 5,       #  61 HENNESSY V.S.O.P 1 LTR Pr      Hennessy VSOP 1 ltr
        "p76": 2,       #  62 HENNESSY XO LTR                Hennessy XO 1 ltr
        "p77": 4,       #  63 REMY MARTIN VSOP LTR           Remy Martin VSOP 1 ltr
        "p18": 6,       #  64 CHIVAS 18 YRS LTR              Chivas Regal 18Y 1 ltr
        "p22": 2,       #  65 ROYAL SALUTE 21 YRS LTR        Chivas Royal Salute 21Y 1 lt
        "p66": 0,       #  66 PATRON COFFE                   Patron XO Cafe 0.75 ltr
        "p67": 6,       #  67 PATRON SILVER 75CL TEQUI       Patron Silver 0.75 ltr
        "p68": 4,       #  68 PATRON ANEJO 75CL GOLD T       Patron Gold 0.75 ltr
        "p69": 6,       #  69 DON JULIO BLANCO 70/75CL       Don Julio Blanco 70/75cl
        "p70": 8,       #  70 DON JULIO REPOSADO 70/75       Don Julio Reposado 70/75cl
        "p71": 6,       #  71 DON JULIO ANEJO 70/75CL        Don Julio Anejo 70/75cl
        "p72": 2,       #  72 DON JULIO 1942 ANEJO 70        Don Julio 1942 70/75cl
        "p94": 8,       #  73 ASTI MARTINI 75CL              Martini Asti 0.75
        "p96": 8,       #  74 JC CHARDONNAY PINOT NOIR       Jacob Creek Chardonnay Pinot
        "p91": 10,      #  75 BOTTEGA VINO D POET PROS       Bottega Prosecco 0.75
        "p92": 6,       #  76 BOTTEGA ROSE Proseco POE       Bottega Rose 0.75
        "p93": 6,       #  77 BOTTEGA GOLD BRUT 75C vi       Bottega Gold 0.75
        "p88": 6,       #  78 VEUVE CLICQUOT Y/L PONSR       Veuve Clicquot 0.75
        "p85": 15,      #  79 MOET & CHANDON BRUT IMP        Moet Brut 0.75
        "p86": 6,       #  80 MOET & CHANDON ROSE 75CL       Moet Rose 0.75
        "p87": 6,       #  81 MOET ICE IMPERIAL 75cl         Moet Ice 0.75
        "p90": 2,       #  82 DOM PERIGNON M&C 75CL          Dom Perignon 0.75
        "p25": 4,       #  83 GLENDFIDICH SPL R12YRS         Glenfiddich 12Y 1 ltr
        "p26": 4,       #  84 GLENDFIDICH 15 YRS LTR         Glenfiddich 15Y 1 ltr
        "p27": 4,       #  85 GLENDFIDICH 18Y Smal Bat       Glenfiddich 18Y 0.75 ltr
        "p99": 6,       #  86 BARON RIMAPERE SAUV BLAN       Rimapere Sauvignon Blanc 0.7
        "p103": 6,      #  87 MARCHESI GAVI D GAVI 75C       Gavi Di Gavi 0.75
        "p101": 6,      #  88 LAROCHE CHABLIS ST MARTI       Louis Moreau Chablis 0.75
        "p102": 6,      #  89 L J BOURGOGNE BL Cuv D ja      Bourgogne Louis Jadot 0.75
        "p107": 6,      #  90 CASTEL CH. BARREYRES HAUT M 75 Castel Barreyres 0.75
        "p109": 12,     #  91 CH SAINT LEON BOX SUP 75       Chateau Saint Leon 0.75
        "p110": 6,      #  92 CAMPO VIEJO RESERVA RIOJ       Campo Viejo Reserva 0.75
        "p113": 6,      #  93 CAMPO VIEJO GRAN RESERVA       Campo Viejo Gran Reserva 0.7
        "p116": 6,      #  94 M MINUTY ROSE PROVENCE         Minuty Cotes De Provence 0.7
        "p118": 6,      #  95 Cav D ESCLN WHISPERING         Whispering Angel 0.75
        "p19": 2,       #  96 JACK DANIELS HONEY LTR         Jack Daniels Honey 1 ltr
        "p46": 2.5,     #  97 BACCARDI BREEZER W/MELON       Bacardi Breezer Melon 0.275 
        "p41": 2.5,     #  98 ASAHI BEER BTLS SUPER DR       Asahi Super Dry 0.33 bottle
        "p81": 4,       #  99 APEROLE Aperitivo LTR          Aperol 1 ltr
        "p24": 2,       # 100 CHIVAS 25 YRS                  Chivas Regal 25Y 0.7 ltr
        "p73": 2,       # 101 CLASE AZUL Reposado 70/7       Clase Azul Reposado 70/75cl
        "p108": 4,      # 102 MS CH PERRON LALANDE D POMEROL Chateau Perron 0.75
        "p112": 4,      # 103 LA CELIA RESERVA MALBEC 75CL   La Celia Malbec 0.75
        "p100": 4,      # 104 CALVET SANCERRE Les Hautes     Calvet Sancerre 0.75
        "p111": 4,      # 105 CHATEAU des LAURETS Saint Emil Chateau Des Laurets 0.75
        "p39": 4,       # 106 GUINNESS BEER CANS 44cl        Guinness 0.44 can
        "p40": 6,       # 107 XXL VODKA MIX ENERGY CAN       XXL Vodka 0.25 can
        "p60": 4,       # 108 MONKEY 47 DRY GIN 50CL         Monkey 47 0.5 ltr
        "p20": 2,       # 109 GENTLEMAN JACK 1 LTR JD        Gentleman Jack 1 ltr
        "p28": 3,       # 110 MACALLAN 12 YR FIN TRIP        Macallan 12Y 0.7 ltr
        "p29": 3,       # 111 MACALLAN 15 YRS Double Ca      Macallan 15Y 0.7 ltr
        "p30": 3,       # 112 MACALLAN 18 YRS                Macallan 18Y 0.75 ltr
        "p42": 2.5,     # 113 HOEGARDEN BLANCHE 33CL B       Hoegaarden 0.33 bottle
        "p82": 2,       # 114 TEQUILA ROSE LIQUER 70C S/Bery Tequila Rose Strawberry Crea
        "p61": 2,       # 115 MALFY Con Ara Blood Orange GIN Malfy Con Arancia 0.7 ltr
        "p62": 2,       # 116 MALFY GIN ROSA 70cl GrapfruitE Malfy Rosa 0.7 ltr
        "p63": 2,       # 117 Drumshanb GUNPODER GIN         Drumshanbo Gunpowder 0.7 ltr
        "p119": 4,      # 118 CH SAINT MAUR L Exelenc ROS 7  Saint Maur Rose 0.75
        "p114": 2,      # 119 CH LAGRANGE 2010 St Julien     Chateau Lagrange 0.75
        "p89": 4,       # 120 RUINART BLANC D BLANC 75 CL    Ruinart Blanc 0.75
        "p95": 8,       # 121 ZONIN PROSECCO 75CL            Zonin Prosecco 0.75
        "p104": 12,     # 122 OYSTER BAY SAUVIGNON           Oyster Bay Sauvignon Blanc 0
        "p15": 8,       # 123 BAIANTINES  (дописано от руки) Ballantines Finest 1 ltr
    },
    "silicon": {     # B3
        "p1": 50,       #   1 ABSOLUT BLUE LTR               Absolut 1 ltr
        "p10": 46,      #   2 J/W RED LABEL 1 LTR            Red Label 1 ltr
        "p11": 12,      #   3 J/W BLAK LABEL 1 LTR           Black Label 1 ltr
        "p12": 12,      #   4 JACK DANIELS LTR               Jack Daniels 1 ltr
        "p13": 12,      #   5 CHIVAS REGAL 1 LTR             Chivas Regal 12Y 1 ltr
        "p31": 15,      #   6 HEINEKEN BEER CANS 33CL        Heineken 0.33 can
        "p33": 12,      #   7 BUDWEISER BEER CAN 33/35       Budweiser 0.33 can
        "p47": 10,      #   8 CARLSBERG 50CL Can             Carlsberg 0.5 can
        "p37": 6,       #   9 RED HORSE 50CL Can             Red Horse 0.5 can
        "p38": 7,       #  10 AMSTEL LIGHT Slim Can 35       Amstel Light 0.33 can
        "p43": 5,       #  11 CORONA BEER BTL 35.5CL         Corona Extra 0.355 bottle
        "p36": 2.5,     #  12 STELLA 33CL BTLS               Stella Artois 0.33 bottle
        "p35": 4,       #  13 STELLA ARTOIS 33 CL cans       Stella Artois 0.33 can
        "p32": 3,       #  14 HEINEKEN BEER BTL 33CL         Heineken 0.33 bottle
        "p34": 3,       #  15 BUDWEISER BERR BTL 33cl        Budweiser 0.33 bottle
        "p45": 3,       #  16 SMIRNOFF ICE RED 27,5CL        Smirnoff Ice 0.275 bottle
        "p44": 3,       #  17 PERONI NASTRO AZURO BEER       Peroni Nastro Azzurro 0.33 b
        "p123": 12,     #  18 JC CHARDONNAY 75CL             Jacob Creek Chardonnay 0.75
        "p105": 12,     #  19 JC SHIRAZ CABARNET 75CL        Jacob Creek Shiraz 0.75
        "p97": 12,      #  20 CES PINOT GRIG D VEN FIO       Pinot Grigio Cesari 0.75
        "p98": 12,      #  21 Le GRAND Noir SAUV BLANC       Le Grand Noir Sauvignon Blan
        "p106": 12,     #  22 Le GRAND Noir MERLOT 75C       Le Grand Noir Merlot 0.75
        "p120": 1,      #  23 MIP Collection ROSE Provienc 7 MiP Collection Rose 0.75
        "p117": 12,     #  24 CH KSARA SUNSET ROSE 75C       Chateau Ksara Rose 0.75
        "p115": 12,     #  25 MATEUS ROSE 75CL               Mateus Rose 0.75
        "p48": 8,       #  26 BACARDI WHITE RUM LTR          Bacardi White 1 ltr
        "p49": 6,       #  27 BACARDI BLACK. 1 LTR           Bacardi Black 1 ltr
        "p50": 6,       #  28 BACARDI GOLD LTR               Bacardi Gold 1 ltr
        "p65": 8,       #  29 JOSE CUERVO GOLD LTR           Jose Cuervo Gold 1 ltr
        "p64": 8,       #  30 JOSE CUERVO SILVER Espec       Jose Cuervo Silver 1 ltr
        "p59": 4,       #  31 TANQUERAY GIN LTR              Tanqueray 1 ltr
        "p58": 4,       #  32 GORDONS PINK GIN Ltr           Gordon Pink 0.7 ltr
        "p55": 6,       #  33 GORDONS GIN LTR                Gordon's 1 ltr
        "p56": 8,       #  34 BOMBAY SAPPHIRE GIN LTR        Bombay Sapphire 1 ltr
        "p57": 4,       #  35 HENDRICKS GIN 1 LTR            Hendrick's 1 ltr
        "p51": 4,       #  36 CAPTAIN MORGAN BLK LTR         Captain Morgan Black 1 ltr
        "p52": 4,       #  37 CAPTAIN MORGAN SPICED GO       Captain Morgan Gold 1 ltr
        "p53": 3,       #  38 MALIBU WHITHE RUM LTR          Malibu 1 ltr
        "p78": 3,       #  39 BAILEYS IRISH CREAM LTR        Baileys 1 ltr
        "p79": 2,       #  40 AMARULA CREAM LTR              Amarula 1 ltr
        "p14": 7,       #  41 JAMESON IRISH WSK LTR          Jameson 1 ltr
        "p23": 4,       #  42 J&B RARE SCOTCH 1 LTR          J&B 1 ltr
        "p122": 1,      #  43 D/H CLARNET SELECT 5LTR        Drostdy Hof Claret Select 5 
        "p121": 1,      #  44 D/H PREM GRN CRU 5LTR          Drostdy Hof Premier Grand Cr
        "p54": 4,       #  45 MARTINI BIANCO 1 LTR           Martini Bianco 1 ltr
        "p5": 6,        #  46 SMIRNOFF R/L 1 LTR             Smirnoff Vodka 1 ltr
        "p2": 6,        #  47 STOLICHNAYA VODKA LTR          Stolichnaya 1 ltr
        "p3": 4,        #  48 RUSSIAN STD. PETERS L          Russian Standard 1 ltr
        "p80": 12,      #  49 JAGERMEISTER 1 LTR             Jagermeister 1 ltr
        "p8": 6,        #  50 BELVEDERE VODKA LTR            Belvedere 1 ltr
        "p7": 12,       #  51 GREY GOOSE VODKA LTR           Grey Goose 1 ltr
        "p6": 3,        #  52 BELUGA NOBLE VODKA 70CL        Beluga 0.7 ltr
        "p9": 3,        #  53 CIROK VODKA LTR                Ciroc 1 ltr
        "p4": 3,        #  54 SKYY VODKA 1 LTR               Skyy Vodka 1 ltr
        "p83": 5,       #  55 ARAK TOUMA 50/54CL             Arak Touma 0.75 ltr
        "p84": 3,       #  56 EFE Fresh Grape RAKI LTR Green Efe Raki 1 ltr
        "p17": 8,       #  57 J/W GOLD LABEL RESERV 1        Gold Label 1 ltr
        "p16": 4,       #  58 J/W DOUBLE BLACK LTR           Double Black 1 ltr
        "p21": 2,       #  59 J/W BLUE LABEL 1 LTR           Blue Label 1 ltr
        "p74": 3,       #  60 HENNESSY VS LTR                Hennessy VS 1 ltr
        "p75": 3,       #  61 HENNESSY V.S.O.P 1 LTR Pr      Hennessy VSOP 1 ltr
        "p76": 1,       #  62 HENNESSY XO LTR                Hennessy XO 1 ltr
        "p77": 2,       #  63 REMY MARTIN VSOP LTR           Remy Martin VSOP 1 ltr
        "p18": 3,       #  64 CHIVAS 18 YRS LTR              Chivas Regal 18Y 1 ltr
        "p22": 1,       #  65 ROYAL SALUTE 21 YRS LTR        Chivas Royal Salute 21Y 1 lt
        "p66": 0,       #  66 PATRON COFFE                   Patron XO Cafe 0.75 ltr
        "p67": 5,       #  67 PATRON SILVER 75CL TEQUI       Patron Silver 0.75 ltr
        "p68": 5,       #  68 PATRON ANEJO 75CL GOLD T       Patron Gold 0.75 ltr
        "p69": 3,       #  69 DON JULIO BLANCO 70/75CL       Don Julio Blanco 70/75cl
        "p70": 3,       #  70 DON JULIO REPOSADO 70/75       Don Julio Reposado 70/75cl
        "p71": 3,       #  71 DON JULIO ANEJO 70/75CL        Don Julio Anejo 70/75cl
        "p72": 1,       #  72 DON JULIO 1942 ANEJO 70        Don Julio 1942 70/75cl
        "p94": 4,       #  73 ASTI MARTINI 75CL              Martini Asti 0.75
        "p96": 4,       #  74 JC CHARDONNAY PINOT NOIR       Jacob Creek Chardonnay Pinot
        "p91": 4,       #  75 BOTTEGA VINO D POET PROS       Bottega Prosecco 0.75
        "p92": 3,       #  76 BOTTEGA ROSE Proseco POE       Bottega Rose 0.75
        "p93": 3,       #  77 BOTTEGA GOLD BRUT 75C vi       Bottega Gold 0.75
        "p88": 2,       #  78 VEUVE CLICQUOT Y/L PONSR       Veuve Clicquot 0.75
        "p85": 4,       #  79 MOET & CHANDON BRUT IMP        Moet Brut 0.75
        "p86": 2,       #  80 MOET & CHANDON ROSE 75CL       Moet Rose 0.75
        "p87": 2,       #  81 MOET ICE IMPERIAL 75cl         Moet Ice 0.75
        "p90": 1,       #  82 DOM PERIGNON M&C 75CL          Dom Perignon 0.75
        "p25": 4,       #  83 GLENDFIDICH SPL R12YRS         Glenfiddich 12Y 1 ltr
        "p26": 2,       #  84 GLENDFIDICH 15 YRS LTR         Glenfiddich 15Y 1 ltr
        "p27": 2,       #  85 GLENDFIDICH 18Y Smal Bat       Glenfiddich 18Y 0.75 ltr
        "p99": 2,       #  86 BARON RIMAPERE SAUV BLAN       Rimapere Sauvignon Blanc 0.7
        "p103": 4,      #  87 MARCHESI GAVI D GAVI 75C       Gavi Di Gavi 0.75
        "p101": 4,      #  88 LAROCHE CHABLIS ST MARTI       Louis Moreau Chablis 0.75
        "p102": 4,      #  89 L J BOURGOGNE BL Cuv D ja      Bourgogne Louis Jadot 0.75
        "p107": 4,      #  90 CASTEL CH. BARREYRES HAUT M 75 Castel Barreyres 0.75
        "p109": 6,      #  91 CH SAINT LEON BOX SUP 75       Chateau Saint Leon 0.75
        "p110": 4,      #  92 CAMPO VIEJO RESERVA RIOJ       Campo Viejo Reserva 0.75
        "p113": 4,      #  93 CAMPO VIEJO GRAN RESERVA       Campo Viejo Gran Reserva 0.7
        "p116": 6,      #  94 M MINUTY ROSE PROVENCE         Minuty Cotes De Provence 0.7
        "p118": 4,      #  95 Cav D ESCLN WHISPERING         Whispering Angel 0.75
        "p19": 1,       #  96 JACK DANIELS HONEY LTR         Jack Daniels Honey 1 ltr
        "p46": 2,       #  97 BACCARDI BREEZER W/MELON       Bacardi Breezer Melon 0.275 
        "p41": 2,       #  98 ASAHI BEER BTLS SUPER DR       Asahi Super Dry 0.33 bottle
        "p81": 2,       #  99 APEROLE Aperitivo LTR          Aperol 1 ltr
        "p24": 1,       # 100 CHIVAS 25 YRS                  Chivas Regal 25Y 0.7 ltr
        "p73": 1,       # 101 CLASE AZUL Reposado 70/7       Clase Azul Reposado 70/75cl
        "p108": 2,      # 102 MS CH PERRON LALANDE D POMEROL Chateau Perron 0.75
        "p112": 2,      # 103 LA CELIA RESERVA MALBEC 75CL   La Celia Malbec 0.75
        "p100": 2,      # 104 CALVET SANCERRE Les Hautes     Calvet Sancerre 0.75
        "p111": 2,      # 105 CHATEAU des LAURETS Saint Emil Chateau Des Laurets 0.75
        "p39": 1,       # 106 GUINNESS BEER CANS 44cl        Guinness 0.44 can
        "p40": 6,       # 107 XXL VODKA MIX ENERGY CAN       XXL Vodka 0.25 can
        "p60": 1,       # 108 MONKEY 47 DRY GIN 50CL         Monkey 47 0.5 ltr
        "p20": 1,       # 109 GENTLEMAN JACK 1 LTR JD        Gentleman Jack 1 ltr
        "p28": 1,       # 110 MACALLAN 12 YR FIN TRIP        Macallan 12Y 0.7 ltr
        "p29": 1,       # 111 MACALLAN 15 YRS Double Ca      Macallan 15Y 0.7 ltr
        "p30": 1,       # 112 MACALLAN 18 YRS                Macallan 18Y 0.75 ltr
        "p42": 3,       # 113 HOEGARDEN BLANCHE 33CL B       Hoegaarden 0.33 bottle
        "p82": 0,       # 114 TEQUILA ROSE LIQUER 70C S/Bery Tequila Rose Strawberry Crea
        "p61": 0,       # 115 MALFY Con Ara Blood Orange GIN Malfy Con Arancia 0.7 ltr
        "p62": 0,       # 116 MALFY GIN ROSA 70cl GrapfruitE Malfy Rosa 0.7 ltr
        "p63": 0,       # 117 Drumshanb GUNPODER GIN         Drumshanbo Gunpowder 0.7 ltr
        "p119": 1,      # 118 CH SAINT MAUR L Exelenc ROS 7  Saint Maur Rose 0.75
        "p114": 2,      # 119 CH LAGRANGE 2010 St Julien     Chateau Lagrange 0.75
        "p89": 3,       # 120 RUINART BLANC D BLANC 75 CL    Ruinart Blanc 0.75
        "p95": 3,       # 121 ZONIN PROSECCO 75CL            Zonin Prosecco 0.75
        "p104": 8,      # 122 OYSTER BAY SAUVIGNON           Oyster Bay Sauvignon Blanc 0
        "p15": 6,       # 123 BAIANTINES  (дописано от руки) Ballantines Finest 1 ltr
    },
    "alguses": {     # B4
        "p1": 50,       #   1 ABSOLUT BLUE LTR               Absolut 1 ltr
        "p10": 46,      #   2 J/W RED LABEL 1 LTR            Red Label 1 ltr
        "p11": 12,      #   3 J/W BLAK LABEL 1 LTR           Black Label 1 ltr
        "p12": 12,      #   4 JACK DANIELS LTR               Jack Daniels 1 ltr
        "p13": 12,      #   5 CHIVAS REGAL 1 LTR             Chivas Regal 12Y 1 ltr
        "p31": 15,      #   6 HEINEKEN BEER CANS 33CL        Heineken 0.33 can
        "p33": 12,      #   7 BUDWEISER BEER CAN 33/35       Budweiser 0.33 can
        "p47": 10,      #   8 CARLSBERG 50CL Can             Carlsberg 0.5 can
        "p37": 6,       #   9 RED HORSE 50CL Can             Red Horse 0.5 can
        "p38": 3,       #  10 AMSTEL LIGHT Slim Can 35       Amstel Light 0.33 can
        "p43": 5,       #  11 CORONA BEER BTL 35.5CL         Corona Extra 0.355 bottle
        "p36": 2.5,     #  12 STELLA 33CL BTLS               Stella Artois 0.33 bottle
        "p35": 3,       #  13 STELLA ARTOIS 33 CL cans       Stella Artois 0.33 can
        "p32": 3,       #  14 HEINEKEN BEER BTL 33CL         Heineken 0.33 bottle
        "p34": 3,       #  15 BUDWEISER BERR BTL 33cl        Budweiser 0.33 bottle
        "p45": 3,       #  16 SMIRNOFF ICE RED 27,5CL        Smirnoff Ice 0.275 bottle
        "p44": 3,       #  17 PERONI NASTRO AZURO BEER       Peroni Nastro Azzurro 0.33 b
        "p123": 12,     #  18 JC CHARDONNAY 75CL             Jacob Creek Chardonnay 0.75
        "p105": 12,     #  19 JC SHIRAZ CABARNET 75CL        Jacob Creek Shiraz 0.75
        "p97": 12,      #  20 CES PINOT GRIG D VEN FIO       Pinot Grigio Cesari 0.75
        "p98": 12,      #  21 Le GRAND Noir SAUV BLANC       Le Grand Noir Sauvignon Blan
        "p106": 12,     #  22 Le GRAND Noir MERLOT 75C       Le Grand Noir Merlot 0.75
        "p120": 1,      #  23 MIP Collection ROSE Provienc 7 MiP Collection Rose 0.75
        "p117": 12,     #  24 CH KSARA SUNSET ROSE 75C       Chateau Ksara Rose 0.75
        "p115": 12,     #  25 MATEUS ROSE 75CL               Mateus Rose 0.75
        "p48": 8,       #  26 BACARDI WHITE RUM LTR          Bacardi White 1 ltr
        "p49": 6,       #  27 BACARDI BLACK. 1 LTR           Bacardi Black 1 ltr
        "p50": 6,       #  28 BACARDI GOLD LTR               Bacardi Gold 1 ltr
        "p65": 8,       #  29 JOSE CUERVO GOLD LTR           Jose Cuervo Gold 1 ltr
        "p64": 8,       #  30 JOSE CUERVO SILVER Espec       Jose Cuervo Silver 1 ltr
        "p59": 4,       #  31 TANQUERAY GIN LTR              Tanqueray 1 ltr
        "p58": 4,       #  32 GORDONS PINK GIN Ltr           Gordon Pink 0.7 ltr
        "p55": 6,       #  33 GORDONS GIN LTR                Gordon's 1 ltr
        "p56": 8,       #  34 BOMBAY SAPPHIRE GIN LTR        Bombay Sapphire 1 ltr
        "p57": 4,       #  35 HENDRICKS GIN 1 LTR            Hendrick's 1 ltr
        "p51": 4,       #  36 CAPTAIN MORGAN BLK LTR         Captain Morgan Black 1 ltr
        "p52": 4,       #  37 CAPTAIN MORGAN SPICED GO       Captain Morgan Gold 1 ltr
        "p53": 3,       #  38 MALIBU WHITHE RUM LTR          Malibu 1 ltr
        "p78": 3,       #  39 BAILEYS IRISH CREAM LTR        Baileys 1 ltr
        "p79": 2,       #  40 AMARULA CREAM LTR              Amarula 1 ltr
        "p14": 7,       #  41 JAMESON IRISH WSK LTR          Jameson 1 ltr
        "p23": 4,       #  42 J&B RARE SCOTCH 1 LTR          J&B 1 ltr
        "p122": 1,      #  43 D/H CLARNET SELECT 5LTR        Drostdy Hof Claret Select 5 
        "p121": 1,      #  44 D/H PREM GRN CRU 5LTR          Drostdy Hof Premier Grand Cr
        "p54": 4,       #  45 MARTINI BIANCO 1 LTR           Martini Bianco 1 ltr
        "p5": 6,        #  46 SMIRNOFF R/L 1 LTR             Smirnoff Vodka 1 ltr
        "p2": 6,        #  47 STOLICHNAYA VODKA LTR          Stolichnaya 1 ltr
        "p3": 4,        #  48 RUSSIAN STD. PETERS L          Russian Standard 1 ltr
        "p80": 4,       #  49 JAGERMEISTER 1 LTR             Jagermeister 1 ltr
        "p8": 6,        #  50 BELVEDERE VODKA LTR            Belvedere 1 ltr
        "p7": 12,       #  51 GREY GOOSE VODKA LTR           Grey Goose 1 ltr
        "p6": 3,        #  52 BELUGA NOBLE VODKA 70CL        Beluga 0.7 ltr
        "p9": 3,        #  53 CIROK VODKA LTR                Ciroc 1 ltr
        "p4": 3,        #  54 SKYY VODKA 1 LTR               Skyy Vodka 1 ltr
        "p83": 5,       #  55 ARAK TOUMA 50/54CL             Arak Touma 0.75 ltr
        "p84": 3,       #  56 EFE Fresh Grape RAKI LTR Green Efe Raki 1 ltr
        "p17": 6,       #  57 J/W GOLD LABEL RESERV 1        Gold Label 1 ltr
        "p16": 6,       #  58 J/W DOUBLE BLACK LTR           Double Black 1 ltr
        "p21": 2,       #  59 J/W BLUE LABEL 1 LTR           Blue Label 1 ltr
        "p74": 3,       #  60 HENNESSY VS LTR                Hennessy VS 1 ltr
        "p75": 3,       #  61 HENNESSY V.S.O.P 1 LTR Pr      Hennessy VSOP 1 ltr
        "p76": 1,       #  62 HENNESSY XO LTR                Hennessy XO 1 ltr
        "p77": 2,       #  63 REMY MARTIN VSOP LTR           Remy Martin VSOP 1 ltr
        "p18": 3,       #  64 CHIVAS 18 YRS LTR              Chivas Regal 18Y 1 ltr
        "p22": 1,       #  65 ROYAL SALUTE 21 YRS LTR        Chivas Royal Salute 21Y 1 lt
        "p66": 0,       #  66 PATRON COFFE                   Patron XO Cafe 0.75 ltr
        "p67": 5,       #  67 PATRON SILVER 75CL TEQUI       Patron Silver 0.75 ltr
        "p68": 5,       #  68 PATRON ANEJO 75CL GOLD T       Patron Gold 0.75 ltr
        "p69": 3,       #  69 DON JULIO BLANCO 70/75CL       Don Julio Blanco 70/75cl
        "p70": 3,       #  70 DON JULIO REPOSADO 70/75       Don Julio Reposado 70/75cl
        "p71": 3,       #  71 DON JULIO ANEJO 70/75CL        Don Julio Anejo 70/75cl
        "p72": 1,       #  72 DON JULIO 1942 ANEJO 70        Don Julio 1942 70/75cl
        "p94": 4,       #  73 ASTI MARTINI 75CL              Martini Asti 0.75
        "p96": 4,       #  74 JC CHARDONNAY PINOT NOIR       Jacob Creek Chardonnay Pinot
        "p91": 4,       #  75 BOTTEGA VINO D POET PROS       Bottega Prosecco 0.75
        "p92": 3,       #  76 BOTTEGA ROSE Proseco POE       Bottega Rose 0.75
        "p93": 3,       #  77 BOTTEGA GOLD BRUT 75C vi       Bottega Gold 0.75
        "p88": 2,       #  78 VEUVE CLICQUOT Y/L PONSR       Veuve Clicquot 0.75
        "p85": 4,       #  79 MOET & CHANDON BRUT IMP        Moet Brut 0.75
        "p86": 2,       #  80 MOET & CHANDON ROSE 75CL       Moet Rose 0.75
        "p87": 2,       #  81 MOET ICE IMPERIAL 75cl         Moet Ice 0.75
        "p90": 1,       #  82 DOM PERIGNON M&C 75CL          Dom Perignon 0.75
        "p25": 4,       #  83 GLENDFIDICH SPL R12YRS         Glenfiddich 12Y 1 ltr
        "p26": 2,       #  84 GLENDFIDICH 15 YRS LTR         Glenfiddich 15Y 1 ltr
        "p27": 2,       #  85 GLENDFIDICH 18Y Smal Bat       Glenfiddich 18Y 0.75 ltr
        "p99": 2,       #  86 BARON RIMAPERE SAUV BLAN       Rimapere Sauvignon Blanc 0.7
        "p103": 4,      #  87 MARCHESI GAVI D GAVI 75C       Gavi Di Gavi 0.75
        "p101": 4,      #  88 LAROCHE CHABLIS ST MARTI       Louis Moreau Chablis 0.75
        "p102": 4,      #  89 L J BOURGOGNE BL Cuv D ja      Bourgogne Louis Jadot 0.75
        "p107": 4,      #  90 CASTEL CH. BARREYRES HAUT M 75 Castel Barreyres 0.75
        "p109": 6,      #  91 CH SAINT LEON BOX SUP 75       Chateau Saint Leon 0.75
        "p110": 4,      #  92 CAMPO VIEJO RESERVA RIOJ       Campo Viejo Reserva 0.75
        "p113": 4,      #  93 CAMPO VIEJO GRAN RESERVA       Campo Viejo Gran Reserva 0.7
        "p116": 6,      #  94 M MINUTY ROSE PROVENCE         Minuty Cotes De Provence 0.7
        "p118": 4,      #  95 Cav D ESCLN WHISPERING         Whispering Angel 0.75
        "p19": 1,       #  96 JACK DANIELS HONEY LTR         Jack Daniels Honey 1 ltr
        "p46": 2,       #  97 BACCARDI BREEZER W/MELON       Bacardi Breezer Melon 0.275 
        "p41": 2,       #  98 ASAHI BEER BTLS SUPER DR       Asahi Super Dry 0.33 bottle
        "p81": 2,       #  99 APEROLE Aperitivo LTR          Aperol 1 ltr
        "p24": 1,       # 100 CHIVAS 25 YRS                  Chivas Regal 25Y 0.7 ltr
        "p73": 1,       # 101 CLASE AZUL Reposado 70/7       Clase Azul Reposado 70/75cl
        "p108": 2,      # 102 MS CH PERRON LALANDE D POMEROL Chateau Perron 0.75
        "p112": 2,      # 103 LA CELIA RESERVA MALBEC 75CL   La Celia Malbec 0.75
        "p100": 2,      # 104 CALVET SANCERRE Les Hautes     Calvet Sancerre 0.75
        "p111": 2,      # 105 CHATEAU des LAURETS Saint Emil Chateau Des Laurets 0.75
        "p39": 1,       # 106 GUINNESS BEER CANS 44cl        Guinness 0.44 can
        "p40": 6,       # 107 XXL VODKA MIX ENERGY CAN       XXL Vodka 0.25 can
        "p60": 1,       # 108 MONKEY 47 DRY GIN 50CL         Monkey 47 0.5 ltr
        "p20": 1,       # 109 GENTLEMAN JACK 1 LTR JD        Gentleman Jack 1 ltr
        "p28": 1,       # 110 MACALLAN 12 YR FIN TRIP        Macallan 12Y 0.7 ltr
        "p29": 1,       # 111 MACALLAN 15 YRS Double Ca      Macallan 15Y 0.7 ltr
        "p30": 1,       # 112 MACALLAN 18 YRS                Macallan 18Y 0.75 ltr
        "p42": 3,       # 113 HOEGARDEN BLANCHE 33CL B       Hoegaarden 0.33 bottle
        "p82": 0,       # 114 TEQUILA ROSE LIQUER 70C S/Bery Tequila Rose Strawberry Crea
        "p61": 0,       # 115 MALFY Con Ara Blood Orange GIN Malfy Con Arancia 0.7 ltr
        "p62": 0,       # 116 MALFY GIN ROSA 70cl GrapfruitE Malfy Rosa 0.7 ltr
        "p63": 0,       # 117 Drumshanb GUNPODER GIN         Drumshanbo Gunpowder 0.7 ltr
        "p119": 1,      # 118 CH SAINT MAUR L Exelenc ROS 7  Saint Maur Rose 0.75
        "p114": 2,      # 119 CH LAGRANGE 2010 St Julien     Chateau Lagrange 0.75
        "p89": 3,       # 120 RUINART BLANC D BLANC 75 CL    Ruinart Blanc 0.75
        "p95": 3,       # 121 ZONIN PROSECCO 75CL            Zonin Prosecco 0.75
        "p104": 12,     # 122 OYSTER BAY SAUVIGNON           Oyster Bay Sauvignon Blanc 0
        "p15": 6,       # 123 BAIANTINES  (дописано от руки) Ballantines Finest 1 ltr
    },
    "tecom": {     # B5
        "p1": 30,       #   1 ABSOLUT BLUE LTR               Absolut 1 ltr
        "p10": 30,      #   2 J/W RED LABEL 1 LTR            Red Label 1 ltr
        "p11": 8,       #   3 J/W BLAK LABEL 1 LTR           Black Label 1 ltr
        "p12": 8,       #   4 JACK DANIELS LTR               Jack Daniels 1 ltr
        "p13": 8,       #   5 CHIVAS REGAL 1 LTR             Chivas Regal 12Y 1 ltr
        "p31": 8,       #   6 HEINEKEN BEER CANS 33CL        Heineken 0.33 can
        "p33": 8,       #   7 BUDWEISER BEER CAN 33/35       Budweiser 0.33 can
        "p47": 8,       #   8 CARLSBERG 50CL Can             Carlsberg 0.5 can
        "p37": 0,       #   9 RED HORSE 50CL Can             Red Horse 0.5 can
        "p38": 0,       #  10 AMSTEL LIGHT Slim Can 35       Amstel Light 0.33 can
        "p43": 7,       #  11 CORONA BEER BTL 35.5CL         Corona Extra 0.355 bottle
        "p36": 0,       #  12 STELLA 33CL BTLS               Stella Artois 0.33 bottle
        "p35": 0,       #  13 STELLA ARTOIS 33 CL cans       Stella Artois 0.33 can
        "p32": 0,       #  14 HEINEKEN BEER BTL 33CL         Heineken 0.33 bottle
        "p34": 0,       #  15 BUDWEISER BERR BTL 33cl        Budweiser 0.33 bottle
        "p45": 0,       #  16 SMIRNOFF ICE RED 27,5CL        Smirnoff Ice 0.275 bottle
        "p44": 0,       #  17 PERONI NASTRO AZURO BEER       Peroni Nastro Azzurro 0.33 b
        "p123": 12,     #  18 JC CHARDONNAY 75CL             Jacob Creek Chardonnay 0.75
        "p105": 12,     #  19 JC SHIRAZ CABARNET 75CL        Jacob Creek Shiraz 0.75
        "p97": 12,      #  20 CES PINOT GRIG D VEN FIO       Pinot Grigio Cesari 0.75
        "p98": 12,      #  21 Le GRAND Noir SAUV BLANC       Le Grand Noir Sauvignon Blan
        "p106": 12,     #  22 Le GRAND Noir MERLOT 75C       Le Grand Noir Merlot 0.75
        "p120": 4,      #  23 MIP Collection ROSE Provienc 7 MiP Collection Rose 0.75
        "p117": 4,      #  24 CH KSARA SUNSET ROSE 75C       Chateau Ksara Rose 0.75
        "p115": 7,      #  25 MATEUS ROSE 75CL               Mateus Rose 0.75
        "p48": 4,       #  26 BACARDI WHITE RUM LTR          Bacardi White 1 ltr
        "p49": 4,       #  27 BACARDI BLACK. 1 LTR           Bacardi Black 1 ltr
        "p50": 4,       #  28 BACARDI GOLD LTR               Bacardi Gold 1 ltr
        "p65": 7,       #  29 JOSE CUERVO GOLD LTR           Jose Cuervo Gold 1 ltr
        "p64": 7,       #  30 JOSE CUERVO SILVER Espec       Jose Cuervo Silver 1 ltr
        "p59": 4,       #  31 TANQUERAY GIN LTR              Tanqueray 1 ltr
        "p58": 4,       #  32 GORDONS PINK GIN Ltr           Gordon Pink 0.7 ltr
        "p55": 7,       #  33 GORDONS GIN LTR                Gordon's 1 ltr
        "p56": 7,       #  34 BOMBAY SAPPHIRE GIN LTR        Bombay Sapphire 1 ltr
        "p57": 7,       #  35 HENDRICKS GIN 1 LTR            Hendrick's 1 ltr
        "p51": 4,       #  36 CAPTAIN MORGAN BLK LTR         Captain Morgan Black 1 ltr
        "p52": 4,       #  37 CAPTAIN MORGAN SPICED GO       Captain Morgan Gold 1 ltr
        "p53": 2,       #  38 MALIBU WHITHE RUM LTR          Malibu 1 ltr
        "p78": 2,       #  39 BAILEYS IRISH CREAM LTR        Baileys 1 ltr
        "p79": 1,       #  40 AMARULA CREAM LTR              Amarula 1 ltr
        "p14": 4,       #  41 JAMESON IRISH WSK LTR          Jameson 1 ltr
        "p23": 4,       #  42 J&B RARE SCOTCH 1 LTR          J&B 1 ltr
        "p122": 0,      #  43 D/H CLARNET SELECT 5LTR        Drostdy Hof Claret Select 5 
        "p121": 0,      #  44 D/H PREM GRN CRU 5LTR          Drostdy Hof Premier Grand Cr
        "p54": 2,       #  45 MARTINI BIANCO 1 LTR           Martini Bianco 1 ltr
        "p5": 4,        #  46 SMIRNOFF R/L 1 LTR             Smirnoff Vodka 1 ltr
        "p2": 4,        #  47 STOLICHNAYA VODKA LTR          Stolichnaya 1 ltr
        "p3": 4,        #  48 RUSSIAN STD. PETERS L          Russian Standard 1 ltr
        "p80": 4,       #  49 JAGERMEISTER 1 LTR             Jagermeister 1 ltr
        "p8": 4,        #  50 BELVEDERE VODKA LTR            Belvedere 1 ltr
        "p7": 12,       #  51 GREY GOOSE VODKA LTR           Grey Goose 1 ltr
        "p6": 4,        #  52 BELUGA NOBLE VODKA 70CL        Beluga 0.7 ltr
        "p9": 2,        #  53 CIROK VODKA LTR                Ciroc 1 ltr
        "p4": 2,        #  54 SKYY VODKA 1 LTR               Skyy Vodka 1 ltr
        "p83": 1,       #  55 ARAK TOUMA 50/54CL             Arak Touma 0.75 ltr
        "p84": 1,       #  56 EFE Fresh Grape RAKI LTR Green Efe Raki 1 ltr
        "p17": 4,       #  57 J/W GOLD LABEL RESERV 1        Gold Label 1 ltr
        "p16": 1,       #  58 J/W DOUBLE BLACK LTR           Double Black 1 ltr
        "p21": 2,       #  59 J/W BLUE LABEL 1 LTR           Blue Label 1 ltr
        "p74": 2,       #  60 HENNESSY VS LTR                Hennessy VS 1 ltr
        "p75": 2,       #  61 HENNESSY V.S.O.P 1 LTR Pr      Hennessy VSOP 1 ltr
        "p76": 1,       #  62 HENNESSY XO LTR                Hennessy XO 1 ltr
        "p77": 1,       #  63 REMY MARTIN VSOP LTR           Remy Martin VSOP 1 ltr
        "p18": 6,       #  64 CHIVAS 18 YRS LTR              Chivas Regal 18Y 1 ltr
        "p22": 1,       #  65 ROYAL SALUTE 21 YRS LTR        Chivas Royal Salute 21Y 1 lt
        "p66": 0,       #  66 PATRON COFFE                   Patron XO Cafe 0.75 ltr
        "p67": 4,       #  67 PATRON SILVER 75CL TEQUI       Patron Silver 0.75 ltr
        "p68": 4,       #  68 PATRON ANEJO 75CL GOLD T       Patron Gold 0.75 ltr
        "p69": 4,       #  69 DON JULIO BLANCO 70/75CL       Don Julio Blanco 70/75cl
        "p70": 4,       #  70 DON JULIO REPOSADO 70/75       Don Julio Reposado 70/75cl
        "p71": 3,       #  71 DON JULIO ANEJO 70/75CL        Don Julio Anejo 70/75cl
        "p72": 2,       #  72 DON JULIO 1942 ANEJO 70        Don Julio 1942 70/75cl
        "p94": 6,       #  73 ASTI MARTINI 75CL              Martini Asti 0.75
        "p96": 6,       #  74 JC CHARDONNAY PINOT NOIR       Jacob Creek Chardonnay Pinot
        "p91": 5,       #  75 BOTTEGA VINO D POET PROS       Bottega Prosecco 0.75
        "p92": 5,       #  76 BOTTEGA ROSE Proseco POE       Bottega Rose 0.75
        "p93": 6,       #  77 BOTTEGA GOLD BRUT 75C vi       Bottega Gold 0.75
        "p88": 3,       #  78 VEUVE CLICQUOT Y/L PONSR       Veuve Clicquot 0.75
        "p85": 15,      #  79 MOET & CHANDON BRUT IMP        Moet Brut 0.75
        "p86": 5,       #  80 MOET & CHANDON ROSE 75CL       Moet Rose 0.75
        "p87": 3,       #  81 MOET ICE IMPERIAL 75cl         Moet Ice 0.75
        "p90": 2,       #  82 DOM PERIGNON M&C 75CL          Dom Perignon 0.75
        "p25": 3,       #  83 GLENDFIDICH SPL R12YRS         Glenfiddich 12Y 1 ltr
        "p26": 3,       #  84 GLENDFIDICH 15 YRS LTR         Glenfiddich 15Y 1 ltr
        "p27": 3,       #  85 GLENDFIDICH 18Y Smal Bat       Glenfiddich 18Y 0.75 ltr
        "p99": 4,       #  86 BARON RIMAPERE SAUV BLAN       Rimapere Sauvignon Blanc 0.7
        "p103": 4,      #  87 MARCHESI GAVI D GAVI 75C       Gavi Di Gavi 0.75
        "p101": 4,      #  88 LAROCHE CHABLIS ST MARTI       Louis Moreau Chablis 0.75
        "p102": 4,      #  89 L J BOURGOGNE BL Cuv D ja      Bourgogne Louis Jadot 0.75
        "p107": 2,      #  90 CASTEL CH. BARREYRES HAUT M 75 Castel Barreyres 0.75
        "p109": 6,      #  91 CH SAINT LEON BOX SUP 75       Chateau Saint Leon 0.75
        "p110": 6,      #  92 CAMPO VIEJO RESERVA RIOJ       Campo Viejo Reserva 0.75
        "p113": 6,      #  93 CAMPO VIEJO GRAN RESERVA       Campo Viejo Gran Reserva 0.7
        "p116": 4,      #  94 M MINUTY ROSE PROVENCE         Minuty Cotes De Provence 0.7
        "p118": 3,      #  95 Cav D ESCLN WHISPERING         Whispering Angel 0.75
        "p19": 2,       #  96 JACK DANIELS HONEY LTR         Jack Daniels Honey 1 ltr
        "p46": 0,       #  97 BACCARDI BREEZER W/MELON       Bacardi Breezer Melon 0.275 
        "p41": 0,       #  98 ASAHI BEER BTLS SUPER DR       Asahi Super Dry 0.33 bottle
        "p81": 2,       #  99 APEROLE Aperitivo LTR          Aperol 1 ltr
        "p24": 2,       # 100 CHIVAS 25 YRS                  Chivas Regal 25Y 0.7 ltr
        "p73": 3,       # 101 CLASE AZUL Reposado 70/7       Clase Azul Reposado 70/75cl
        "p108": 0,      # 102 MS CH PERRON LALANDE D POMEROL Chateau Perron 0.75
        "p112": 0,      # 103 LA CELIA RESERVA MALBEC 75CL   La Celia Malbec 0.75
        "p100": 0,      # 104 CALVET SANCERRE Les Hautes     Calvet Sancerre 0.75
        "p111": 0,      # 105 CHATEAU des LAURETS Saint Emil Chateau Des Laurets 0.75
        "p39": 0,       # 106 GUINNESS BEER CANS 44cl        Guinness 0.44 can
        "p40": 0,       # 107 XXL VODKA MIX ENERGY CAN       XXL Vodka 0.25 can
        "p60": 1,       # 108 MONKEY 47 DRY GIN 50CL         Monkey 47 0.5 ltr
        "p20": 0,       # 109 GENTLEMAN JACK 1 LTR JD        Gentleman Jack 1 ltr
        "p28": 2,       # 110 MACALLAN 12 YR FIN TRIP        Macallan 12Y 0.7 ltr
        "p29": 2,       # 111 MACALLAN 15 YRS Double Ca      Macallan 15Y 0.7 ltr
        "p30": 1,       # 112 MACALLAN 18 YRS                Macallan 18Y 0.75 ltr
        "p42": 0,       # 113 HOEGARDEN BLANCHE 33CL B       Hoegaarden 0.33 bottle
        "p82": 0,       # 114 TEQUILA ROSE LIQUER 70C S/Bery Tequila Rose Strawberry Crea
        "p61": 1,       # 115 MALFY Con Ara Blood Orange GIN Malfy Con Arancia 0.7 ltr
        "p62": 1,       # 116 MALFY GIN ROSA 70cl GrapfruitE Malfy Rosa 0.7 ltr
        "p63": 0,       # 117 Drumshanb GUNPODER GIN         Drumshanbo Gunpowder 0.7 ltr
        "p119": 0,      # 118 CH SAINT MAUR L Exelenc ROS 7  Saint Maur Rose 0.75
        "p114": 3,      # 119 CH LAGRANGE 2010 St Julien     Chateau Lagrange 0.75
        "p89": 4,       # 120 RUINART BLANC D BLANC 75 CL    Ruinart Blanc 0.75
        "p95": 6,       # 121 ZONIN PROSECCO 75CL            Zonin Prosecco 0.75
        "p104": 6,      # 122 OYSTER BAY SAUVIGNON           Oyster Bay Sauvignon Blanc 0
        "p15": 4,       # 123 BAIANTINES  (дописано от руки) Ballantines Finest 1 ltr
    },
}


async def plan(db, sr) -> dict:
    """Что изменится: старая норма → новая, по клеткам."""
    cat = sr._catalog()
    old = await db.get_stock_norms()
    rows, same = [], 0
    for oid, per in SHEET.items():
        for pid, new in per.items():
            if pid not in cat:
                rows.append(("НЕТ В КАТАЛОГЕ", oid, pid, None, new)); continue
            cur = old.get(f"{oid}:{pid}")
            if cur is not None and abs(float(cur) - float(new)) < 1e-9:
                same += 1
            else:
                rows.append(("правка", oid, pid, cur, new))
    return {"rows": rows, "same": same, "old": old,
            "cells": sum(len(p) for p in SHEET.values())}


async def run(db, sr, apply: bool, backup_dir: str = "/root", say=print) -> dict:
    p = await plan(db, sr)
    bad = [r for r in p["rows"] if r[0] == "НЕТ В КАТАЛОГЕ"]
    if bad:
        say("СТОП: позиций нет в каталоге: " + ", ".join(r[2] for r in bad))
        return {"ok": False, "stop": "catalog"}
    up = [r for r in p["rows"] if r[3] is not None and float(r[4]) > float(r[3])]
    dn = [r for r in p["rows"] if r[3] is not None and float(r[4]) < float(r[3])]
    new = [r for r in p["rows"] if r[3] is None]
    say(f"клеток на листе {p['cells']}; совпало с нынешними {p['same']}, "
        f"выросло {len(up)}, упало {len(dn)}, не было нормы {len(new)}")
    d = sum(float(r[4]) - float(r[3] or 0) for r in p["rows"])
    say(f"в штуках итого {d:+.0f}")
    if not apply:
        say("пробный прогон: ничего не записано (--apply — записать)")
        return {"ok": True, "dry": True, "changes": len(p["rows"])}

    rule = await db.stock_norm_rule_get()
    path = f"{backup_dir}/norms_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump({"norms": p["old"], "rule": rule}, f, ensure_ascii=False, default=str)
    say(f"копия прежних норм: {path}")

    n = 0
    for oid, per in SHEET.items():
        for pid, v in per.items():
            await db.set_stock_norm(oid, pid, v, 0)
            n += 1
    await db.stock_norm_rule_set({"kind": "sheet", "day": DAY, "note": NOTE,
                                  "at": datetime.now(timezone.utc).isoformat(), "by": 0})
    sr.base_drop()
    after = await db.get_stock_norms()
    miss = [f"{o}:{p}" for o, per in SHEET.items() for p, v in per.items()
            if abs(float(after.get(f"{o}:{p}", -1)) - float(v)) > 1e-9]
    if miss:
        say(f"ЗАПИСАЛОСЬ НЕ ВСЁ ({len(miss)}) — откатываю")
        await rollback(db, sr, path, say)
        return {"ok": False, "miss": miss, "backup": path}
    say(f"записано клеток: {n}; правило норм: с листа операторов {DAY}")
    return {"ok": True, "backup": path, "cells": n}


async def rollback(db, sr, path: str, say=print) -> None:
    data = json.load(open(path))
    for key, v in (data.get("norms") or {}).items():
        oid, _, pid = key.partition(":")
        await db.set_stock_norm(oid, pid, v, 0)
    rule = data.get("rule") or {}
    if rule:
        await db.stock_norm_rule_set({k: v for k, v in rule.items() if k != "_id"})
    sr.base_drop()
    say(f"откат: вернул {len(data.get('norms') or {})} норм и прежнее правило")


async def main():
    import db, stock_routes as sr
    await db.connect()
    if "--rollback" in sys.argv:
        await rollback(db, sr, sys.argv[sys.argv.index("--rollback") + 1])
        return
    res = await run(db, sr, apply="--apply" in sys.argv)
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    asyncio.run(main())
