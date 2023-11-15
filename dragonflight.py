import slugify

dungeons = ["Atal'Dazar",
            "Black Rook Hold",
            "DOTI: Galakrond's Fall",
            "DOTI: Murozond's Rise",
            "Darkheart Thicket",
            "Everbloom",
            "Throne of the Tides",
            "Waycrest Manor"]            

dungeon_short_names = {}
dungeon_short_names["Atal'Dazar"] = "AD"
dungeon_short_names["Black Rook Hold"] = "BRH"
dungeon_short_names["Darkheart Thicket"] = "DHT"
dungeon_short_names["DOTI: Galakrond's Fall"] = "FALL"
dungeon_short_names["DOTI: Murozond's Rise"] = "RISE"
dungeon_short_names["Everbloom"] = "EB"
dungeon_short_names["Throne of the Tides"] = "TOT"
dungeon_short_names["Waycrest Manor"] = "WM"

dungeon_slugs = []
for d in dungeons:
    dungeon_slugs += [slugify.slugify(unicode(d))]

slugs_to_dungeons = {}
for d in dungeons:
    slugs_to_dungeons[slugify.slugify(unicode(d))] = d



tier_items = [ ### aberrus tier
             202464, 202462, 202461, 202460, 202459, # dk
             202527, 202525, 202524, 202523, 202522, # dh
             202518, 202516, 202515, 202514, 202513, # druid
             202491, 202489, 202488, 202487, 202486, # evoker
             202482, 202480, 202479, 202478, 202477, # hunter
             202554, 202552, 202551, 202550, 202549, # mage
             202509, 202507, 202506, 202505, 202504, # monk
             202455, 202453, 202452, 202451, 202450, # paladin
             202543, 202542, 202541, 202545, 202540, # priest  
             202500, 202498, 202497, 202496, 202495, # rogue
             202473, 202471, 202470, 202469, 202468, # shaman
             202534, 202533, 202532, 202536, 202531, # warlock
             202446, 202444, 202443, 202442, 202441, # warrior
             ### vault tier
             200405, 200407, 200408, 200409, 200410, # dk
             200342, 200344, 200345, 200346, 200347, # dh
             200351, 200353, 200354, 200355, 200356, # druid
             200378, 200380, 200381, 200382, 200383, # evoker
             200387, 200389, 200390, 200391, 200392, # hunter
             200315, 200317, 200318, 200319, 200320, # mage
             200360, 200362, 200363, 200364, 200365, # monk
             200414, 200416, 200417, 200418, 200419, # paladin
             200326, 200327, 200328, 200324, 200329, # priest  
             200369, 200371, 200372, 200373, 200374, # rogue
             200396, 200398, 200399, 200400, 200401, # shaman
             200335, 200336, 200337, 200333, 200338, # warlock
             200423, 200425, 200426, 200427, 200428, # warrior
             ### amirdrassil tier
             207198, 207199, 207200, 207201, 207203, # dk
             207261, 207262, 207263, 207264, 207266, # dh
             207252, 207253, 207254, 207255, 207257, # druid
             207225, 207226, 207227, 207228, 207230, # evoker
             207216, 207217, 207218, 207219, 207221, # hunter
             207288, 207289, 207290, 207291, 207293, # mage
             207243, 207244, 207245, 207246, 207248, # monk
             207189, 207190, 207191, 207192, 207194, # paladin
             207279, 207280, 207281, 207282, 207284, # priest  
             207234, 207235, 207236, 207237, 207239, # rogue
             207207, 207208, 207209, 207210, 207212, # shaman
             207270, 207271, 207272, 207273, 207275, # warlock
             207180, 207181, 207182, 207183, 207185, # warrior
            ]

embellished_items = [
    191623, 191985, 190522, 190523, 190526, 190519, # Plate
    193463, 193460, 193465, 193462, 193461, 193459, 193464, 193466, 204704, # Mail
    193452, 193457, 193458, 193451, 193454, 193456, 193455, 193494, 193453, 204706, # Leather
    193524, 193513, 193521, 193512, 193537, 193527, 193532, 193525, 193536, 193520, 193526, 193530, 205025, # Cloth
    193001, 193002, # Jewelry
    193496, 192081, 194894, 204401, 205046, 205168, # Weapons
]

primordial_stones = [
    # Fire
    "204002", "204003", "204004", "204005",
    # Frost
    "204010", "204011", "204012", "204013",
    # Earth
    "204006", "204007", "204009",
    # Nature
    "204000", "204001", "204020", "204022", "204030",
    # Arcane
    "204014", "204018", "204019", "204025",
    # Shadow
    "204015", "204029",
    # Necro
    "204021", "204027",
]

crafted_items = embellished_items + [
    198333, 190502, 190496, 190500, 190499, 190498, 190501, 190497, 190495, # Plate
    193421, 193424, 193426, 193423, 193427, 193428, 193422, 193425, 198332, # Mail
    193408, 193406, 193418, 193399, 193400, 193398, 193419, 193407, 198327, # Leather
    193516, 193504, 193523, 193511, 193508, 193518, 193519, 193509, 193510, 198322, # Cloth
    191492, 191491, 193006, 193003, 193004, 193005, 201759, 192999, 193000, # Jewelry
    193449, 194879, 194898, 194897, 198335, 190503, 190507, 190508, 190505, 190506, 190509, 190510, 190518, 190511, 190516, 190512, 190515, 190513, 190517, 190514, # Weapons
]


### ARCHIVE ###

# dungeons = ["Algeth'ar Academy",
#             "Court of Stars",
#             "Halls of Valor",
#             "Ruby Life Pools",
#             "Shadowmoon Burial Grounds",
#             "Temple of the Jade Serpent",
#             "The Azure Vault",
#             "The Nokhud Offensive"]            

# dungeon_short_names["Algeth'ar Academy"] = "AA"
# dungeon_short_names["Court of Stars"] = "COS"
# dungeon_short_names["Halls of Valor"] = "HOV"
# dungeon_short_names["Ruby Life Pools"] = "RLP"
# dungeon_short_names["Shadowmoon Burial Grounds"] = "SBG"
# dungeon_short_names["Temple of the Jade Serpent"] = "TJS"
# dungeon_short_names["The Azure Vault"] = "AV"
# dungeon_short_names["The Nokhud Offensive"] = "NO"

# t30_items = [200405, 200407, 200408, 200409, 200410, # dk
#              200342, 200344, 200345, 200346, 200347, # dh
#              200351, 200353, 200354, 200355, 200356, # druid
#              200378, 200380, 200381, 200382, 200383, # evoker
#              200387, 200389, 200390, 200391, 200392, # hunter
#              200315, 200317, 200318, 200319, 200320, # mage
#              200360, 200362, 200363, 200364, 200365, # monk
#              200414, 200416, 200417, 200418, 200419, # paladin
#              200326, 200327, 200328, 200324, 200329, # priest  
#              200369, 200371, 200372, 200373, 200374, # rogue
#              200396, 200398, 200399, 200400, 200401, # shaman
#              200335, 200336, 200337, 200333, 200338, # warlock
#              200423, 200425, 200426, 200427, 200428, # warrior
#             ]
