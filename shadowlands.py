import slugify

dungeons = ["De Other Side",
            "Halls of Atonement",
            "Mists of Tirna Scithe",
            "The Necrotic Wake",            
            "Plaguefall",
            "Sanguine Depths",
            "Spires of Ascension",
            "Theater of Pain",
            "Tazavesh: Streets of Wonder",
            "Tazavesh: So'leah's Gambit"]

dungeon_short_names = {}
dungeon_short_names["De Other Side"] = "DOS"
dungeon_short_names["Halls of Atonement"] = "HOA"
dungeon_short_names["Mists of Tirna Scithe"] = "MISTS"
dungeon_short_names["Plaguefall"] = "PF"
dungeon_short_names["Sanguine Depths"] = "SD"
dungeon_short_names["Spires of Ascension"] = "SOA"
dungeon_short_names["The Necrotic Wake"] = "NW"
dungeon_short_names["Theater of Pain"] = "TOP"
dungeon_short_names["Tazavesh: Streets of Wonder"] = "STRT"
dungeon_short_names["Tazavesh: So'leah's Gambit"] = "GMBT"

dungeon_slugs = []
for d in dungeons:
    dungeon_slugs += [slugify.slugify(unicode(d))]

slugs_to_dungeons = {}
for d in dungeons:
    slugs_to_dungeons[slugify.slugify(unicode(d))] = d

# no rotation, will just be a pass through when empty
tormented_weeks = {}

covenantID_mapping = {}
covenantID_mapping[1] = {"name" : "Kyrian",
                         "id" : 321076,
                         "icon" : "ui_sigil_kyrian.jpg"}
covenantID_mapping[2] = {"name" : "Venthyr",
                         "id" : 321079,
                         "icon" : "ui_sigil_venthyr.jpg"}
covenantID_mapping[3] = {"name" : "Night Fae",
                         "id" : 321077,
                         "icon" : "ui_sigil_nightfae.jpg"}
covenantID_mapping[4] = {"name" : "Necrolord",
                         "id" : 321078,
                         "icon" : "ui_sigil_necrolord.jpg"}

covenantNameToID = {}
for k, v in covenantID_mapping.iteritems():
    covenantNameToID[v["name"]] = v["id"]


# itemIds for shards of domination

shards_of_domination = ["187057", "187284", "187293", "187302", "187312", # bek - blood
                        "187059", "187285", "187294", "187303", "187313", # jas
                        "187061", "187286", "187295", "187304", "187314", # rev
                        "187063", "187287", "187296", "187305", "187315", # cor - frost
                        "187065", "187288", "187297", "187306", "187316", # kyr
                        "187071", "187289", "187298", "187307", "187317", # tel
                        "187073", "187290", "187299", "187308", "187318", # dyz
                        "187076", "187291", "187300", "187309", "187319", # oth
                        "187079", "187292", "187301", "187310", "187320", # zed
                        ]

                        
                         


