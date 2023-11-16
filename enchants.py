enchant_mapping = {}
# Dragonflight

# simplified to ignore quality since it's not relevant for decision making
# to collapse multiple ranks
enchant_collapse = \
    {'6488': '6490',
     '6489': '6490',
     '6490': '6490',
     '6491': '6493',
     '6492': '6493',
     '6493': '6493',
     '6494': '6496',
     '6495': '6496',
     '6496': '6496',
     '6520': '6522',
     '6521': '6522',
     '6522': '6522',
     '6523': '6525',
     '6524': '6525',
     '6525': '6525',
     '6526': '6528',
     '6527': '6528',
     '6528': '6528',
     '6536': '6538',
     '6537': '6538',
     '6538': '6538',
     '6539': '6541',
     '6540': '6541',
     '6541': '6541',
     '6542': '6544',
     '6543': '6544',
     '6544': '6544',
     '6545': '6547',
     '6546': '6547',
     '6547': '6547',
     '6548': '6550',
     '6549': '6550',
     '6550': '6550',
     '6551': '6553',
     '6552': '6553',
     '6553': '6553',
     '6554': '6556',
     '6555': '6556',
     '6556': '6556',
     '6557': '6559',
     '6558': '6559',
     '6559': '6559',
     '6560': '6562',
     '6561': '6562',
     '6562': '6562',
     '6563': '6565',
     '6564': '6565',
     '6565': '6565',
     '6566': '6568',
     '6567': '6568',
     '6568': '6568',
     '6569': '6589',
     '6570': '6589',
     '6571': '6589',
     '6572': '6574',
     '6573': '6574',
     '6574': '6574',
     '6575': '6595',
     '6576': '6595',
     '6577': '6595',
     '6578': '6580',
     '6579': '6580',
     '6580': '6580',
     '6581': '6583',
     '6582': '6583',
     '6583': '6583',
     '6584': '6586',
     '6585': '6586',
     '6586': '6586',
     '6587': '6589',
     '6588': '6589',
     '6589': '6589',
     '6590': '6592',
     '6591': '6592',
     '6592': '6592',
     '6593': '6595',
     '6594': '6595',
     '6595': '6595',
     '6596': '6598',
     '6597': '6598',
     '6598': '6598',
     '6599': '6583',
     '6600': '6583',
     '6601': '6583',
     '6602': '6604',
     '6603': '6604',
     '6604': '6604',
     '6605': '6607',
     '6606': '6607',
     '6607': '6607',
     '6608': '6610',
     '6609': '6610',
     '6610': '6610',
     '6611': '6613',
     '6612': '6613',
     '6613': '6613',
     '6614': '6616',
     '6615': '6616',
     '6616': '6616',
     '6617': '6619',
     '6618': '6619',
     '6619': '6619',
     '6620': '6622',
     '6621': '6622',
     '6622': '6622',
     '6623': '6625',
     '6624': '6625',
     '6625': '6625',
     '6626': '6628',
     '6627': '6628',
     '6628': '6628',
     '6629': '6631',
     '6630': '6631',
     '6631': '6631',
     '6632': '6634',
     '6633': '6634',
     '6634': '6634',
     '6635': '6637',
     '6636': '6637',
     '6637': '6637',
     '6638': '6640',
     '6639': '6640',
     '6640': '6640',
     '6641': '6643',
     '6642': '6643',
     '6643': '6643',
     '6644': '6646',
     '6645': '6646',
     '6646': '6646',
     '6647': '6649',
     '6648': '6649',
     '6649': '6649',
     '6650': '6652',
     '6651': '6652',
     '6652': '6652',
     '6653': '6655',
     '6654': '6655',
     '6655': '6655',
    '6822': '6824',
    '6823': '6824',
    '6824': '6824',
    '6825': '6827',
    '6826': '6827',
    '6827': '6827',
    '6828': '6830',
    '6829': '6830',
    '6830': '6830',
    '7001': '7003',
    '7002': '7003',
    '7003': '7003'}

