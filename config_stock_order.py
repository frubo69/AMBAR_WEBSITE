"""
AMBAR — порядок позиций в пересчёте склада.

Тот же порядок, в котором позиции идут в рабочей таблице «отчёт-заявка», и он
одинаков на всех листах — B1, B2, B3. Это порядок обхода полок, а не алфавит:
рядом стоит то, что рядом лежит, поэтому считать по нему быстрее и труднее
пропустить позицию.

Действует ТОЛЬКО в пересчёте склада. Каталог, витрина и POS сортируются
по-своему и этого файла не видят.

Позиции каталога, которых в таблице нет, идут в конец — потерять их нельзя.
Если в каталог добавится товар, он тоже окажется в конце, пока его не впишут
сюда руками.

Сверено с «пример отчета-заявка амбар.xlsx» (лист B1 JVC, 122 строки):
все 122 строки на месте, номера здесь совпадают с номерами на листе. Сверх
листа в каталоге только Ballantines Finest — его на бумаге нет, поэтому он
стоит последним.

Строки 18 и 74 — обе Jacob's Creek Chardonnay, тихое и игристое. Позиция p96
называлась просто «Chardonnay» и по имени села в строку 18, хотя на её
фотографии игристое с пино нуар, то есть строка 74. Тихого вина в каталоге не
было вовсе; теперь оно заведено (p123) и стоит в своей строке.
"""

STOCK_ORDER = [
    "p1",     #   1 Absolut 1 ltr  # ABSOLUT BLUE LTR
    "p10",    #   2 Red Label 1 ltr  # J/W RED LABEL  1 LTR
    "p11",    #   3 Black Label 1 ltr  # J/W BLAK LABEL 1 LTR
    "p12",    #   4 Jack Daniels 1 ltr  # JACK DANIELS LTR
    "p13",    #   5 Chivas Regal 12Y 1 ltr  # CHIVAS REGAL 1 LTR
    "p31",    #   6 Heineken 0.33 can  # HEINEKEN BEER CANS 33CL
    "p33",    #   7 Budweiser 0.33 can  # BUDWEISER BEER CAN 33/35
    "p47",    #   8 Carlsberg 0.5 can  # CARLSBERG 50CL Can
    "p37",    #   9 Red Horse 0.5 can  # RED HORSE 50CL Can
    "p38",    #  10 Amstel Light 0.33 can  # AMSTEL LIGHT Slim Can 35
    "p43",    #  11 Corona Extra 0.355 bottle  # CORONA BEER BTL 35.5CL
    "p36",    #  12 Stella Artois 0.33 bottle  # STELLA 33CL BTLS
    "p35",    #  13 Stella Artois 0.33 can  # STELLA ARTOIS 33 CL cans
    "p32",    #  14 Heineken 0.33 bottle  # HEINEKEN BEER BTL 33CL
    "p34",    #  15 Budweiser 0.33 bottle  # BUDWEISER BERR BTL 33cl
    "p45",    #  16 Smirnoff Ice 0.275 bottle  # SMIRNOFF ICE RED 27,5CL
    "p44",    #  17 Peroni Nastro Azzurro 0.33 bottle  # PERONI NASTRO AZURO BEER
    "p123",   #  18 Jacob Creek Chardonnay 0.75  # JC CHARDONNAY 75CL
    "p105",   #  19 Jacob Creek Shiraz 0.75  # JC SHIRAZ CABARNET 75CL
    "p97",    #  20 Pinot Grigio Cesari 0.75  # CES PINOT GRIG D VEN FIO
    "p98",    #  21 Le Grand Noir Sauvignon Blanc 0.75  # Le GRAND Noir SAUV BLANC
    "p106",   #  22 Le Grand Noir Merlot 0.75  # Le GRAND Noir MERLOT 75C
    "p120",   #  23 MiP Collection Rose 0.75  # MIP Collection ROSE Provienc 7
    "p117",   #  24 Chateau Ksara Rose 0.75  # CH KSARA SUNSET ROSE 75C
    "p115",   #  25 Mateus Rose 0.75  # MATEUS ROSE 75CL
    "p48",    #  26 Bacardi White 1 ltr  # BACARDI WHITE RUM LTR
    "p49",    #  27 Bacardi Black 1 ltr  # BACARDI BLACK. 1 LTR
    "p50",    #  28 Bacardi Gold 1 ltr  # BACARDI GOLD LTR
    "p65",    #  29 Jose Cuervo Gold 1 ltr  # JOSE CUERVO GOLD LTR
    "p64",    #  30 Jose Cuervo Silver 1 ltr  # JOSE CUERVO SILVER Espec
    "p59",    #  31 Tanqueray 1 ltr  # TANQUERAY GIN LTR
    "p58",    #  32 Gordon Pink 0.7 ltr  # GORDONS PINK GIN Ltr
    "p55",    #  33 Gordon's 1 ltr  # GORDONS GIN LTR
    "p56",    #  34 Bombay Sapphire 1 ltr  # BOMBAY SAPPHIRE GIN LTR
    "p57",    #  35 Hendrick's 1 ltr  # HENDRICKS GIN 1 LTR
    "p51",    #  36 Captain Morgan Black 1 ltr  # CAPTAIN MORGAN BLK LTR
    "p52",    #  37 Captain Morgan Gold 1 ltr  # CAPTAIN MORGAN SPICED GO
    "p53",    #  38 Malibu 1 ltr  # MALIBU WHITHE RUM LTR
    "p78",    #  39 Baileys 1 ltr  # BAILEYS IRISH CREAM LTR
    "p79",    #  40 Amarula 1 ltr  # AMARULA CREAM LTR
    "p14",    #  41 Jameson 1 ltr  # JAMESON IRISH WSK LTR
    "p23",    #  42 J&B 1 ltr  # J&B RARE SCOTCH 1 LTR
    "p122",   #  43 Drostdy Hof Claret Select 5 ltr  # D/H CLARNET SELECT 5LTR
    "p121",   #  44 Drostdy Hof Premier Grand Cru 5 ltr  # D/H PREM GRN CRU 5LTR
    "p54",    #  45 Martini Bianco 1 ltr
    "p5",     #  46 Smirnoff Vodka 1 ltr  # SMIRNOFF R/L 1 LTR
    "p2",     #  47 Stolichnaya 1 ltr  # STOLICHNAYA VODKA LTR
    "p3",     #  48 Russian Standard 1 ltr  # RUSSIAN STD. PETERS L
    "p80",    #  49 Jagermeister 1 ltr
    "p8",     #  50 Belvedere 1 ltr  # BELVEDERE VODKA  LTR
    "p7",     #  51 Grey Goose 1 ltr  # GREY GOOSE VODKA LTR
    "p6",     #  52 Beluga 0.7 ltr  # BELUGA  NOBLE VODKA 70CL
    "p9",     #  53 Ciroc 1 ltr  # CIROK VODKA LTR
    "p4",     #  54 Skyy Vodka 1 ltr
    "p83",    #  55 Arak Touma 0.75 ltr  # ARAK TOUMA 50/54CL
    "p84",    #  56 Efe Raki 1 ltr  # EFE Fresh Grape RAKI LTR Green
    "p17",    #  57 Gold Label 1 ltr  # J/W GOLD LABEL RESERV 1
    "p16",    #  58 Double Black 1 ltr  # J/W DOUBLE BLACK LTR
    "p21",    #  59 Blue Label 1 ltr  # J/W BLUE LABEL 1 LTR
    "p74",    #  60 Hennessy VS 1 ltr  # HENNESSY VS LTR
    "p75",    #  61 Hennessy VSOP 1 ltr  # HENNESSY V.S.O.P 1 LTR Pr
    "p76",    #  62 Hennessy XO 1 ltr  # HENNESSY XO LTR
    "p77",    #  63 Remy Martin VSOP 1 ltr  # REMY MARTIN VSOP LTR
    "p18",    #  64 Chivas Regal 18Y 1 ltr  # CHIVAS 18 YRS LTR
    "p22",    #  65 Chivas Royal Salute 21Y 1 ltr  # ROYAL SALUTE 21 YRS LTR
    "p66",    #  66 Patron XO Cafe 0.75 ltr  # PATRON COFFE
    "p67",    #  67 Patron Silver 0.75 ltr  # PATRON SILVER  75CL TEQUI
    "p68",    #  68 Patron Gold 0.75 ltr  # PATRON ANEJO 75CL GOLD T
    "p69",    #  69 Don Julio Blanco 70/75cl
    "p70",    #  70 Don Julio Reposado 70/75cl  # DON JULIO REPOSADO 70/75
    "p71",    #  71 Don Julio Anejo 70/75cl
    "p72",    #  72 Don Julio 1942 70/75cl  # DON JULIO 1942 ANEJO 70
    "p94",    #  73 Martini Asti 0.75  # ASTI MARTINI 75CL
    "p96",    #  74 Jacob Creek Chardonnay Pinot Noir 0.75  # JC CHARDONNAY PINOT NOIR
    "p91",    #  75 Bottega Prosecco 0.75  # BOTTEGA VINO D POET PROS
    "p92",    #  76 Bottega Rose 0.75  # BOTTEGA ROSE Proseco POE
    "p93",    #  77 Bottega Gold 0.75  # BOTTEGA GOLD BRUT 75C vi
    "p88",    #  78 Veuve Clicquot 0.75  # VEUVE CLICQUOT Y/L PONSR
    "p85",    #  79 Moet Brut 0.75  # MOET & CHANDON BRUT IMP
    "p86",    #  80 Moet Rose 0.75  # MOET & CHANDON  ROSE 75CL
    "p87",    #  81 Moet Ice 0.75  # MOET ICE IMPERIAL 75cl
    "p90",    #  82 Dom Perignon 0.75  # DOM PERIGNON M&C 75CL
    "p25",    #  83 Glenfiddich 12Y 1 ltr  # GLENDFIDICH SPL R12YRS
    "p26",    #  84 Glenfiddich 15Y 1 ltr  # GLENDFIDICH 15 YRS LTR
    "p27",    #  85 Glenfiddich 18Y 0.75 ltr  # GLENDFIDICH 18Y Smal Bat
    "p99",    #  86 Rimapere Sauvignon Blanc 0.75  # BARON RIMAPERE SAUV BLAN
    "p103",   #  87 Gavi Di Gavi 0.75  # MARCHESI GAVI D GAVI 75C
    "p101",   #  88 Louis Moreau Chablis 0.75  # LAROCHE CHABLIS ST MARTI
    "p102",   #  89 Bourgogne Louis Jadot 0.75  # L J BOURGOGNE BL Cuv D ja
    "p107",   #  90 Castel Barreyres 0.75  # CASTEL CH. BARREYRES HAUT M 75
    "p109",   #  91 Chateau Saint Leon 0.75  # CH SAINT LEON BOX SUP 75
    "p110",   #  92 Campo Viejo Reserva 0.75  # CAMPO VIEJO RESERVA RIOJ
    "p113",   #  93 Campo Viejo Gran Reserva 0.75  # CAMPO VIEJO GRAN RESERVA
    "p116",   #  94 Minuty Cotes De Provence 0.75  # M MINUTY ROSE PROVENCE
    "p118",   #  95 Whispering Angel 0.75  # Cav D ESCLN WHISPERING
    "p19",    #  96 Jack Daniels Honey 1 ltr  # JACK DANIELS HONEY LTR
    "p46",    #  97 Bacardi Breezer Melon 0.275 bottle  # BACCARDI BREEZER W/MELON
    "p41",    #  98 Asahi Super Dry 0.33 bottle  # ASAHI BEER BTLS SUPER DR
    "p81",    #  99 Aperol 1 ltr  # APEROLE Aperitivo LTR
    "p24",    # 100 Chivas Regal 25Y 0.7 ltr  # CHIVAS 25 YRS
    "p73",    # 101 Clase Azul Reposado 70/75cl  # CLASE AZUL Reposado 70/7
    "p108",   # 102 Chateau Perron 0.75  # MS CH PERRON LALANDE D POMEROL
    "p112",   # 103 La Celia Malbec 0.75  # LA CELIA RESERVA MALBEC 75CL
    "p100",   # 104 Calvet Sancerre 0.75  # CALVET SANCERRE Les Hautes
    "p111",   # 105 Chateau Des Laurets 0.75  # CHATEAU des LAURETS Saint Emilion
    "p39",    # 106 Guinness 0.44 can  # GUINNESS BEER CANS 44cl
    "p40",    # 107 XXL Vodka 0.25 can  # XXL  VODKA MIX ENERGY CAN
    "p60",    # 108 Monkey 47 0.5 ltr  # MONKEY 47  DRY GIN 50CL
    "p20",    # 109 Gentleman Jack 1 ltr  # GENTLEMAN JACK 1 LTR JD
    "p28",    # 110 Macallan 12Y 0.7 ltr  # MACALLAN 12 YR FIN TRIP
    "p29",    # 111 Macallan 15Y 0.7 ltr  # MACALLAN 15 YRS Double Ca
    "p30",    # 112 Macallan 18Y 0.75 ltr  # MACALLAN 18 YRS
    "p42",    # 113 Hoegaarden 0.33 bottle  # HOEGARDEN BLANCHE 33CL B
    "p82",    # 114 Tequila Rose Strawberry Cream 0.7 ltr  # TEQUILA ROSE LIQUER 70C S/Bery
    "p61",    # 115 Malfy Con Arancia 0.7 ltr  # MALFY Con Ara Blood Orange GIN 70
    "p62",    # 116 Malfy Rosa 0.7 ltr  # MALFY GIN ROSA 70cl GrapfruitE
    "p63",    # 117 Drumshanbo Gunpowder 0.7 ltr  # Drumshanb GUNPODER GIN
    "p119",   # 118 Saint Maur Rose 0.75  # CH SAINT MAUR L Exelenc ROS 7
    "p114",   # 119 Chateau Lagrange 0.75  # CH LAGRANGE 2010 St Julien
    "p89",    # 120 Ruinart Blanc 0.75  # RUINART BLANC D BLANC 75 CL
    "p95",    # 121 Zonin Prosecco 0.75  # ZONIN PROSECCO 75CL
    "p104",   # 122 Oyster Bay Sauvignon Blanc 0.75  # OYSTER BAY SAUVIGNON
    "p15",    # 123 Ballantines Finest 1 ltr
    # Табак. На бумажном листе магазина его нет и не будет — сигареты берём не
    # у Баракуды. Но на полке они стоят и в пересчёте участвуют наравне со
    # всеми, поэтому и номер у них свой, продолжающий тот же счёт. Без этого
    # все позиции вне листа получали один номер на всех и вставали в списке в
    # случайном порядке.
    "p124",   # 124 Marlboro Gold
    "p125",   # 125 TEREA Sienna
    "p126",   # 126 TEREA Amber
]

STOCK_ORDER_INDEX = {pid: i for i, pid in enumerate(STOCK_ORDER)}


def order_key(product_id: str) -> int:
    """Место позиции в обходе. Незнакомые — в конец, но перед этим стабильно."""
    return STOCK_ORDER_INDEX.get(product_id, len(STOCK_ORDER))