# Leg armor
enchant_mapping["6488"] = {"id": 193557, "name": "Fierce Armor Kit", "quality": 1}
enchant_mapping["6489"] = {"id": 193561, "name": "Fierce Armor Kit", "quality": 2}
enchant_mapping["6490"] = {"id": 193565, "name": "Fierce Armor Kit", "quality": 3}
enchant_mapping["6491"] = {"id": 193559, "name": "Reinforced Armor Kit", "quality": 1}
enchant_mapping["6492"] = {"id": 193563, "name": "Reinforced Armor Kit", "quality": 2}
enchant_mapping["6493"] = {"id": 193567, "name": "Reinforced Armor Kit", "quality": 3}
enchant_mapping["6494"] = {"id": 193556, "name": "Frosted Armor Kit", "quality": 1}
enchant_mapping["6495"] = {"id": 193560, "name": "Frosted Armor Kit", "quality": 2}
enchant_mapping["6496"] = {"id": 193564, "name": "Frosted Armor Kit", "quality": 3}
enchant_mapping["6828"] = {"id": 204700, "name": "Lambent Armor Kit", "quality": 1}
enchant_mapping["6829"] = {"id": 204701, "name": "Lambent Armor Kit", "quality": 2}
enchant_mapping["6830"] = {"id": 204702, "name": "Lambent Armor Kit", "quality": 3}

# Scope
enchant_mapping["6520"] = {"id": 198310, "name": "Gyroscopic Kaleidoscope", "quality": 1}
enchant_mapping["6521"] = {"id": 198311, "name": "Gyroscopic Kaleidoscope", "quality": 2}
enchant_mapping["6522"] = {"id": 198312, "name": "Gyroscopic Kaleidoscope", "quality": 3}
enchant_mapping["6523"] = {"id": 198313, "name": "Projectile Propulsion Pinion", "quality": 1}
enchant_mapping["6524"] = {"id": 198314, "name": "Projectile Propulsion Pinion", "quality": 2}
enchant_mapping["6525"] = {"id": 198315, "name": "Projectile Propulsion Pinion", "quality": 3}
enchant_mapping["6526"] = {"id": 198316, "name": "High Intensity Thermal Scanner", "quality": 1}
enchant_mapping["6527"] = {"id": 198317, "name": "High Intensity Thermal Scanner", "quality": 2}
enchant_mapping["6528"] = {"id": 198318, "name": "High Intensity Thermal Scanner", "quality": 3}

# Spellthread
enchant_mapping["6536"] = {"id": 194008, "name": "Vibrant Spellthread", "quality": 1}
enchant_mapping["6537"] = {"id": 194009, "name": "Vibrant Spellthread", "quality": 2}
enchant_mapping["6538"] = {"id": 194010, "name": "Vibrant Spellthread", "quality": 3}
enchant_mapping["6539"] = {"id": 194011, "name": "Frozen Spellthread", "quality": 1}
enchant_mapping["6540"] = {"id": 194012, "name": "Frozen Spellthread", "quality": 2}
enchant_mapping["6541"] = {"id": 194013, "name": "Frozen Spellthread", "quality": 3}
enchant_mapping["6542"] = {"id": 194014, "name": "Temporal Spellthread", "quality": 1}
enchant_mapping["6543"] = {"id": 194015, "name": "Temporal Spellthread", "quality": 2}
enchant_mapping["6544"] = {"id": 194016, "name": "Temporal Spellthread", "quality": 3}

# Ring
enchant_mapping["6545"] = {"id": 199957, "name": "Writ of Critical Strike", "quality": 1}
enchant_mapping["6546"] = {"id": 199999, "name": "Writ of Critical Strike", "quality": 2}
enchant_mapping["6547"] = {"id": 200041, "name": "Writ of Critical Strike", "quality": 3}
enchant_mapping["6548"] = {"id": 199953, "name": "Devotion of Critical Strike", "quality": 1}
enchant_mapping["6549"] = {"id": 199995, "name": "Devotion of Critical Strike", "quality": 2}
enchant_mapping["6550"] = {"id": 200037, "name": "Devotion of Critical Strike", "quality": 3}
enchant_mapping["6551"] = {"id": 199958, "name": "Writ of Haste", "quality": 1}
enchant_mapping["6552"] = {"id": 200000, "name": "Writ of Haste", "quality": 2}
enchant_mapping["6553"] = {"id": 200042, "name": "Writ of Haste", "quality": 3}
enchant_mapping["6554"] = {"id": 199954, "name": "Devotion of Haste", "quality": 1}
enchant_mapping["6555"] = {"id": 199996, "name": "Devotion of Haste", "quality": 2}
enchant_mapping["6556"] = {"id": 200038, "name": "Devotion of Haste", "quality": 3}
enchant_mapping["6557"] = {"id": 199959, "name": "Writ of Mastery", "quality": 1}
enchant_mapping["6558"] = {"id": 200001, "name": "Writ of Mastery", "quality": 2}
enchant_mapping["6559"] = {"id": 200043, "name": "Writ of Mastery", "quality": 3}
enchant_mapping["6560"] = {"id": 199955, "name": "Devotion of Mastery", "quality": 1}
enchant_mapping["6561"] = {"id": 199997, "name": "Devotion of Mastery", "quality": 2}
enchant_mapping["6562"] = {"id": 200039, "name": "Devotion of Mastery", "quality": 3}
enchant_mapping["6563"] = {"id": 199960, "name": "Writ of Versatility", "quality": 1}
enchant_mapping["6564"] = {"id": 200002, "name": "Writ of Versatility", "quality": 2}
enchant_mapping["6565"] = {"id": 200044, "name": "Writ of Versatility", "quality": 3}
enchant_mapping["6566"] = {"id": 199956, "name": "Devotion of Versatility", "quality": 1}
enchant_mapping["6567"] = {"id": 199998, "name": "Devotion of Versatility", "quality": 2}
enchant_mapping["6568"] = {"id": 200040, "name": "Devotion of Versatility", "quality": 3}

# Wrist
enchant_mapping["6569"] = {"id": 199940, "name": "Writ of Avoidance", "quality": 1}
enchant_mapping["6570"] = {"id": 199982, "name": "Writ of Avoidance", "quality": 2}
enchant_mapping["6571"] = {"id": 200024, "name": "Writ of Avoidance", "quality": 3}
enchant_mapping["6572"] = {"id": 199937, "name": "Devotion of Avoidance", "quality": 1}
enchant_mapping["6573"] = {"id": 199979, "name": "Devotion of Avoidance", "quality": 2}
enchant_mapping["6574"] = {"id": 200021, "name": "Devotion of Avoidance", "quality": 3}
enchant_mapping["6575"] = {"id": 199941, "name": "Writ of Leech", "quality": 1}
enchant_mapping["6576"] = {"id": 199983, "name": "Writ of Leech", "quality": 2}
enchant_mapping["6577"] = {"id": 200025, "name": "Writ of Leech", "quality": 3}
enchant_mapping["6578"] = {"id": 199938, "name": "Devotion of Leech", "quality": 1}
enchant_mapping["6579"] = {"id": 199980, "name": "Devotion of Leech", "quality": 2}
enchant_mapping["6580"] = {"id": 200022, "name": "Devotion of Leech", "quality": 3}
enchant_mapping["6581"] = {"id": 199942, "name": "Writ of Speed", "quality": 1}
enchant_mapping["6582"] = {"id": 199984, "name": "Writ of Speed", "quality": 2}
enchant_mapping["6583"] = {"id": 200026, "name": "Writ of Speed", "quality": 3}
enchant_mapping["6584"] = {"id": 199939, "name": "Devotion of Speed", "quality": 1}
enchant_mapping["6585"] = {"id": 199981, "name": "Devotion of Speed", "quality": 2}
enchant_mapping["6586"] = {"id": 200023, "name": "Devotion of Speed", "quality": 3}

# Back
enchant_mapping["6587"] = {"id": 199950, "name": "Writ of Avoidance", "quality": 1}
enchant_mapping["6588"] = {"id": 199992, "name": "Writ of Avoidance", "quality": 2}
enchant_mapping["6589"] = {"id": 200034, "name": "Writ of Avoidance", "quality": 3}
enchant_mapping["6590"] = {"id": 199947, "name": "Graceful Avoidance", "quality": 1}
enchant_mapping["6591"] = {"id": 199989, "name": "Graceful Avoidance", "quality": 2}
enchant_mapping["6592"] = {"id": 200031, "name": "Graceful Avoidance", "quality": 3}
enchant_mapping["6593"] = {"id": 199951, "name": "Writ of Leech", "quality": 1}
enchant_mapping["6594"] = {"id": 199993, "name": "Writ of Leech", "quality": 2}
enchant_mapping["6595"] = {"id": 200035, "name": "Writ of Leech", "quality": 3}
enchant_mapping["6596"] = {"id": 199949, "name": "Regenerative Leech", "quality": 1}
enchant_mapping["6597"] = {"id": 199991, "name": "Regenerative Leech", "quality": 2}
enchant_mapping["6598"] = {"id": 200033, "name": "Regenerative Leech", "quality": 3}
enchant_mapping["6599"] = {"id": 199952, "name": "Writ of Speed", "quality": 1}
enchant_mapping["6600"] = {"id": 199994, "name": "Writ of Speed", "quality": 2}
enchant_mapping["6601"] = {"id": 200036, "name": "Writ of Speed", "quality": 3}
enchant_mapping["6602"] = {"id": 199948, "name": "Homebound Speed", "quality": 1}
enchant_mapping["6603"] = {"id": 199990, "name": "Homebound Speed", "quality": 2}
enchant_mapping["6604"] = {"id": 200032, "name": "Homebound Speed", "quality": 3}

# Boots
enchant_mapping["6605"] = {"id": 199934, "name": "Plainsrunner's Breeze", "quality": 1}
enchant_mapping["6606"] = {"id": 199976, "name": "Plainsrunner's Breeze", "quality": 2}
enchant_mapping["6607"] = {"id": 200018, "name": "Plainsrunner's Breeze", "quality": 3}
enchant_mapping["6608"] = {"id": 199935, "name": "Rider's Reassurance", "quality": 1}
enchant_mapping["6609"] = {"id": 199977, "name": "Rider's Reassurance", "quality": 2}
enchant_mapping["6610"] = {"id": 200019, "name": "Rider's Reassurance", "quality": 3}
enchant_mapping["6611"] = {"id": 199936, "name": "Watcher's Loam", "quality": 1}
enchant_mapping["6612"] = {"id": 199978, "name": "Watcher's Loam", "quality": 2}
enchant_mapping["6613"] = {"id": 200020, "name": "Watcher's Loam", "quality": 3}

# Chest
enchant_mapping["6614"] = {"id": 199943, "name": "Accelerated Agility", "quality": 1}
enchant_mapping["6615"] = {"id": 199985, "name": "Accelerated Agility", "quality": 2}
enchant_mapping["6616"] = {"id": 200027, "name": "Accelerated Agility", "quality": 3}
enchant_mapping["6617"] = {"id": 199944, "name": "Reserve of Intellect", "quality": 1}
enchant_mapping["6618"] = {"id": 199986, "name": "Reserve of Intellect", "quality": 2}
enchant_mapping["6619"] = {"id": 200028, "name": "Reserve of Intellect", "quality": 3}
enchant_mapping["6620"] = {"id": 199945, "name": "Sustained Strength", "quality": 1}
enchant_mapping["6621"] = {"id": 199987, "name": "Sustained Strength", "quality": 2}
enchant_mapping["6622"] = {"id": 200029, "name": "Sustained Strength", "quality": 3}
enchant_mapping["6623"] = {"id": 199946, "name": "Waking Stats", "quality": 1}
enchant_mapping["6624"] = {"id": 199988, "name": "Waking Stats", "quality": 2}
enchant_mapping["6625"] = {"id": 200030, "name": "Waking Stats", "quality": 3}

# Weapon
enchant_mapping["6626"] = {"id": 199967, "name": "Burning Writ", "quality": 1}
enchant_mapping["6627"] = {"id": 200009, "name": "Burning Writ", "quality": 2}
enchant_mapping["6628"] = {"id": 200051, "name": "Burning Writ", "quality": 3}
enchant_mapping["6629"] = {"id": 199966, "name": "Burning Devotion", "quality": 1}
enchant_mapping["6630"] = {"id": 200008, "name": "Burning Devotion", "quality": 2}
enchant_mapping["6631"] = {"id": 200050, "name": "Burning Devotion", "quality": 3}
enchant_mapping["6632"] = {"id": 199969, "name": "Earthen Writ", "quality": 1}
enchant_mapping["6633"] = {"id": 200011, "name": "Earthen Writ", "quality": 2}
enchant_mapping["6634"] = {"id": 200053, "name": "Earthen Writ", "quality": 3}
enchant_mapping["6635"] = {"id": 199968, "name": "Earthen Devotion", "quality": 1}
enchant_mapping["6636"] = {"id": 200010, "name": "Earthen Devotion", "quality": 2}
enchant_mapping["6637"] = {"id": 200052, "name": "Earthen Devotion", "quality": 3}
enchant_mapping["6638"] = {"id": 199971, "name": "Sophic Writ", "quality": 1}
enchant_mapping["6639"] = {"id": 200013, "name": "Sophic Writ", "quality": 2}
enchant_mapping["6640"] = {"id": 200055, "name": "Sophic Writ", "quality": 3}
enchant_mapping["6641"] = {"id": 199970, "name": "Sophic Devotion", "quality": 1}
enchant_mapping["6642"] = {"id": 200012, "name": "Sophic Devotion", "quality": 2}
enchant_mapping["6643"] = {"id": 200054, "name": "Sophic Devotion", "quality": 3}
enchant_mapping["6644"] = {"id": 199973, "name": "Frozen Writ", "quality": 1}
enchant_mapping["6645"] = {"id": 200015, "name": "Frozen Writ", "quality": 2}
enchant_mapping["6646"] = {"id": 200057, "name": "Frozen Writ", "quality": 3}
enchant_mapping["6647"] = {"id": 199972, "name": "Frozen Devotion", "quality": 1}
enchant_mapping["6648"] = {"id": 200014, "name": "Frozen Devotion", "quality": 2}
enchant_mapping["6649"] = {"id": 200056, "name": "Frozen Devotion", "quality": 3}
enchant_mapping["6650"] = {"id": 199975, "name": "Wafting Writ", "quality": 1}
enchant_mapping["6651"] = {"id": 200017, "name": "Wafting Writ", "quality": 2}
enchant_mapping["6652"] = {"id": 200059, "name": "Wafting Writ", "quality": 3}
enchant_mapping["6653"] = {"id": 199974, "name": "Wafting Devotion", "quality": 1}
enchant_mapping["6654"] = {"id": 200016, "name": "Wafting Devotion", "quality": 2}
enchant_mapping["6655"] = {"id": 200058, "name": "Wafting Devotion", "quality": 3}
enchant_mapping["6822"] = {"id": 204613, "name": "Spore Tender", "quality": 1}
enchant_mapping["6823"] = {"id": 204614, "name": "Spore Tender", "quality": 2}
enchant_mapping["6824"] = {"id": 204615, "name": "Spore Tender", "quality": 3}
enchant_mapping["6825"] = {"id": 204621, "name": "Shadowflame Wreathe", "quality": 1}
enchant_mapping["6826"] = {"id": 204622, "name": "Shadowflame Wreathe", "quality": 2}
enchant_mapping["6827"] = {"id": 204623, "name": "Shadowflame Wreathe", "quality": 3}
enchant_mapping["7001"] = {"id": 207085, "name": "Dreaming Devotion", "quality": 1}
enchant_mapping["7002"] = {"id": 207086, "name": "Dreaming Devotion", "quality": 2}
enchant_mapping["7003"] = {"id": 207087, "name": "Dreaming Devotion", "quality": 3}
# -----------------------------------------
# Death Knight Runeforging -- (note this is spell id, since there's no scroll)
enchant_mapping["3368"] = {"spell_id" : 53344, "name" : "Fallen Crusader"}
enchant_mapping["3370"] = {"spell_id" : 53343, "name" : "Razorice"}
enchant_mapping["3847"] = {"spell_id" : 62158, "name" : "Stoneskin Gargoyle"}
enchant_mapping["6241"] = {"spell_id" : 326805, "name" : "Sanguination"}
enchant_mapping["6242"] = {"spell_id" : 326855, "name" : "Spellwarding"}
enchant_mapping["6243"] = {"spell_id" : 326911, "name" : "Hysteria"}
enchant_mapping["6244"] = {"spell_id" : 326977, "name" : "Unending Thirst"}
enchant_mapping["6245"] = {"spell_id" : 327082, "name" : "Apocalypse"}

# -----------------------------------------
# LEGACY

# SL Enchants (why?)
enchant_mapping["6203"] = {"id": 172411, "name": "Fortified Avoidance"}
enchant_mapping["6229"] = {"id": 172366, "name": "Celestial Guidance"}
enchant_mapping["6230"] = {"id": 177962, "name": "Eternal Stats"}

# Battle for Azeroth Engineering
## Eng. Belt Enhancements -- note this is by SPELL ID, since there's no item
enchant_mapping["5953"] = {"spell_id": 255940,  "name": "Personal Space Amplifier"}
enchant_mapping["5967"] = {"spell_id": 269123,  "name": "Miniaturized Plasma Shield"}
enchant_mapping["5952"] = {"spell_id": 255936, "name": "Holographic Horror Projector"}

## Eng. Belt Enhancements -- note this is by SPELL ID, since there's no item
enchant_mapping["3599"] = {"spell_id": 54736, "name": "EMP Generator"}
enchant_mapping["3601"] = {"spell_id": 54793, "name": "Frag Belt"}
enchant_mapping["4187"] = {"spell_id": 84424, "name": "Invisibility Field"}
enchant_mapping["4188"] = {"spell_id": 84427, "name": "Grounded Plasma Shield"}
enchant_mapping["4222"] = {"spell_id": 67839, "name": "Mind Amplification Dish"}
enchant_mapping["4214"] = {"spell_id": 84425, "name": "Cardboard Assassin"}
enchant_mapping["4223"] = {"spell_id": 55016, "name": "Nitro Boosts"}
enchant_mapping["4750"] = {"spell_id": 82200, "name": "Spinal Healing Injector"}
enchant_mapping["5000"] = {"spell_id": 109099, "name": "Watergliding Jets"}
enchant_mapping["6192"] = {"spell_id": 310495, "name": "Dimensional Shifter"}
