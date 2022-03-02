import webapp2
import logging
import os
import json
import copy
import operator
import time
import pdb

from google.appengine.api import app_identity

from google.appengine.api import urlfetch
from google.appengine.ext import ndb
from google.appengine.ext import deferred
from google.appengine.api.taskqueue import TaskRetryOptions
from google.appengine.runtime import DeadlineExceededError

from google.appengine.ext import vendor
# add libraries in lib
vendor.add('lib')

import slugify
import cloudstorage as gcs

from shadowlands import dungeons, dungeon_slugs, dungeon_short_names, slugs_to_dungeons
from shadowlands import tormented_weeks as affix_rotation_weeks
from shadowlands import covenantID_mapping
from shadowlands import covenantNameToID
from shadowlands import shards_of_domination # shards of dominaion ids

from warcraft import specs, tanks, healers, melee, ranged, role_titles, regions, pvp_regions, pvp_modes
from warcraft import spec_short_names
from t_interval import t_interval

from models import Run, DungeonAffixRegion, KnownAffixes, PvPLadderStats, PvPCounts

# wcl handling
from models import SpecRankings, SpecRankingsRaid, CovenantStats, RaidCounts, DungeonEaseTierList
from auth import api_key
from wcl import wcl_specs
from wcl_shadowlands import dungeon_encounters
from wcl_shadowlands import sepulcher_encounters as raid_encounters

from enchants import enchant_mapping


from sepulcher import sep_canonical_order as raid_canonical_order
from sepulcher import sep_short_names as raid_short_names
from sepulcher import sep_ignore as raid_ignore

# cloudflare cache handling
from auth import cloudflare_api_key, cloudflare_zone

# ludus labs api
from auth import ludus_access_key

## globals
from config import RIO_MAX_PAGE
from shadowlands import dungeons as DUNGEONS
from warcraft import regions as REGIONS
from config import RIO_MAX_PAGE, RIO_SEASON, RAID_NAME
from config import WCL_SEASON, WCL_PARTITION
from config import MIN_KEY_LEVEL
from config import MAX_RAID_DIFFICULTY

## raider.io handling
def update_known_affixes(affixes, affixes_slug):
    '''Update datastore's list of known affixes and their last seen times'''
    key = ndb.Key('KnownAffixes', affixes_slug)
    known_affix = key.get()

    if known_affix is None: # only add it if we haven't seen it before
        known_affix = KnownAffixes(id=affixes_slug, affixes=affixes)
        known_affix.put()
    else:
        known_affix.put() # put it back to update last seen

def parse_individual_ranking(ranking):
    '''Parse an individual r.io run and return a Run model object for it'''

    score = ranking["score"]
    run = ranking["run"]

    roster = []
    ksrid = ""
    completed_at = ""
    completed_at = datetime.datetime.strptime(run["completed_at"], "%Y-%m-%dT%H:%M:%S.%fZ")

    clear_time_ms = run["clear_time_ms"]
    mythic_level = run["mythic_level"]
    if mythic_level < MIN_KEY_LEVEL: # only track runs at +16 or above
        return None
    num_chests = run["num_chests"]
    keystone_time_ms = run["keystone_time_ms"]
    faction = run["faction"]
    ksrid = str(run["keystone_run_id"])

    for roster_entry in run["roster"]:
        character = roster_entry["character"]
        spec_class = character["spec"]["name"] + " " + character["class"]["name"]
        roster += [spec_class]

    return Run(score=score, roster=roster, keystone_run_id=ksrid,
               completed_at=completed_at, clear_time_ms=clear_time_ms,
               mythic_level=mythic_level, num_chests=num_chests,
               keystone_time_ms=keystone_time_ms, faction=faction)


def parse_response(data, dungeon, affixes, region, page):
    '''Parse the response from r.io and store it in our datastore'''
    dungeon_slug = slugify.slugify(unicode(dungeon))

    if affixes == "current":
        affixes = ""
        affixes += data[0]["run"]["weekly_modifiers"][0]["name"] + ", "
        affixes += data[0]["run"]["weekly_modifiers"][1]["name"] + ", "
        affixes += data[0]["run"]["weekly_modifiers"][2]["name"] + ", "
        affixes += data[0]["run"]["weekly_modifiers"][3]["name"]

    affixes_slug = slugify.slugify(unicode(affixes))
    update_known_affixes(affixes, affixes_slug)


    key_string = dungeon_slug + "-" + affixes_slug + "-" + region + "-" + str(page)
    key = ndb.Key('DungeonAffixRegion',
                  key_string)
    dar = DungeonAffixRegion(key=key)

    dar.dungeon = dungeon
    dar.affixes = affixes
    dar.region = region
    dar.page = page

    for individual_ranking in data:
        parsed_run = parse_individual_ranking(individual_ranking)
        if parsed_run is not None:
            dar.runs += [parsed_run]

    return dar


# update

## @@season update
## also in templates/max_link and templates/by-affix
## also in wcl_ (also marked with @@)

def update_dungeon_affix_region(dungeon, affixes, region, season=RIO_SEASON, page=0):
    '''For a given dungeon, affixes, region, season, and page, get top M+ runs'''
    dungeon_slug = slugify.slugify(unicode(dungeon))

    if region == "cn" and affixes == "current": # not working properly for cn
        affixes = current_affixes()
    
    affixes_slug = slugify.slugify(unicode(affixes))


    req_url = "https://raider.io/api/v1/mythic-plus/runs?"
    req_url += "season=%s&region=%s&affixes=%s&dungeon=%s&page=%d" \
        % (season, region, affixes_slug, dungeon_slug, page)

    response = {}
    try:
        result = urlfetch.fetch(req_url, deadline=60)
        if result.status_code == 200:
            response = json.loads(result.content)["rankings"]
            if response == []: # empty rankings, as sometimes happens at week start
                logging.info("no rankings found for %s / %s / %s / %s",
                             dungeon, affixes, region, page)
                return
            dar = parse_response(response,
                                 dungeon, affixes, region, page)
            dar.put()
    except DeadlineExceededError:
        logging.exception('deadline exception fetching url: %s', req_url)
        options = TaskRetryOptions(task_retry_limit=1)
        deferred.defer(update_dungeon_affix_region, dungeon, affixes,
                       region, season, page, _retry_options=options)

    except urlfetch.Error:
        logging.exception('caught exception fetching url: %s', req_url)

def update_current():
    '''Query the r.io api across all regions for each dungeon (current affixes)'''
    global DUNGEONS, REGIONS, RIO_MAX_PAGE
    for region in REGIONS:
        for dungeon in DUNGEONS:
            for page in range(0, RIO_MAX_PAGE):
                options = TaskRetryOptions(task_retry_limit=1)
                deferred.defer(update_dungeon_affix_region,
                               dungeon,
                               "current",
                               region,
                               page=page,
                               _retry_options=options)


## end raider.io processing

## data analysis start


## replacements for numpy
def average(data):
    return mean(data)

def mean(data):
    """Return the sample arithmetic mean of data."""
    n = len(data)
    if n < 1:
        return 0
    return sum(data)/float(n) 

def _ss(data):
    """Return sum of square deviations of sequence data."""
    c = mean(data)
    ss = sum((x-c)**2 for x in data)
    return ss

def std(data, ddof=0):
    """Calculates the population standard deviation
    by default; specify ddof=1 to compute the sample
    standard deviation."""
    n = len(data)
    if n < 2:
        return 0
    ss = _ss(data)
    pvar = ss/(n-ddof)
    return pvar**0.5


from math import sqrt
from ckmeans import ckmeans

def look_up_covenants(spec, mode):
    spec_slug = slugify.slugify(unicode(spec))
    mode_slug = slugify.slugify(unicode(mode))
    
    key_slug = "%s-%s" % (spec_slug, mode_slug)
    key = ndb.Key('CovenantStats', key_slug)

    cs = key.get()

    if cs == None:
        return 0, 0, []
    
    data = json.loads(cs.data)
    n_parses = data["n_parses"]
    n_uniques = data["n_uniques"]
    covenants = data["covenants"]

    return n_parses, n_uniques, covenants

def gen_top_covenant_report_for(spec, mode):

    n_parses, n_uniques, covenants = look_up_covenants(spec, mode)
    
    slug = slugify.slugify(unicode(spec))

    # no data
    if len(covenants) < 1:
        return 0, "", {}

    covenant_names = []
    for i in [1, 2, 3, 4]:
        covenant_names += [covenantID_mapping[i]["name"]]        

    top_name = covenants[0][1][0]

    # return n_parses, and then cov dict cov: #
    
    data = {}
    for cc in covenants:
        if cc[1] == []:
            continue
        if cc[1][0] not in covenant_names:
            continue
        cov_name = cc[1][0]
        pct = int(round((float(cc[0])/n_parses)*100))
        data[cov_name] = pct

       
    logging.info("%s %s" % (spec, mode))
    logging.info(data)

    cov_colors  = {}
    cov_colors["Night Fae"] = "#c851ec"
    cov_colors["Kyrian"] = "#a9dcfc" 
    cov_colors["Venthyr"] = "#e02d2d"
    cov_colors["Necrolord"] = "#96b364"

    output = {}
    for k, v in data.iteritems():
        output[k] = [v, slugify.slugify(unicode(k)), cov_colors[k]]


    output["max_cov"] = max(data.iteritems(), key=operator.itemgetter(1))[0]
    logging.info(output)
        

    return n_parses, top_name, output
    

def create_package(name):
    package = {}
    package["name"] = name
    package["slug"] = slugify.slugify(unicode(name))
    return package

def gen_covenants_report():
    report = {}
    report["Tanks"] = []
    report["Healers"] = []
    report["Melee"] = []
    report["Ranged"] = []

    # each report is a list with elements
    # spec - the spec we're looking at
    #   name - pretty name of the spec
    #   slug - slug of the spec
    # mplus - the covenant for m+
    #   name - pretty name of the covenant
    #   slug - slug of the covenant
    # raid - the covenant for raid
    #   name - pretty name of the covenant
    #   slug - slug of the covenant

    

    # how many parses overall for raid + mplus
    
    n_parses = {}
    n_parses["raid"] = 0
    n_parses["mplus"] = 0

    mm_data = {}
    rr_data = {}


    for i, display in enumerate([tanks, healers, melee, ranged]):
        for spec in sorted(display):

            n, mplus, m_data = gen_top_covenant_report_for(spec, "mplus")
            n_parses["mplus"] += n
            mm_data[spec] = m_data
            
            n, raid, r_data = gen_top_covenant_report_for(spec, "raid")
            n_parses["raid"] += n
            rr_data[spec] = r_data
            
            report[role_titles[i]] += [[create_package(spec),
                                        create_package(mplus),
                                        create_package(raid)]]


    return report, n_parses, mm_data, rr_data


# generate a dungeon tier list
def gen_dungeon_tier_list(dungeons_report):

    scores = []

    for k in dungeons_report:
        scores += [float(k[0])]

    if len(dungeons_report) < 6:
        # for some reason we're seeing fewer than 6 dungeons
        # might be early in the week, etc.
        return gen_dungeon_tier_list_small(dungeons_report)
        
    buckets = ckmeans(scores, 6)
   
    added = []

    tiers = {}
    tm = {}
    tm[5] = "S"
    tm[4] = "A"
    tm[3] = "B"
    tm[2] = "C"
    tm[1] = "D"
    tm[0] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = []
    
    for i in range(0, 6):
        for k in dungeons_report:
            if float(k[0]) in buckets[i]:
                if k not in added:
                    if tm[i] not in tiers:
                        tiers[tm[i]] = []
                    tiers[tm[i]] += [k]
                    added += [k]


    # add stragglers to last tier
    for k in dungeons_report:
        if k not in added:
            if tm[0] not in tiers:
                tiers[tm[0]] = []
            tiers[tm[0]] += [k]
            added += [k]

    return render_dungeon_tier_list(tiers, tm)

def render_dungeon_tier_list(tiers, tm):
    dtl = {}
    dtl["S"] = ""
    dtl["A"] = ""
    dtl["B"] = ""
    dtl["C"] = ""
    dtl["D"] = ""
    dtl["F"] = ""

    global dungeon_short_names
    template = env.get_template("dungeon-mini-icon.html")
    
    for i in range(0, 6):
        for k in tiers[tm[i]]:
            rendered = template.render(dungeon_slug = k[4],
                                       dungeon_name = k[1],
                                       dungeon_short_name = dungeon_short_names[k[1]])
            dtl[tm[i]] += rendered
    
    return dtl
    

def icon_spec(dname, prefix="", size=56):
    dslug = slugify.slugify(unicode(dname))
    return '<a href="%s.html"><img src="images/specs/%s.jpg" width="%d" height="%d" title="%s" alt="%s" /><br/>%s</a>' % (prefix+dslug, dslug, size, size, dname, dname, dname)

import pdb

# generate a specs tier list
def gen_spec_tier_list(specs_report, role, prefix="", api=False):
    global role_titles

    scores = []
    for i in range(0, 4):
        for k in specs_report[role_titles[i]]:
            if int(k[3]) < 20: # ignore specs with fewer than 20 runs as they would skew the buckets; we'll add them to F later
                continue
            scores += [float(k[0])]

    if len(scores) < 6: # relax the fewer than 20 rule (early scans early in season)
        scores = []
        for i in range(0, 4):
            for k in specs_report[role_titles[i]]:
                scores += [float(k[0])]
        
    buckets = ckmeans(scores, 6)
            
    added = []

    tiers = {}
    tm = {}
    tm[5] = "S"
    tm[4] = "A"
    tm[3] = "B"
    tm[2] = "C"
    tm[1] = "D"
    tm[0] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = []


    for i in range(0, 6):
        for k in specs_report[role]:
            if len(buckets) > i:
                if float(k[0]) in buckets[i]:
                    if k not in added:
                        tiers[tm[i]] += [k]
                        added += [k]


    # add stragglers to last tier
    for k in specs_report[role]:
        if k not in added:
            tiers[tm[0]] += [k]
            added += [k]

    if api==False:
        dtl = {}
        dtl["S"] = ""
        dtl["A"] = ""
        dtl["B"] = ""
        dtl["C"] = ""
        dtl["D"] = ""
        dtl["F"] = ""


        global spec_short_names
        template = env.get_template("spec-mini-icon.html")
        for i in range(0, 6):
            for k in tiers[tm[i]]:
                rendered = template.render(spec_name = k[1],
                                       spec_short_name = spec_short_names[k[1]],
                                       spec_slug = slugify.slugify(unicode(k[1])))
                dtl[tm[i]] += rendered
    
        return dtl
    else:
        dtl = {}
        dtl["S"] = []
        dtl["A"] = []
        dtl["B"] = []
        dtl["C"] = []
        dtl["D"] = []
        dtl["F"] = []

        for i in range(0, 6):
            for k in tiers[tm[i]]:
                dtl[tm[i]] += [k[1]]
        
        return dtl




def icon_affix(dname, size=28):
    dname = affix_rotation_affixes(dname)
    dslug = slugify.slugify(unicode(dname))
    
    def miniaffix(aname, aslug, size):
        return '<img src="images/affixes/%s.jpg" width="%d" height="%d" title="%s" alt="%s" />' % (aslug, size, size, aname, aname)
    
    affixen = dname.split(", ")
    output = []
    
    for af in affixen:
        afname = af
        afslug = slugify.slugify(af)
        output += [miniaffix(afname, afslug, size=size)]
        
    output_string = output[0]
    output_string += output[1]
    output_string += output[2]
    output_string += output[3]
       
    return output_string


def render_affix_tier_list_api(tiers, tm):
    dtl = {}
    dtl["S"] = []
    dtl["A"] = []
    dtl["B"] = []
    dtl["C"] = []
    dtl["D"] = []
    dtl["F"] = []

    for i in range(0, 6):
        for k in tiers[tm[i]]:
            dtl[tm[i]] += [k[1]]
    
    return dtl


def render_affix_tier_list(tiers, tm, api=False):
    if api==True:
        return render_affix_tier_list_api(tiers, tm)
    
    dtl = {}
    dtl["S"] = ""
    dtl["A"] = ""
    dtl["B"] = ""
    dtl["C"] = ""
    dtl["D"] = ""
    dtl["F"] = ""

    template = env.get_template('affix-mini-icon.html')
    template_all = env.get_template('affixes-mini-icons.html')
    for i in range(0, 6):
        for k in tiers[tm[i]]:
            affixen = k[1].split(", ")
            current_set = current_affixes()
            this_set = k[1]
            affix_set = ""
            
            # @@ link issue -- we're pulling in (A) (B) during s4
            slug_link = slugify.slugify(k[1][:-4])
            if current_set in this_set:
                slug_link = "index"

            
            for each_affix in affixen:
                rendered = template.render(affix_slug = slugify.slugify(each_affix),
                                           affix_name = each_affix)
                affix_set += rendered



            dtl[tm[i]] += template_all.render(affix_link = slug_link,
                                              affix_set = affix_set)
    
    return dtl

# todo: affix tier list (how do affixes compare with each other)
# have this show on all affixes?
# new: generate a dungeon tier list
def gen_affix_tier_list(affixes_report, api=False):
    if len(affixes_report) < 6:
        return gen_affix_tier_list_small(affixes_report, api=api)

    # ckmeans
    scores = []
    for k in affixes_report:
        scores += [float(k[0])]

    buckets = ckmeans(scores, 6)
    added = []

    tiers = {}
    tm = {}
    tm[5] = "S"
    tm[4] = "A"
    tm[3] = "B"
    tm[2] = "C"
    tm[1] = "D"
    tm[0] = "F"


    for i in range(0, 6):
        tiers[tm[i]] = []
    
    for i in range(0, 6):
        for k in affixes_report:
            if float(k[0]) in buckets[i]:
                if k not in added:
                    if tm[i] not in tiers:
                        tiers[tm[i]] = []
                    tiers[tm[i]] += [k]
                    added += [k]

        # add stragglers to last tier
    for k in affixes_report:
        if k not in added:
            if tm[0] not in tiers:
                tiers[tm[0]] = []
            tiers[tm[0]] += [k]
            added += [k]

    return render_affix_tier_list(tiers, tm, api=api)    
    
# use this if there are fewer than 6 affixes scanned
# since we can't cluster into 6 with uh, fewer than 6
def gen_affix_tier_list_small(affixes_report, api=False):
   
    # super simple tier list -- figure out the max and the min, and then bucket tiers
    cimax = -1
    cimin = -1

    for k in affixes_report:       
        if cimax == -1:
            cimax = float(k[0])
        if cimin == -1:
            cimin = float(k[0])
        if float(k[0]) < cimin:
            cimin = float(k[0])
        if float(k[0]) > cimax:
            cimax = float(k[0])

    cirange = cimax - cimin
    cistep = cirange / 6

    added = []

    tiers = {}
    tm = {}
    tm[0] = "S"
    tm[1] = "A"
    tm[2] = "B"
    tm[3] = "C"
    tm[4] = "D"
    tm[5] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = []
    
    for i in range(0, 6):
        for k in affixes_report:
            if float(k[0]) >= (cimax-cistep*(i+1)):
                if k not in added:
                    if tm[i] not in tiers:
                        tiers[tm[i]] = []
                    tiers[tm[i]] += [k]
                    added += [k]


    # add stragglers to last tier
    for k in affixes_report:
        if k not in added:
            if tm[5] not in tiers:
                tiers[tm[5]] = []
            tiers[tm[5]] += [k]
            added += [k]
    
    return render_affix_tier_list(tiers, tm, api=api)

# use this if there are fewer than 6 dungeons scanned
# since we can't cluster into 6 with uh, fewer than 6
def gen_dungeon_tier_list_small(dungeons_report):
   
    # super simple tier list -- figure out the max and the min, and then bucket tiers
    cimax = -1
    cimin = -1

    for k in dungeons_report:       
        if cimax == -1:
            cimax = float(k[0])
        if cimin == -1:
            cimin = float(k[0])
        if float(k[0]) < cimin:
            cimin = float(k[0])
        if float(k[0]) > cimax:
            cimax = float(k[0])

    cirange = cimax - cimin
    cistep = cirange / 6

    added = []

    tiers = {}
    tm = {}
    tm[0] = "S"
    tm[1] = "A"
    tm[2] = "B"
    tm[3] = "C"
    tm[4] = "D"
    tm[5] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = []
    
    for i in range(0, 6):
        for k in dungeons_report:
            if float(k[0]) >= (cimax-cistep*(i+1)):
                if k not in added:
                    if tm[i] not in tiers:
                        tiers[tm[i]] = []
                    tiers[tm[i]] += [k]
                    added += [k]


    # add stragglers to last tier
    for k in dungeons_report:
        if k not in added:
            if tm[5] not in tiers:
                tiers[tm[5]] = []
            tiers[tm[5]] += [k]
            added += [k]
    
    return render_dungeon_tier_list(tiers, tm)


# for background on the analytical approach of using the lower bound of a confidence interval:
# https://www.evanmiller.org/how-not-to-sort-by-average-rating.html
# https://www.evanmiller.org/ranking-items-with-star-ratings.html 

def construct_analysis(counts, sort_by="lb_ci", limit=100):
    overall = []
    all_data = []
    for name, runs in counts.iteritems():
        for r in runs:
            all_data += [r.score]

    master_stddev = 1
    if len(all_data) >= 2:
        master_stddev = std(all_data, ddof=1)
    
       
    for name, runs in counts.iteritems():
        data = []
        max_found = 0 
        max_id = ""
        max_level = 0
        all_runs = []
        for r in runs:
            data += [r.score]
            all_runs += [[r.score, r.mythic_level, r.keystone_run_id]]
            if r.score >= max_found:
                max_found = r.score
                max_id = r.keystone_run_id
                max_level = r.mythic_level
        n = len(data)
        if n == 0:
            overall += [[name, 0, 0, n, [0, 0], [0, "", 0], []]]
            continue
        mean = average(data)
        if n <= 1:
            overall += [[name, mean, 0, n, [0, 0], [max_found, max_id, max_level], all_runs]]
            continue


        # filter to top 100
        sorted_data = sorted(data, reverse=True)
        sorted_data = sorted_data[:limit]
                
        stddev = std(sorted_data, ddof=1)
        sorted_mean = average(sorted_data)
        sorted_n = len(sorted_data)
        t_bounds = t_interval(n)
        ci = [sorted_mean + critval * master_stddev / sqrt(sorted_n) for critval in t_bounds]
        
#        stddev = std(data, ddof=1)
#        t_bounds = t_interval(n)
#        ci = [mean + critval * master_stddev / sqrt(n) for critval in t_bounds]
        maxi = [max_found, max_id, max_level]
        all_runs = sorted(all_runs, key=lambda x: x[0], reverse=True)
        overall += [[name, mean, stddev, n, ci, maxi, all_runs]]

    overall = sorted(overall, key=lambda x: x[4][0], reverse=True)        
    if sort_by == "max":
        overall = sorted(overall, key=lambda x: x[5][0], reverse=True)            
    
    return overall

# construct_analysis for raid, which has per encounter lists of key metrics for a given spec
def construct_analysis_raid(spec_counts):
    counts = spec_counts
    
    overall = {}
    all_data = []

    for encounter, metrics in counts.iteritems():
        for m in metrics:
            all_data += [m]
    
    master_stddev = 1
    if len(all_data) >= 2:
        master_stddev = std(all_data, ddof=1)
       
    for encounter, metrics in counts.iteritems():
        data = []
        for m in metrics:
            data += [m]

        n = len(data)
        if n == 0:
            overall[encounter] = [0, 0, 0, []]
            continue
        mean = average(data)
        if n <= 1:
            overall[encounter] = [mean, n, mean, data]
            continue

        # filter to top 100
        sorted_data = sorted(data, reverse=True)
        sorted_data = sorted_data[:100]
                
        stddev = std(sorted_data, ddof=1)
        sorted_mean = average(sorted_data)
        sorted_n = len(sorted_data)
        t_bounds = t_interval(n)
        ci = [sorted_mean + critval * master_stddev / sqrt(sorted_n) for critval in t_bounds]
        
#        stddev = std(data, ddof=1)
#        t_bounds = t_interval(n)
#        ci = [mean + critval * master_stddev / sqrt(n) for critval in t_bounds]
        # lbci, n, mean, data
        overall[encounter]= [ci[0], n, mean, data]

    return overall


# for pvp
# we filter to the _top 100_ for lb_ci
def construct_analysis_pvp(spec_counts):
    counts = spec_counts
    
    overall = {}
    all_data = []
    
    for specs, metrics in counts.iteritems():
        for m in metrics:
            all_data += [m]
    
    master_stddev = 1
    if len(all_data) >= 2:
        master_stddev = std(all_data, ddof=1)
       
    for spec, metrics in counts.iteritems():
        data = []
        for m in metrics:
            data += [m]

        n = len(data)
        if n == 0:
            overall[spec] = [0, 0, 0, []]
            continue
        mean = average(data)
        if n <= 1:
            overall[spec] = [mean, n, mean, data]
            continue


        # filter to top 100
        sorted_data = sorted(data, reverse=True)
        sorted_data = sorted_data[:100]
                
        stddev = std(sorted_data, ddof=1)
        sorted_mean = average(sorted_data)
        sorted_n = len(sorted_data)
        t_bounds = t_interval(n)
        ci = [sorted_mean + critval * master_stddev / sqrt(sorted_n) for critval in t_bounds]
        # lbci, n, mean, data
        overall[spec] = [ci[0], n, mean, data]

    return overall


# build a spec report for raid
# build for each boss, and overall
# save this to the db for each spec

# read from the db
def gen_raid_spec_analysis(difficulty=MAX_RAID_DIFFICULTY):
    # raid_generate_counts is now a cached call
    raid_counts, raid_max_found, raid_max_link = raid_generate_counts(difficulty=difficulty)
    
    analysis = {}
    lb_ci_spec = {}

    lb_ci_spec["all"] = {}
    
    for s in specs:
        analysis[s] = construct_analysis_raid(raid_counts[s])

        scores = []
        all_scores = []
        n_scores = 0
        
        for e in raid_encounters:
            if e not in raid_ignore: # ignore certain encounters for the tier list
                all_scores += analysis[s][e][3]
                scores += [analysis[s][e][0]]
                n_scores += analysis[s][e][1]
            if e not in lb_ci_spec:
                lb_ci_spec[e] = {}
            lb_ci_spec[e][s] = [analysis[s][e][0], analysis[s][e][1], analysis[s][e][2]]

        # using the average of the lbcis, n, mean of scores
        lb_ci_spec["all"][s] = [average(scores), n_scores, mean(all_scores)]

    return lb_ci_spec, raid_max_found, raid_max_link    

def gen_raid_specs_role_package(encounter, difficulty=MAX_RAID_DIFFICULTY):
    global role_titles, specs

    # gen_raid_spec analysis uses the memoized raid_generate_counts
    lb_ci_spec, raid_max_found, raid_max_link = gen_raid_spec_analysis(difficulty=difficulty)
    encounter_overall = lb_ci_spec[encounter]

    role_package = {}
    stats = {}

    # go through all the specs, grouped by role
    for i, display in enumerate([tanks, healers, melee, ranged]):
        role_score = []
        stats[role_titles[i]] = {}

        n_runs = 0
        ids = []

        for k in display: # for spec k
            rmf = 0
            rml = ""

            if encounter != "all":
                rmf = raid_max_found[k][encounter]
                rml = raid_max_link[k][encounter]
            else:
                maxf = 0
                maxe = ""

                for ee, mm in raid_max_found[k].iteritems():
                    if mm > maxf:
                        maxe = ee
                        maxf = mm

                if maxe != "":
                    rmf = raid_max_found[k][maxe]
                    rml = raid_max_link[k][maxe]                
            
            role_score += [[str("%.2f" % encounter_overall[k][0]), # lower bound of ci
                            str(k), # name of the spec
                            str("%.2f" % encounter_overall[k][2]), # mean
                            str("%d" % encounter_overall[k][1]).rjust(4), # n
                            slugify.slugify(unicode(str(k))), # slug name
                            str("%.2f" % rmf), # maximum run
                            rml, # id of the maximum run
            ]]
            n_runs += encounter_overall[k][1] # since it's just parses, can add

        stats[role_titles[i]]["n"] = n_runs

        # sort role_score by lb_ci
        role_score = sorted(role_score, key=lambda x: x[0], reverse=True)
        role_package[role_titles[i]] = role_score

    return role_package, stats
    

# generate a specs tier list
# placeholder code for now
def gen_raid_spec_tier_list(specs_report, role, encounter_slug="all", prefix="", difficulty=MAX_RAID_DIFFICULTY):
    global role_titles

    # for raid, we compare tanks to tanks
    # compare healers to healers
    # compare dps to dps (grouping melee + ranged)

    compare_with = {}
    compare_with["Tanks"] = ["Tanks"]
    compare_with["Healers"] = ["Healers"]
    compare_with["Melee"] = ["Melee", "Ranged"]
    compare_with["Ranged"] = ["Melee", "Ranged"]
    
    scores = []
    for i in range(0, 4):
        if role_titles[i] not in compare_with[role]:
            continue
        for k in specs_report[role_titles[i]]:
            if int(k[3]) < 20: # ignore specs with fewer than 20 parses as they would skew the buckets; we'll add them to F later
                continue

            scores += [float(k[0])]

    if len(scores) < 6: # relax the fewer than 20 rule (early scans)
        scores = []
        for i in range(0, 4):
            if role_titles[i] not in compare_with[role]:
                continue            
            for k in specs_report[role_titles[i]]:
                scores += [float(k[0])]
        
    buckets = ckmeans(scores, 6)
            
    added = []

    tiers = {}
    tm = {}
    tm[5] = "S"
    tm[4] = "A"
    tm[3] = "B"
    tm[2] = "C"
    tm[1] = "D"
    tm[0] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = []


    for i in range(0, 6):
        for k in specs_report[role]:
            if len(buckets) > i:
                if float(k[0]) in buckets[i]:
                    if k not in added:
                        tiers[tm[i]] += [k]
                        added += [k]


    # add stragglers to last tier
    for k in specs_report[role]:
        if k not in added:
            tiers[tm[0]] += [k]
            added += [k]
    
    dtl = {}
    dtl["S"] = ""
    dtl["A"] = ""
    dtl["B"] = ""
    dtl["C"] = ""
    dtl["D"] = ""
    dtl["F"] = ""

    global spec_short_names
    template = env.get_template("raid-spec-mini-icon.html")
    for i in range(0, 6):
        for k in tiers[tm[i]]:
            rendered = template.render(spec_name = k[1],
                                       spec_short_name = spec_short_names[k[1]],
                                       spec_slug = slugify.slugify(unicode(k[1])),
                                       encounter_slug = encounter_slug,
                                       difficulty = difficulty)
            dtl[tm[i]] += rendered
    
    return dtl   


def gen_pvp_specs_role_package(mode):
    global role_titles, specs

    role_package = {}
    stats = {}

    # go through all the specs, grouped by role
    for i, display in enumerate([tanks, healers, melee, ranged]):
        role_score = []
        stats[role_titles[i]] = {}

        n_runs = 0

        for k in display: # for spec k

            key_slug = "%s-%s" % (slugify.slugify(unicode(k)), mode)
            pc = ndb.Key('PvPCounts', key_slug).get()

            data = json.loads(pc.data)
            
            role_score += [[str("%.2f" % data["lb_ci"]), # lower bound of ci
                            str(k), # name of the spec
                            str("%.2f" % data["mean"]), # mean
                            str("%d" % data["n"]), # n
                            slugify.slugify(unicode(str(k))), # slug name
                            str("%d" % data["max"]), # maximum rating
                            "", # no links for pvp
            ]]
            n_runs += data["n"] # total number of specs at rating

        stats[role_titles[i]]["n"] = n_runs

        # sort role_score by lb_ci
        role_score = sorted(role_score, key=lambda x: x[0], reverse=True)
        role_package[role_titles[i]] = role_score

    return role_package, stats
    

# generate a specs tier list
# placeholder code for now
def gen_pvp_spec_tier_list(specs_report, role, mode, api=False, prefix=""):
    global role_titles
    

    # for pvp we compare everyone to everyone (just using rating)
    
    scores = []
    for i in range(0, 4):
        for k in specs_report[role_titles[i]]:
            if int(k[3]) < 20: # ignore specs with fewer than 20 parses as they would skew the buckets; we'll add them to F later
                continue

            scores += [float(k[0])]

    if len(scores) < 6: # relax the fewer than 20 rule (early scans)
        scores = []
        for i in range(0, 4):
            for k in specs_report[role_titles[i]]:
                scores += [float(k[0])]
        
    buckets = ckmeans(scores, 6)
            
    added = []

    tiers = {}
    tm = {}
    tm[5] = "S"
    tm[4] = "A"
    tm[3] = "B"
    tm[2] = "C"
    tm[1] = "D"
    tm[0] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = []


    for i in range(0, 6):
        for k in specs_report[role]:
            if len(buckets) > i:
                if float(k[0]) in buckets[i]:
                    if k not in added:
                        tiers[tm[i]] += [k]
                        added += [k]


    # add stragglers to last tier
    for k in specs_report[role]:
        if k not in added:
            tiers[tm[0]] += [k]
            added += [k]

    if api==False:
        dtl = {}
        dtl["S"] = ""
        dtl["A"] = ""
        dtl["B"] = ""
        dtl["C"] = ""
        dtl["D"] = ""
        dtl["F"] = ""

        global spec_short_names
        template = env.get_template("pvp-spec-mini-icon.html")
        for i in range(0, 6):
            for k in tiers[tm[i]]:
                rendered = template.render(spec_name = k[1],
                                           spec_short_name = spec_short_names[k[1]],
                                           spec_slug = slugify.slugify(unicode(k[1])))
                dtl[tm[i]] += rendered
    
        return dtl
    else:
        dtl = {}
        dtl["S"] = []
        dtl["A"] = []
        dtl["B"] = []
        dtl["C"] = []
        dtl["D"] = []
        dtl["F"] = []

        for i in range(0, 6):
            for k in tiers[tm[i]]:
                dtl[tm[i]] += [k[1]]
        
        return dtl        




## end data analysis

## getting data out and into counts

# generate counts -- this is used by construct_analysis to do the statistical analysis
def generate_counts(affixes="All Affixes", dungeon="all", spec="all"):
    global dungeons, regions, specs, last_updated, RIO_MAX_PAGE

    affixes_to_get = [affixes]
    if affixes == "All Affixes":
        affixes_to_get = known_affixes()

    dungeon_counts = {}
    spec_counts = {}
    set_counts = {}
    th_counts = {} # tank healer
    dps_counts = {} # just dps
    affix_counts = {} # compare affixes to each other (
    dung_spec_counts = {} # spec per dungeons

    for s in specs:
        spec_counts[s] = []
    
    for d in dungeons:
        dung_spec_counts[d] = {}        
        for s in specs:
            dung_spec_counts[d][s] = []

    for affix in affixes_to_get:
        affixes_slug = slugify.slugify(unicode(affix))
        for region in regions:
            for dung in dungeons:
                for page in range(0, RIO_MAX_PAGE):
                    dungeon_slug = slugify.slugify(unicode(dung))
                    key_string = dungeon_slug + "-" + affixes_slug + "-" + region + "-" + str(page)
                    key = ndb.Key('DungeonAffixRegion',
                                  key_string)

                    dar = key.get()

                    if dar == None:
                        continue

                    if last_updated == None:
                        last_updated = dar.last_updated
                    if dar.last_updated > last_updated:
                        last_updated = dar.last_updated

                    for run in dar.runs:
                        if run.mythic_level < MIN_KEY_LEVEL: # don't count runs under a +16
                            continue
                        
                        if dung not in dungeon_counts:
                            dungeon_counts[dung] = []
                        dungeon_counts[dung] += [run]

                        if affix not in affix_counts:
                            affix_counts[affix] = []
                        affix_counts[affix] += [run]

                        # all this is spec / dungeon / comp breakdown
                        if dungeon == "all" or dung == dungeon:
                                if spec == "all":
                                    if canonical_order(run.roster) not in set_counts:
                                        set_counts[canonical_order(run.roster)] = []
                                    set_counts[canonical_order(run.roster)] += [run]

                                    if canonical_order(run.roster)[:2] not in th_counts:
                                        th_counts[canonical_order(run.roster)[:2]] = []
                                    th_counts[canonical_order(run.roster)[:2]] += [run]

                                    if canonical_order(run.roster)[-3:] not in dps_counts:
                                        dps_counts[canonical_order(run.roster)[-3:]] = []
                                    dps_counts[canonical_order(run.roster)[-3:]] += [run]

                                    for ch in run.roster:
                                        spec_counts[ch] += [run]
                                        dung_spec_counts[dung][ch] += [run]
                            
    return dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts


# for constructing the raid tier list
# we'll have 3 -- all dps against each other (melee and ranged)
# all tanks against each other (since we only have dps for tanks and tank dps is so much lower)
# all healers against each other, based on hps
def process_raid_generate_counts_spec_encounter(spec, encounter, difficulty=MAX_RAID_DIFFICULTY):
    counts = []
    
    # only consider the difficulty (we'll need to call this twice, once each for Heroic and Mythic)
    wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec,
                                       SpecRankingsRaid.difficulty==difficulty,
                                       SpecRankingsRaid.encounter==encounter)
    results = wcl_query.fetch()

    rankings = []

    max_found = 0
    max_link = ""

    # include max link for each encounter
    
    global last_updated
    for k in results:
        if last_updated == None:
            last_updated = k.last_updated
        if k.last_updated > last_updated:
            last_updated = k.last_updated    

        latest = json.loads(k.rankings)

        for k in latest:
            metric = float(k["total"])/1000
            counts += [metric]

            if metric > max_found:
                max_found = metric
                max_link = k["reportID"]
            

    
    data = {}
    data["counts"] = counts
    data["max_found"] = max_found
    data["max_link"] = max_link

    spec_slug = slugify.slugify(unicode(spec))
    encounter_slug = slugify.slugify(unicode(encounter))
    difficulty_slug = slugify.slugify(unicode(difficulty))

    key_slug = "%s-%s-%s" % (spec_slug, encounter_slug, difficulty_slug)
    key = ndb.Key('RaidCounts', key_slug)
    
    raid_counts = RaidCounts(id = key_slug,
                             difficulty = difficulty,
                             spec = spec,
                             encounter = encounter,
                             data = json.dumps(data),
                             last_updated = last_updated)

    raid_counts.put()


def process_generate_raid_counts():
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    for d in difficulties:
        for s in specs:
            for k, v in raid_encounters.iteritems():
                options = TaskRetryOptions(task_retry_limit = 1)        
                deferred.defer(process_raid_generate_counts_spec_encounter, s, k, d,
                               _retry_options=options)

def raid_generate_counts_spec_encounter(spec, encounter, difficulty=MAX_RAID_DIFFICULTY):
    # read from the db
    spec_slug = slugify.slugify(unicode(spec))
    encounter_slug = slugify.slugify(unicode(encounter))
    difficulty_slug = slugify.slugify(unicode(difficulty))

    key_slug = "%s-%s-%s" % (spec_slug, encounter_slug, difficulty_slug)
    key = ndb.Key('RaidCounts', key_slug)
    
    rc = key.get()
    if rc == None:
        return [0], 0.0, ""
    data = json.loads(rc.data)
    counts = data["counts"]
    max_found = data["max_found"]
    max_link = data["max_link"]

    return counts, max_found, max_link


def raid_generate_counts_spec(spec, difficulty=MAX_RAID_DIFFICULTY):
    counts = {}
    max_found = {}
    max_link = {}
    for k, v in raid_encounters.iteritems():
        counts[k], max_found[k], max_link[k] = raid_generate_counts_spec_encounter(spec, k, difficulty=difficulty)
    return counts, max_found, max_link
        

def raid_generate_counts(difficulty=MAX_RAID_DIFFICULTY):
    counts = {}
    max_found = {}
    max_link = {}    
    for s in specs:
        counts[s], max_found[s], max_link[s] = raid_generate_counts_spec(s, difficulty=difficulty)

    return counts, max_found, max_link
       
        

# known affixes
known_affixes_save = []

def known_affixes():
#    global known_affixes_save
#    if known_affixes_save != []:
#        return known_affixes_save # poor man's caching

    known_affixes_save = []
    affix_query = KnownAffixes.query().order(KnownAffixes.first_seen)
    results = affix_query.fetch()
    for k in results:
        if k.affixes not in known_affixes_save:
            if k.affixes != None:
                known_affixes_save += [k.affixes]

    return known_affixes_save

def known_affixes_links(prefix="", use_index=True):
    known_affixes_list = known_affixes()
    known_affixes_report = []
    known_affixes_report += [["All Affixes", prefix+"all-affixes", ""]]
    for k in known_affixes_list:
        if use_index:
            if k == current_affixes():
                known_affixes_report += [[affix_rotation_affixes(k), prefix+"index",
                                          icon_affix(k)]]
            else:
                known_affixes_report += [[affix_rotation_affixes(k), prefix+slugify.slugify(unicode(k)),
                                          icon_affix(k)]]
            
        else:
            known_affixes_report += [[affix_rotation_affixes(k), prefix+slugify.slugify(unicode(k)),
                                      icon_affix(k)]]
            
    known_affixes_report.reverse()
    return known_affixes_report

def known_dungeon_links(affixes_slug, prefix=""):
    known_dungeon_list = dungeons

    known_dungeon_report = []

    for k in known_dungeon_list:
        known_dungeon_report += [[k, prefix+slugify.slugify(unicode(k))+"-" + affixes_slug]]
            
    return known_dungeon_report

def known_specs_links(prefix=""):
    global tanks, healers, melee, ranged
    known_specs_report = []
    for d in [sorted(tanks), sorted(healers), sorted(melee), sorted(ranged)]:
        for k in d:
            known_specs_report += [[k, slugify.slugify(unicode(k)), icon_spec(k, size=22)]]

    return known_specs_report

def known_specs_subset_links(subset, prefix=""):
    known_specs_report = []
    for d in [sorted(subset)]:
        for k in d:
            known_specs_report += [[k, slugify.slugify(unicode(k)), icon_spec(k, size=22)]]

    return known_specs_report

        
def current_affixes():
    pull_query = KnownAffixes.query().order(-KnownAffixes.last_seen, -KnownAffixes.first_seen)
    current_affixes_save = pull_query.fetch(1)[0].affixes
    
    return current_affixes_save



# generate pvp counts and store them
# we want counts, n, max_rating
def process_pvp_counts_for_a_mode(actual_mode):
    global pvp_regions, pvp_modes, specs

    # for each spec
    raw_counts = {}

    mode_list = [actual_mode]

    for mode in mode_list:
        for region in pvp_regions:
            key_slug = "%s-%s" % (region, mode)
            key = ndb.Key('PvPLadderStats', key_slug)
            data = json.loads((key.get()).data)

            for entry in data:
                full_spec_name = "%s %s" % (entry["active_spec"], entry["character_class"])
                if full_spec_name not in specs:
                    continue
                if full_spec_name not in raw_counts:
                    raw_counts[full_spec_name] = []

                raw_counts[full_spec_name] += [entry["rating"]]


    # overall is a dict per spec
    # each spec has a list
    # [ lbci, n, average, [list with actual data]]
    overall = construct_analysis_pvp(raw_counts)

    for s in specs:
        key_slug = "%s-%s" % (slugify.slugify(unicode(s)), actual_mode)
        data = {}
        if s in overall:
            data["lb_ci"] = overall[s][0]
            data["n"] = overall[s][1]
            data["mean"] = overall[s][2]
            data["max"] = max(overall[s][3])
            data["raw"] = overall[s][3]
        else:
            data["lb_ci"] = 0
            data["n"] = 0
            data["mean"] = 0
            data["max"] = 0
            data["raw"] = []

        pc = PvPCounts(id = key_slug,
                       spec = s,
                       mode = actual_mode,
                       data = json.dumps(data))
        pc.put()


def process_pvp_counts_overall():
    global pvp_regions, pvp_modes, specs


    max_rating = {}
    for mode in pvp_modes:
        for s in specs:
            key_slug = "%s-%s" % (slugify.slugify(unicode(s)), mode)
            pcc = ndb.Key('PvPCounts', key_slug).get()
            data = json.loads(pcc.data)

            if mode not in max_rating:
                max_rating[mode] = data["max"]
                
            if data["max"] > max_rating[mode]:
                max_rating[mode] = data["max"]
    
    
    for s in specs:
        _lbci = []
        _n = []
        _mean = []
        _max = []
        

        for mode in pvp_modes:
            key_slug = "%s-%s" % (slugify.slugify(unicode(s)), mode)
            pcc = ndb.Key('PvPCounts', key_slug).get()
            data = json.loads(pcc.data)
            
            
            _lbci += [float(data["lb_ci"])/max_rating[mode]*3000]
            _n += [data["n"]]
            _mean += [data["mean"]]
            _max += [data["max"]]


        data = {}
        data["lb_ci"] = average(_lbci)
        data["n"] = sum(_n)
        data["mean"] = average(_mean)
        data["max"] = max(_max)

        key_slug = "%s-%s" % (slugify.slugify(unicode(s)), "all")
        pc = PvPCounts(id = key_slug,
                       spec = s,
                       mode = "all",
                       data = json.dumps(data))
        pc.put()
        
        
def process_pvp_counts():
    global pvp_modes
    modes_to_process =  []
    modes_to_process += pvp_modes
    
    for mode in modes_to_process:
        options = TaskRetryOptions(task_retry_limit=1)        
        deferred.defer(process_pvp_counts_for_a_mode, mode,
                       _retry_options=options)

    options = TaskRetryOptions(task_retry_limit=1)        
    deferred.defer(process_pvp_counts_overall,
                   _retry_options=options)        
        


## end getting data out into counts



## html generation start

##   generating common reports

def affix_rotation_affixes(affixes):
    global affix_rotation_weeks
    if affixes in affix_rotation_weeks:
        return affixes + " (%s)" % affix_rotation_weeks[affixes]
    return affixes

# given a list of affixes, return a pretty affix string
# <img><img><img><img> Affix1, Affix2, Affix3, Affix4
def pretty_affixes(affixes, size=16, no_text=False):
    if affixes=="All Affixes":
        return "All Affixes"

    output_string = ""
    if no_text:
        output_string = icon_affix(affixes, size=size)
    else:
        output_string = icon_affix(affixes, size=size) + " %s" % affix_rotation_affixes(affixes)
    return output_string
        

def canonical_order(s):
    # given a list, return a tuple in canonical order
    output = []
    ta = []
    he = []
    me = []
    ra = []

    for c in s:
        if c in tanks:
            ta += [c]
        if c in healers:
            he += [c]
        if c in melee:
            me += [c]
        if c in ranged:
            ra += [c]

    output += sorted(ta) + sorted(he) + sorted(me) + sorted(ra)
    return tuple(output)

def pretty_set(s):
    output_string = ""
    for k in s:
        output_string += "<td class=\"comp %s\">%s</td>" % (k, k)
    return output_string

def gen_set_report(set_counts):
    set_overall = construct_analysis(set_counts, sort_by="max")

    set_output = []
    for x in set_overall:
        if x[3] <= 1:
            continue
        set_output += [[str("%.2f" % x[4][0]),
                            pretty_set(x[0]),
                            str("%.2f" % x[1]),
                            str(x[3]),
                            str("%.2f" % x[5][0]), # maximum run
                            x[5][1],
                            x[5][2], # level of the max run
                            x[6], # all runs info
                        ]]

    return set_output[:50]

def gen_dungeon_report(dungeon_counts):
    # use a higher limit for dungeons
    dungeons_overall = construct_analysis(dungeon_counts, limit=400)

    stats = {}

    min_key = None
    max_key = None
    n_runs = 0 
    
    dungeon_output = []
    for x in dungeons_overall:

        dungeon_output += [[str("%.2f" % x[4][0]),
                            x[0],
                            str("%.2f" % x[1]),
                            str(x[3]),
                            slugify.slugify(unicode(x[0])),
                            str("%.2f" % x[5][0]), # maximum run
                            x[5][1], # id of the maximum run
                            x[5][2], # level of the max run
                            x[6], # all runs info
                            ]]

        n_runs += len(x[6])

        for k in x[6]:
            if min_key == None:
                min_key = k[1]
            else:
                if min_key > k[1]:
                    min_key = k[1]

        if max_key == None:
            max_key = x[5][2]
        else:
            if max_key < x[5][2]:
                max_key = x[5][2]


    stats["min"] = min_key
    stats["max"] = max_key
    stats["n"] = n_runs
    
    return dungeon_output, stats

def gen_affix_report(affix_counts):
    affixes_overall = construct_analysis(affix_counts, limit=3200) # look at all runs for affixes
    
    stats = {}

    min_key = None
    max_key = None
    n_runs = 0     
    
    affix_output = []
    for x in affixes_overall:

        affix_output += [[str("%.2f" % x[4][0]),
                            affix_rotation_affixes(x[0]),
                            str("%.2f" % x[1]),
                            str(x[3]),
                            slugify.slugify(unicode(x[0])),
                            str("%.2f" % x[5][0]), # maximum run
                            x[5][1], # id of the maximum run
                            x[5][2], # level of the max run
                            x[6], # all runs info
                            ]]

        n_runs += len(x[6])

        for k in x[6]:
            if min_key == None:
                min_key = k[1]
            else:
                if min_key > k[1]:
                    min_key = k[1]

        if max_key == None:
            max_key = x[5][2]
        else:
            if max_key < x[5][2]:
                max_key = x[5][2]


    stats["min"] = min_key
    stats["max"] = max_key
    stats["n"] = n_runs

    return affix_output, stats

def gen_spec_report(spec_counts):
    global role_titles, specs

    role_package = {}
    stats = {}

    spec_overall = construct_analysis(spec_counts)

    for i, display in enumerate([tanks, healers, melee, ranged]):
        role_score = []
        stats[role_titles[i]] = {}

        min_key = None
        max_key = None
        n_runs = 0
        ids = []
        
        for k in sorted(spec_overall, key=lambda x: x[4][0], reverse=True):
            if k[0] in display:
                role_score += [[str("%.2f" % k[4][0]), # lower bound of ci
                                str(k[0]), # name
                                str("%.2f" % k[1]), # mean
                                str("%d" % k[3]).rjust(4), # n
                                slugify.slugify(unicode(str(k[0]))), # slug name
                                str("%.2f" % k[5][0]), # maximum run
                                k[5][1], # id of the maximum run
                                k[5][2], # level of the max run
                                k[6], # all runs info
                                ]]
                for j in k[6]:
                    ids += [j[2]]
                        
                for j in k[6]:
                    if min_key == None:
                        min_key = j[1]
                    else:
                        if min_key > j[1]:
                            min_key = j[1]

                if max_key == None:
                    max_key = k[5][2]
                else:
                    if max_key < k[5][2]:
                        max_key = k[5][2]

        n_runs = len(set(ids))

        stats[role_titles[i]]["min"] = min_key
        stats[role_titles[i]]["max"] = max_key
        stats[role_titles[i]]["n"] = n_runs
                
        role_package[role_titles[i]] = role_score
    return role_package, stats


# this exists to deal with specs that are only good in one dungeon
# or that appear only good -- e.g. fire mages going frost for the first pull of TD
# or frost dks going unholy for the first pull for junkyard

# instead of using an lb_ci for all runs that are in our top set
# we instead take an average of the lb_ci for each dungeon
# this reduces the prominence of 'first pull specs'
# since they tend to be concentrated in a single dungeon and aren't applicable everywhere
def gen_dung_spec_report(dung_spec_counts, spec_counts):
    global specs, dungeons
    
    # start with the normal spec_report
    role_package, stats = gen_spec_report(spec_counts)

    # look at each dungeon for each spec --
    # basically construct analysis on each, then average, including 0s
    per_dungeon_overall = {}
    for k, v in dung_spec_counts.iteritems():
        per_dungeon_overall[k] = construct_analysis(v)

    # for each spec, go through and grab the lb_cis for each dungeon
    per_spec_lb_ci = {}
    for s in specs:
        per_spec_lb_ci[s] = []
        for d in dungeons:
            for k in per_dungeon_overall[d]:
                if k[0] == s:
                    per_spec_lb_ci[s] += [k[4][0]]


    # recalculate CI based on the as the average of dungeon and adjust the role package
    # we'll be adjusting [0] of the rolepackage, which is "%.2f" % lb_ci 
    for k, v in role_package.iteritems():
        for rp in v:
            mean = average(per_spec_lb_ci[rp[1]])

            # we're modifying role_package directly here
            rp[0] = "%.2f" % mean

    # lastly, we need to resort role package within each set
    for k, v in role_package.iteritems():
        role_package[k] = sorted(v, key=lambda x: float(x[0]), reverse=True)
    
    return role_package, stats


# wcl parsing starts here
def wcl_parse(rankings, extractor, is_sorted=True, is_aggregated=True, only_use_ids=False, flatten=False):
    groupings = {}
    map_name_id_icon = []
    metadata = {}

    # go through each element in rankings
    # and use extractor to pull out what we want to focus on
    # and then add it to groupings
    # also, build out a map of name to -> id icon for each element
    for k in rankings:
        # extractor pulls out the elements we want to use
        names_in_set, name_id_icons = extractor(k)
        map_name_id_icon += name_id_icons

        add_this = None
        if flatten: # use each element of names_in_set separately
            added_this_round = []                    
            for element in names_in_set:
                add_this = tuple([element])
                if add_this not in groupings:
                   groupings[add_this] = 0
                   metadata[add_this] = []
                if add_this not in added_this_round:
                   groupings[add_this] += 1
                   added_this_round += [add_this]

        else: # treat the elements in aggregate; don't consider them individually
            if is_sorted:
                add_this = tuple(sorted(names_in_set))
            else:
                add_this = tuple(names_in_set)

            if add_this not in groupings:
                groupings[add_this] = 0
                metadata[add_this] = []
    
            groupings[add_this] += 1

        if add_this == None:
            continue
            
        link_text = ""
        sort_value = 0

        # is this for m+ or raid?
        if "keystoneLevel" in k: # m+
            link_text = "+%d" % k["keystoneLevel"]
            sort_value = int(k["keystoneLevel"])
        elif "total" in k: # raid
            link_text = "%.2fk" % (float(k["total"])/1000)
            sort_value = (float(k["total"])/1000)

        # 0 is an artifact of band value, for aggregated reports in the popover -- unused now
        report_id = ""
        if "reportID" in k:
            report_id = k["reportID"]

        if flatten:
            for element in names_in_set:
                add_this = tuple([element])
                metadata[add_this] += [[sort_value, 0, link_text, report_id]]            
        else:
            metadata[add_this] += [[sort_value, 0, link_text, report_id]]

    # get rid of duplicate icons in the look up table / mapping
    no_duplicate_mapping = {}
    for mapping in map_name_id_icon:
        if "id" not in mapping:
            logging.info(mapping)
            logging.info(extractor)
            continue
        if only_use_ids:
            no_duplicate_mapping[mapping["id"]] = [mapping["id"], ""]
        else:
            no_duplicate_mapping[mapping["id"]] = [mapping["id"], mapping["icon"], mapping["name"]]

    for k, v in metadata.iteritems():
        metadata[k] = sorted(v, key=operator.itemgetter(0), reverse=True)[:1] # just the best one

    return wcl_top10(groupings, metadata), no_duplicate_mapping


# extract elements in category
def wcl_generic_extract(ranking, category):
    names_in_set = []
    name_id_icons = []
    if category not in ranking:
        return [], []
    
    for i, j in enumerate(ranking[category]):
        names_in_set += [j["id"]]
        name_id_icons += [j]

    return names_in_set, name_id_icons


# extract gear a single ranking
def wcl_extract_gear(ranking, slots):
    names_in_set = []
    name_id_icons = []
    for i, j in enumerate(ranking["gear"]):
        if i in slots:
            names_in_set += [j["id"]]
            name_id_icons += [j]

    return names_in_set, name_id_icons

def wcl_gear(rankings, slots):
    is_sorted = True
    if 15 in slots: # don't sort if there's an offhand
        is_sorted = False

    return wcl_parse(rankings,
                     lambda e: wcl_extract_gear(e, slots),
                     is_sorted = is_sorted)

def wcl_extract_gems(ranking, shards=False):
    names_in_set = []
    name_id_icons = []
    
    for i, j in enumerate(ranking["gear"]):
        if "gems" in j:
            for each_gem in j["gems"]:
                if shards: # filter to shards
                    if each_gem["id"] not in shards_of_domination:
                        continue
                else: # filter out shards
                    if each_gem["id"] in shards_of_domination:                    
                        continue
                names_in_set += [each_gem["id"]]
                name_id_icons += [each_gem]

    return names_in_set, name_id_icons

def wcl_gems(rankings):
    return wcl_parse(rankings,
                     wcl_extract_gems,
                     only_use_ids=True,
                     flatten=True)

def wcl_gem_builds(rankings):
    return wcl_parse(rankings,
                     wcl_extract_gems,
                     only_use_ids=True)

def wcl_shards(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_gems(e, shards=True),
                     only_use_ids=True,
                     flatten=True)

def wcl_shard_builds(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_gems(e, shards=True),                     
                     only_use_ids=True)

def wcl_hsc(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_gear(e, [0, 2, 4]),
                     is_sorted=False) # we want to show in helm, shoulders, chest order

def wcl_extract_azerite_powers(ranking, offsets):
    names_in_set = []
    name_id_icons = []
    for i, j in enumerate(ranking["azeritePowers"]):
        if i % 5 in offsets:
            names_in_set += [j["id"]]
            name_id_icons += [j]
            
    return names_in_set, name_id_icons

def wcl_primary(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_azerite_powers(e, [0, 1]))

def wcl_role(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_azerite_powers(e, [2]))

def wcl_defensive(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_azerite_powers(e, [3]))


def wcl_extract_essences(ranking):
    names_in_set = []
    name_id_icons = []
    if "essencePowers" not in ranking:
        return [], []


    essences = []
    for i, j in enumerate(ranking["essencePowers"]):
        if i != 1: # skip the major's minor
            essences += [j["id"]]
            name_id_icons += [j]

    major = essences[0]
    minors = sorted(essences[1:])
    names_in_set = [major] + minors            
    
    return names_in_set, name_id_icons    
    

def wcl_essences(rankings):
    return wcl_parse(rankings, wcl_extract_essences, is_sorted=False)

def wcl_extract_talents(ranking):
    names_in_set = []
    name_id_icons = []

    for i, j in enumerate(ranking["talents"]):
        names_in_set += [j["id"]]
        name_id_icons += [j]
    
    return names_in_set, name_id_icons    

def wcl_talents(rankings):
    return wcl_parse(rankings, wcl_extract_talents, is_sorted=False)

def wcl_extract_soulbinds(ranking):
    names_in_set = []
    name_id_icons = []

    if "soulbindPowers" not in ranking:
        return [], []

    if "soulbindID" not in ranking:
        return [], []

    if ranking["soulbindID"] == 0:
        return [], []

    if ranking["covenantID"] not in [1, 2, 3, 4]:
        return [], []
        s
    names_in_set += [covenantID_mapping[ranking["covenantID"]]["id"]]
    name_id_icons += [
        {
            "name": covenantID_mapping[ranking["covenantID"]]["name"],
            "id": covenantID_mapping[ranking["covenantID"]]["id"],
            "icon": covenantID_mapping[ranking["covenantID"]]["icon"],
        }
    ]
   
    soulbindID_mapping = {}
    soulbindID_mapping[1] = {"name": "Niya"}
    soulbindID_mapping[2] = {"name": "Dreamweaver"}
    soulbindID_mapping[3] = {"name": "General Draven"}
    soulbindID_mapping[4] = {"name": "Plague Deviser Marileth"}
    soulbindID_mapping[5] = {"name": "Emeni"}
    soulbindID_mapping[6] = {"name": "Korayn"}
    soulbindID_mapping[7] = {"name": "Pelagos"}
    soulbindID_mapping[8] = {"name": "Nadjia the Mistblade"}
    soulbindID_mapping[9] = {"name": "Theotar the Mad Duke"}
    soulbindID_mapping[10] = {"name": "Bonesmith Heirmir"}
    soulbindID_mapping[13] = {"name": "Kleia"}
    soulbindID_mapping[18] = {"name": "Forgelite Prime Mikanikos"}

    # there's no spell or item id for soulbinds
    # so we have create our own icons
    # and then map to them
    # this is sadly very hacky for now
    # we convert to slug
    # and then use this in spec_view
    for k, v in soulbindID_mapping.iteritems():
        v["id"] = slugify.slugify(unicode(v["name"]))
    
    names_in_set += [soulbindID_mapping[ranking["soulbindID"]]["id"]]
    name_id_icons += [{"id": soulbindID_mapping[ranking["soulbindID"]]["id"],
                       "icon": soulbindID_mapping[ranking["soulbindID"]]["name"], # delib use name here
                       "name": soulbindID_mapping[ranking["soulbindID"]]["name"]}]

    return names_in_set, name_id_icons   
    

def wcl_extract_soulbind_abilities(ranking):
    names_in_set = []
    name_id_icons = []

    if "soulbindPowers" not in ranking:
        return [], []

    if "soulbindID" not in ranking:
        return [], []

    if ranking["soulbindID"] == 0:
        return [], []

    if ranking["covenantID"] not in [1, 2, 3, 4]:
        return [], []
    
    names_in_set, name_id_icons = wcl_extract_soulbinds(ranking)

    for i, j in enumerate(ranking["soulbindPowers"]):
        names_in_set += [j["id"]]
        name_id_icons += [j]

    return names_in_set, name_id_icons   

def wcl_soulbinds(rankings):
    return wcl_parse(rankings, wcl_extract_soulbinds, is_sorted=False)

def wcl_soulbind_abilities(rankings):
    return wcl_parse(rankings, wcl_extract_soulbind_abilities, is_sorted=False)

def wcl_extract_covenants(ranking):
    names_in_set = []
    name_id_icons = []

    if "covenantID" not in ranking:
        return [], []
    
    if ranking["covenantID"] not in [1, 2, 3, 4]:
        return [], []
    
    names_in_set += [covenantID_mapping[ranking["covenantID"]]["name"]]
    name_id_icons += [
        {
            "name": covenantID_mapping[ranking["covenantID"]]["name"],
            "id": covenantID_mapping[ranking["covenantID"]]["id"],
            "icon": covenantID_mapping[ranking["covenantID"]]["icon"],
        }
    ]
    
    return names_in_set, name_id_icons   

def wcl_covenants(rankings):
    return wcl_parse(rankings, wcl_extract_covenants, only_use_ids=False)

def wcl_legendaries(rankings):
    return wcl_parse(rankings, lambda e: wcl_generic_extract(e, "legendaryEffects"))

def wcl_conduit_builds(rankings):
    return wcl_parse(rankings, lambda e: wcl_generic_extract(e, "conduitPowers"))

def wcl_conduits(rankings):
    return wcl_parse(rankings, lambda e: wcl_generic_extract(e, "conduitPowers"), flatten=True)

# talents, essences, azerite combo -- now talents, soulbind, conduit, legendary
def wcl_extract_tea(ranking):
    names_in_set = []
    name_id_icons = []

    add_names, add_icons = wcl_extract_talents(ranking)
    names_in_set += add_names
    name_id_icons += add_icons

    add_names, add_icons = wcl_generic_extract(ranking, "legendaryEffects")
    names_in_set += add_names
    name_id_icons += add_icons

    add_names, add_icons = wcl_generic_extract(ranking, "soulbindPowers")
    names_in_set += add_names
    name_id_icons += add_icons
    
    add_names, add_icons = wcl_generic_extract(ranking, "conduitPowers")
    names_in_set += add_names
    name_id_icons += add_icons     
    
    return names_in_set, name_id_icons    

def wcl_tea(rankings):
    return wcl_parse(rankings, wcl_extract_tea, is_sorted=False)

# we want enchants for particular set of slots
def wcl_extract_enchants(ranking, slots, type="permanentEnchant"):
    names_in_set = []
    name_id_icons = []
    for i, j in enumerate(ranking["gear"]):
        if i in slots:
            if type in j:
                names_in_set += [j[type]]
                name_id_icons += [{"id":j[type]}]

    return names_in_set, name_id_icons

def wcl_enchants(rankings, slots, type="permanentEnchant"):
    return wcl_parse(rankings, lambda e: wcl_extract_enchants(e, slots, type), only_use_ids=True)
    

# pick the top 10, sorted by n
# filter for blanks
def wcl_top10(d, pop=None, top_n = 10):
    # consider sorting by key level / dps instead?   
    dv = sorted(d.items(), key=operator.itemgetter(1), reverse=True)
    output = []
    for i, (s, n) in enumerate(dv):
        if i >= top_n:
            break
        if pop == None:
            output += [[n, s, []]]
        else:
            output += [[n, s, pop[s]]]

    return output

# spec report generation
def gen_wcl_spec_report(spec, dungeon="all"):
    return base_gen_spec_report(spec, "mplus", dungeon)

def gen_wcl_raid_spec_report(spec, encounter="all", difficulty=MAX_RAID_DIFFICULTY):
    return base_gen_spec_report(spec, "raid", encounter, difficulty=difficulty)

def base_gen_spec_report(spec, mode, encounter="all", difficulty=MAX_RAID_DIFFICULTY):
    wcl_query = None

    if mode == "mplus":
        if encounter == "all":
            wcl_query = SpecRankings.query(SpecRankings.spec==spec)
        else:
            wcl_query = SpecRankings.query(SpecRankings.spec==spec,
                                           SpecRankings.dungeon==encounter)            
    elif mode=="raid":
        if encounter == "all":
            wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec)
        else:
            wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec,
                                               SpecRankingsRaid.encounter==encounter)
           
    results = wcl_query.fetch()
    global last_updated

    maxima = []
    n_parses = 0
    rankings = []

    available_difficulty = ""

    # add logs per difficulty per encounter
    mythic = {}
    heroic = {}
    normal = {}
    
    for k in results:
        if last_updated == None:
            last_updated = k.last_updated
        if k.last_updated > last_updated:
            last_updated = k.last_updated

            
        if mode == "raid":
            # filter out ignored encounters raid_ignore for all bosses
            if encounter == "all":
                if k.encounter in raid_ignore:
                    continue
            
        latest = json.loads(k.rankings)

        no_blanks = []
        # filter out reports that lack info (e.g. no covenant)
        for kk in latest:
            if kk['covenantID'] == 0:
                continue
            if kk['soulbindPowers'] == []:
                continue
            if kk['talents'] == []:
                continue
            if kk['legendaryEffects'] == []:
                continue
            if kk['conduitPowers'] == []:
                continue            
            no_blanks += [kk]

        latest = no_blanks
        
        if mode == "mplus":
            filtered_latest = []            
            for kk in latest:
                if kk["keystoneLevel"] < MIN_KEY_LEVEL:
                    continue
                filtered_latest += [kk]
            
            rankings += filtered_latest
        elif mode == "raid":
            if k.difficulty == "Mythic":
                if k.encounter not in mythic:
                    mythic[k.encounter] = []
                mythic[k.encounter] += latest
            elif k.difficulty == "Heroic":
                if k.encounter not in heroic:
                    heroic[k.encounter] = []
                heroic[k.encounter] += latest
            elif k.difficulty == "Normal":
                if k.encounter not in normal:
                    normal[k.encounter] = []
                normal[k.encounter] += latest

    if mode == "raid":
        # if it's all, go through encounter by c ounter
        # if it's a specific ecnounter
        if encounter == "all":
            seen_difficulties = set()

            # go through encounter by encounter
            for k, v in raid_encounters.iteritems():
                if difficulty == "Mythic":
                    if k in mythic:
                        rankings += mythic[k]
                        seen_difficulties.add("Mythic")
                    elif k in heroic:
                        rankings += heroic[k]
                        seen_difficulties.add("Heroic")
                    elif k in normal:
                        rankings += normal[k]
                        seen_difficulties.add("Normal")
                elif difficulty == "Heroic":
                    if k in heroic:
                        rankings += heroic[k]
                        seen_difficulties.add("Heroic")
                    elif k in normal:
                        rankings += normal[k]
                        seen_difficulties.add("Normal")

            canonical_order_difficulties = ["Mythic", "Heroic", "Normal"]
                
            seen_difficulties_canonical = []
            for diff in canonical_order_difficulties:
                if diff in seen_difficulties:
                    seen_difficulties_canonical += [diff]
                
            available_difficulty = " / ".join(seen_difficulties_canonical)
            
        else:
            if difficulty == "Mythic":
                if mythic != [] and encounter in mythic:
                        rankings = mythic[encounter]
                        available_difficulty = "Mythic"
                elif heroic != [] and encounter in heroic:
                        rankings = heroic[encounter]
                        available_difficulty = "Heroic"                    
                else:
                    if encounter in normal:
                        rankings = normal[encounter]
                        available_difficulty = "Normal"
            elif difficulty == "Heroic":
                if heroic != [] and encounter in heroic:
                        rankings = heroic[encounter]
                        available_difficulty = "Heroic"                    
                else:
                    if encounter in normal:
                        rankings = normal[encounter]
                        available_difficulty = "Normal"                    

                
                
    unique_characters = set()   
    for k in rankings:
        name_to_add = k["name"] + "-" + k["serverName"]
        unique_characters.add(name_to_add)
        if mode == "mplus":
            maxima += [k["keystoneLevel"]]

    n_uniques = len(unique_characters)
            
    # clean up difficulty display
    # a single boss should always be only one difficulty
    # this is for the all bosses view, where we might have a mix of
    # heroic and mythic bosses -- until all bosses are done on mythic for that spec
            


    items = {}
    spells = {}

    tea = []
    tea, update_spells = wcl_tea(rankings)
    spells.update(update_spells)
        
    talents, update_spells = wcl_talents(rankings)
    spells.update(update_spells)

    gear = {}    

    gear_slots = []
    gear_slots += [["helms", [0]]]
    gear_slots += [["neck", [1]]]
    gear_slots += [["shoulders", [2]]]
    gear_slots += [["chests", [4]]]
    gear_slots += [["belts", [5]]]
    gear_slots += [["legs", [6]]]
    gear_slots += [["feet", [7]]]
    gear_slots += [["wrists", [8]]]
    gear_slots += [["gloves", [9]]]
    gear_slots += [["rings", [10, 11]]]
    gear_slots += [["trinkets", [12, 13]]]
    gear_slots += [["cloaks", [14]]]
    gear_slots += [["weapons", [15, 16]]]

    for (slot_name, slots) in gear_slots:
        gear[slot_name], update_items = wcl_gear(rankings, slots) 
        items.update(update_items)


    # legendaries
    gear["legendaries"] = []

    # covenants
    covenants, update_spells = wcl_covenants(rankings)
    spells.update(update_spells)    

    # soulbinds & soulbind abilities
    soulbinds, update_spells = wcl_soulbinds(rankings)
    spells.update(update_spells)

    soulbind_abilities, update_spells = wcl_soulbind_abilities(rankings)
    spells.update(update_spells)
    
    # conduits & conduits builds
    conduits, update_spells = wcl_conduits(rankings)
    spells.update(update_spells)
    
    conduit_builds, update_spells = wcl_conduit_builds(rankings)
    spells.update(update_spells)

    # legendary powers
    legendaries, update_spells = wcl_legendaries(rankings)
    spells.update(update_spells)

    gems, update_items = wcl_gems(rankings)
    items.update(update_items)
            
    gem_builds, update_items = wcl_gem_builds(rankings)
    items.update(update_items)

    shards, update_items = wcl_shards(rankings)
    items.update(update_items)
            
    shard_builds, update_items = wcl_shard_builds(rankings)
    items.update(update_items)    
    
    enchants = {}
    enchant_ids = {}
    
    enchants["weapons"], update_enchant_ids = wcl_enchants(rankings, [15, 16])
    enchant_ids.update(update_enchant_ids)

    enchants["chests"], update_enchant_ids = wcl_enchants(rankings, [4])
    enchant_ids.update(update_enchant_ids)

    enchants["wrists"], update_enchant_ids = wcl_enchants(rankings, [8])
    enchant_ids.update(update_enchant_ids)

    enchants["feet"], update_enchant_ids = wcl_enchants(rankings, [7])
    enchant_ids.update(update_enchant_ids)    

    enchants["cloaks"], update_enchant_ids = wcl_enchants(rankings, [14])
    enchant_ids.update(update_enchant_ids)    
    
    enchants["rings"], update_enchant_ids = wcl_enchants(rankings, [10, 11])
    enchant_ids.update(update_enchant_ids)    
    
    enchants["belts"], update_enchant_ids = wcl_enchants(rankings, [5], type="onUseEnchant")
    enchant_ids.update(update_enchant_ids)

    max_maxima = 0
    min_maxima = 0
    
    if len(maxima) > 0:
        max_maxima = max(maxima)
        min_maxima = min(maxima)


    if mode == "raid":
        max_maxima = available_difficulty

    if encounter == "all":

        # log cov stats if it's all encounters
        spec_slug = slugify.slugify(unicode(spec))
        mode_slug = slugify.slugify(unicode(mode))
        
        key_slug = "%s-%s" % (spec_slug, mode_slug)

        data = {}
        data["n_parses"] = len(rankings)
        data["n_uniques"] = n_uniques
        data["covenants"] = covenants

        cov_stats = CovenantStats(id = key_slug,
                                  spec = spec,
                                  mode = mode,
                                  data = json.dumps(data))
        cov_stats.put()
    
        
    # raid won't have a max_maxima and a min_maxima (could use dps but not much point)
    # raid will return available_difficulty in max_maxima
    return len(rankings), n_uniques, max_maxima, min_maxima, tea, talents, legendaries, \
        gear, enchants, gems, gem_builds, shards, shard_builds, \
        covenants, soulbinds, soulbind_abilities, conduits, conduit_builds, \
        spells, items, enchant_ids


## end wcl parsing code


## Rendering Code starts here

def localized_time(last_updated):
    if last_updated == None:
        return pytz.utc.localize(datetime.datetime.now()).astimezone(pytz.timezone("America/New_York"))
    return pytz.utc.localize(last_updated).astimezone(pytz.timezone("America/New_York"))

## initial api
## todo: eventually we want this to be split into two
## -- generate the tier list (and store it in datastore)
## -- render it separately
## for now, to avoid rewriting this entirely, we're generating it as a side effect
def api_affixes_dungeons(affixes):
    global last_updated
    dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts = generate_counts(affixes)
    affixes_slug = slugify.slugify(unicode(affixes))
    affixes_slug_special = affixes_slug
    if affixes == current_affixes():
        affixes_slug_special = "index"
    
    dungeons_report, dungeon_stats = gen_dungeon_report(dungeon_counts)
    dtl = gen_dungeon_tier_list(dungeons_report)

    tiers = {}
    for k, v in dtl.iteritems():
        tiers[k] = []

    for k, v in dtl.iteritems():
        for d in dungeon_slugs:
            if d in v:
                tiers[k] += [slugs_to_dungeons[d]]
                
    last_updated_output = str(localized_time(last_updated))
    affixes_str = affixes

    rendered = {}
    rendered["last_updated"] = last_updated_output
    rendered["affixes"] = affixes
    rendered["dungeon_ease_tier_list"] = tiers
    rendered["source_url"] = "https://mplus.subcreation.net/"

    ## also store this
    affixes_slug = slugify.slugify(unicode(affixes))
    key = ndb.Key('DungeonEaseTierList', affixes_slug)
    tier_list_entry = DungeonEaseTierList(id=affixes_slug,
                                          affixes=affixes,
                                          last_updated=last_updated,
                                          tier_list=tiers)
    tier_list_entry.put()
                  
    return json.dumps(rendered)


# process the overall tier lists
def process_dungeon_ease_tier_lists_for_all_known_affixes():
    for af in known_affixes():
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(api_affixes_dungeons, af, _retry_options=options)
        

# read from the db to return the overall tier lists
def api_affixes_dungeons_overall():
    rendered = {}

    rendered["current_affixes"] = current_affixes()
    rendered["source_url"] = "https://mplus.subcreation.net/"
    rendered["last_updated"] = str(localized_time(last_updated))    

    query = DungeonEaseTierList.query()
    results = query.fetch()
    

    tier_lists = {}
    
    for detl in results:
        affixes = detl.affixes
        tier_list = detl.tier_list
        tier_lists[affixes] = tier_list

    rendered["tier_lists"] = tier_lists
        
    return json.dumps(rendered)


def api_affixes_specs(affixes):
    global last_updated
    dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts = generate_counts(affixes)
    affixes_slug = slugify.slugify(unicode(affixes))
    affixes_slug_special = affixes_slug
    if affixes == current_affixes():
        affixes_slug_special = "index"
    
    specs_report, spec_stats = gen_spec_report(spec_counts)
    dung_spec_report, dung_spec_stats = gen_dung_spec_report(dung_spec_counts, spec_counts)     
    specs_report = dung_spec_report # to balance out per dungeon anomalies
    
    tankstl = gen_spec_tier_list(specs_report, "Tanks", api=True)
    healerstl = gen_spec_tier_list(specs_report, "Healers", api=True)
    meleetl = gen_spec_tier_list(specs_report, "Melee", api=True)
    rangedtl = gen_spec_tier_list(specs_report, "Ranged",api=True)

    last_updated_output = str(localized_time(last_updated))
    affixes_str = affixes

    rendered = {}
    rendered["last_updated"] = last_updated_output
    rendered["affixes"] = affixes
    rendered["melee_tier_list"] = meleetl
    rendered["ranged_tier_list"] = rangedtl
    rendered["tank_tier_list"] = tankstl
    rendered["healer_tier_list"] = healerstl
    rendered["source_url"] = "https://mplus.subcreation.net/"
        
    return json.dumps(rendered)




def api_affixes_tier_list():
    global last_updated
    affixes = "All Affixes"
    
    dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts = generate_counts(affixes)
    affixes_slug = slugify.slugify(unicode(affixes))

    affixes_report, affix_stats = gen_affix_report(affix_counts)        
    aftl = gen_affix_tier_list(affixes_report, api=True)
    
    last_updated_output = str(localized_time(last_updated))
    affixes_str = affixes

    rendered = {}
    rendered["last_updated"] = last_updated_output
    rendered["current_affixes"] = current_affixes()
    rendered["affixes_ease_tier_list"] = aftl
    rendered["source_url"] = "https://mplus.subcreation.net/all-affixes.html"
        
    return json.dumps(rendered)

def render_affixes(affixes, prefix=""):
    dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts = generate_counts(affixes)
    affixes_slug = slugify.slugify(unicode(affixes))
    affixes_slug_special = affixes_slug
    if affixes == current_affixes():
        affixes_slug_special = "index"
    
    dungeons_report, dungeon_stats = gen_dungeon_report(dungeon_counts)
    specs_report, spec_stats = gen_spec_report(spec_counts)
    dung_spec_report, dung_spec_stats = gen_dung_spec_report(dung_spec_counts, spec_counts)    
    affixes_report, affix_stats = gen_affix_report(affix_counts)

    specs_report = dung_spec_report # to balance out per dungeon anomalies

    dtl = gen_dungeon_tier_list(dungeons_report)
    tankstl = gen_spec_tier_list(specs_report, "Tanks", prefix=prefix)
    healerstl = gen_spec_tier_list(specs_report, "Healers", prefix=prefix)
    meleetl = gen_spec_tier_list(specs_report, "Melee", prefix=prefix)
    rangedtl = gen_spec_tier_list(specs_report, "Ranged", prefix=prefix)
    aftl = gen_affix_tier_list(affixes_report)
    
    template = env.get_template('by-affix.html')
    rendered = template.render(title=affixes + " - Mythic+",
                               active_section = "mplus",
                               prefix=prefix,
                               affixes=affixes,
                               pretty_affixes=pretty_affixes(affixes),
                               pretty_affixes_large=pretty_affixes(affixes, size=56),
                               affixes_slug=affixes_slug,
                               affixes_slug_special=affixes_slug_special,
                               dungeons=dungeons_report,
                               dungeon_stats = dungeon_stats,
                               affixes_report=affixes_report,
                               affix_stats = affix_stats,
                               aftl = aftl,
                               dtl = dtl,
                               tankstl = tankstl,
                               healerstl = healerstl,
                               meleetl = meleetl,
                               rangedtl = rangedtl,
                               role_package=specs_report,
                               spec_stats = spec_stats,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))
    return rendered


def api_pvp_specs(mode):
    global last_updated

    specs_report, spec_stats = gen_pvp_specs_role_package(mode)
    
    tankstl = gen_pvp_spec_tier_list(specs_report, "Tanks", mode, api=True)
    healerstl = gen_pvp_spec_tier_list(specs_report, "Healers", mode, api=True)
    meleetl = gen_pvp_spec_tier_list(specs_report, "Melee", mode, api=True)
    rangedtl = gen_pvp_spec_tier_list(specs_report, "Ranged", mode, api=True)
    
    last_updated_output = str(localized_time(last_updated))

    rendered = {}
    rendered["last_updated"] = last_updated_output
    rendered["melee_tier_list"] = meleetl
    rendered["ranged_tier_list"] = rangedtl
    rendered["tank_tier_list"] = tankstl
    rendered["healer_tier_list"] = healerstl
    rendered["source_url"] = "https://pvp.subcreation.net/"
        
    return json.dumps(rendered)

# render compositions as a separate page from affixes
def render_compositions(affixes, prefix=""):
    dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts = generate_counts(affixes)
    affixes_slug = slugify.slugify(unicode(affixes))
    affixes_slug_special = affixes_slug
    if affixes == current_affixes():
        affixes_slug_special = "index"
    
    set_report = gen_set_report(set_counts)
    th_report = gen_set_report(th_counts)
    dps_report = gen_set_report(dps_counts)
    
    template = env.get_template('compositions.html')
    rendered = template.render(title=affixes + " - Compositions - Mythic+",
                               active_section = "mplus",
                               prefix=prefix,
                               affixes=affixes,
                               pretty_affixes=pretty_affixes(affixes),
                               pretty_affixes_large=pretty_affixes(affixes, size=56),
                               affixes_slug=affixes_slug,
                               affixes_slug_special=affixes_slug_special,
                               sets=set_report,
                               sets_th=th_report,
                               sets_dps=dps_report,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))
    return rendered

# render stats separately
def render_stats(affixes, prefix=""):
    dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts = generate_counts(affixes)
    affixes_slug = slugify.slugify(unicode(affixes))
    affixes_slug_special = affixes_slug
    if affixes == current_affixes():
        affixes_slug_special = "index"
   
    dungeons_report, dungeon_stats = gen_dungeon_report(dungeon_counts)
    specs_report, spec_stats = gen_spec_report(spec_counts)
    dung_spec_report, dung_spec_stats = gen_dung_spec_report(dung_spec_counts, spec_counts)    
    affixes_report, affix_stats = gen_affix_report(affix_counts)

    specs_report = dung_spec_report # to balance out per dungeon anomalies
    
    template = env.get_template('stats-affix.html')
    rendered = template.render(title=affixes,
                               active_section = "mplus",
                               prefix=prefix,
                               affixes=affixes,
                               pretty_affixes=pretty_affixes(affixes),
                               pretty_affixes_large=pretty_affixes(affixes, size=56),
                               affixes_slug=affixes_slug,
                               affixes_slug_special=affixes_slug_special,
                               dungeons_report = dungeons_report,
                               dungeon_stats = dungeon_stats,
                               affixes_report=affixes_report,
                               affix_stats = affix_stats,
                               role_package = specs_report,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))
    return rendered

# render raid stats separately
def render_raid_stats(encounter, prefix="", difficulty=MAX_RAID_DIFFICULTY):
    specs_report, spec_stats = gen_raid_specs_role_package(encounter, difficulty=difficulty)

    encounter_slugs = {}
    for e in raid_canonical_order:
        encounter_slugs[e] = slugify.slugify(unicode(e))

    encounter_slug = slugify.slugify(unicode(encounter))
    
    template = env.get_template('stats-raid.html')
    rendered = template.render(title=encounter,
                               active_section = "raid",
                               prefix=prefix,
                               difficulty = difficulty,
                               encounter = encounter,
                               encounter_slug = encounter_slug,
                               raid_stats = spec_stats,
                               role_package = specs_report,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))
    return rendered


def get_archetype(spec):
    if spec in tanks:
        return "tank"
    if spec in healers:
        return "healer"
    if spec in melee:
        return "melee"
    if spec in ranged:
        return "ranged"
    return "unknown"

def render_wcl_spec(spec, dungeon="all", prefix=""):
    spec_slug = slugify.slugify(unicode(spec))
    affixes = "N/A"
    n_parses, n_uniques, key_max, key_min, tea, talents, legendaries, gear, enchants, gems, gem_builds, shards, shard_builds, covenants, soulbinds, soulbind_abilities, conduits, conduit_builds, spells, items, enchant_ids = gen_wcl_spec_report(spec, dungeon)


    title = spec + " - Mythic+"
    if dungeon != "all":
        title = spec + " - %s - Mythic+" % dungeon

    spec_slug = slugify.slugify(unicode(spec))
    dungeon_slug = slugify.slugify(unicode(dungeon))

       
    template = env.get_template('spec.html')
    rendered = template.render(title = title,
                               active_section = "mplus",
                               active_page = spec_slug + "-" + dungeon_slug,
                               dungeon = dungeon,
                               spec = spec,
                               dungeon_slugs = dungeon_slugs,
                               slugs_to_dungeons = slugs_to_dungeons,
                               dungeon_short_names = dungeon_short_names,
                               archetype = get_archetype(spec),
                               spec_slug = spec_slug,
                               tea = tea,
                               talents = talents,
                               legendaries = legendaries,
                               affixes = affixes,
                               gear = gear,
                               enchants = enchants,
                               enchant_ids = enchant_ids,
                               enchant_mapping = enchant_mapping,
                               gems = gems,
                               gem_builds = gem_builds,
                               shards = shards,
                               shard_builds = shard_builds,                               
                               covenants = covenants,
                               covenantNameToID = covenantNameToID,
                               soulbinds = soulbinds,
                               soulbind_abilities = soulbind_abilities,
                               conduits = conduits,
                               conduit_builds = conduit_builds,
                               spells = spells,
                               items = items,
                               n_parses = n_parses,
                               n_uniques = n_uniques,
                               key_max = key_max,
                               key_min = key_min,
                               metric = "key",
                               prefix=prefix,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))

    return rendered

def render_raid_index(encounter="all", difficulty=MAX_RAID_DIFFICULTY, prefix=""):
    template = env.get_template("raid-index.html")

    specs_report, spec_stats = gen_raid_specs_role_package(encounter, difficulty=difficulty)

    encounter_slugs = {}
    for e in raid_canonical_order:
        encounter_slugs[e] = slugify.slugify(unicode(e))

    encounter_slug = slugify.slugify(unicode(encounter))
   
    tankstl = gen_raid_spec_tier_list(specs_report, "Tanks",
                                      encounter_slug=encounter_slug, prefix=prefix,
                                      difficulty=difficulty)
    healerstl = gen_raid_spec_tier_list(specs_report, "Healers",
                                        encounter_slug=encounter_slug,
                                        prefix=prefix,
                                        difficulty=difficulty)
    meleetl = gen_raid_spec_tier_list(specs_report, "Melee",
                                      encounter_slug=encounter_slug,
                                      prefix=prefix,
                                      difficulty=difficulty)
    rangedtl = gen_raid_spec_tier_list(specs_report, "Ranged",
                                       encounter_slug=encounter_slug,
                                       prefix=prefix,
                                       difficulty=difficulty)

    active_page = "raid-index"
    if encounter != "all":
        active_page = "raid-" + encounter_slug
    
    rendered = template.render(prefix=prefix,
                               active_page = active_page,
                               active_section = "raid",
                               title_override = "Subcreation %s" % RAID_NAME,
                               tankstl = tankstl,
                               healerstl = healerstl,
                               meleetl = meleetl,
                               difficulty = difficulty,
                               rangedtl = rangedtl,
                               role_package=specs_report,
                               spec_stats = spec_stats,
                               encounter=encounter,
                               raid_ignore = raid_ignore,
                               encounter_slugs = encounter_slugs,
                               encounter_slug = encounter_slug,                               
                               raid_canonical_order = raid_canonical_order,
                               raid_short_names = raid_short_names,                               
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               max_raid_difficulty = MAX_RAID_DIFFICULTY,
                               last_updated = localized_time(last_updated))


    return rendered


# for now just overall
def render_pvp_index(mode="all", prefix=""):
    template = env.get_template("pvp-index.html")

    specs_report, spec_stats = gen_pvp_specs_role_package(mode)

    global pvp_modes
    pvp_canonical_order = ["2v2", "3v3", "rbg"]
    pvp_pretty_names = {}
    pvp_pretty_names["2v2"] = "2v2 Arena"
    pvp_pretty_names["3v3"] = "3v3 Arena"
    pvp_pretty_names["rbg"] = "Rated BGs"
    
    mode_slugs = {}
    for e in pvp_canonical_order:
        mode_slugs[e] = slugify.slugify(unicode(e))

    mode_slug = slugify.slugify(unicode(mode))
    
    tankstl = gen_pvp_spec_tier_list(specs_report, "Tanks", mode, prefix=prefix)
    healerstl = gen_pvp_spec_tier_list(specs_report, "Healers", mode, prefix=prefix)
    meleetl = gen_pvp_spec_tier_list(specs_report, "Melee", mode, prefix=prefix)
    rangedtl = gen_pvp_spec_tier_list(specs_report, "Ranged", mode, prefix=prefix)    

    active_page = "pvp-index"
    if mode != "all":
        active_page = "pvp-" + mode_slug


    title_override = "Subcreation PvP"
    if mode != "all":
        title_override = "%s - Subcreation PvP" % pvp_pretty_names[mode]
        
    rendered = template.render(prefix=prefix,
                               active_page = active_page,
                               active_section = "pvp",
                               title_override = title_override,
                               tankstl = tankstl,
                               healerstl = healerstl,
                               meleetl = meleetl,
                               rangedtl = rangedtl,
                               role_package=specs_report,
                               spec_stats = spec_stats,
                               mode=mode,
                               mode_slugs = mode_slugs,
                               mode_slug = mode_slug,                               
                               pvp_canonical_order = pvp_canonical_order,
                               pvp_pretty_names = pvp_pretty_names,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))


    return rendered


# render pvp stats separately
def render_pvp_stats(mode, prefix=""):
    specs_report, spec_stats = gen_pvp_specs_role_package(mode)

    mode_slugs = {}
    for e in raid_canonical_order:
        mode_slugs[e] = slugify.slugify(unicode(e))

    pvp_canonical_order = ["2v2", "3v3", "rbg"]
    pvp_pretty_names = {}
    pvp_pretty_names["2v2"] = "2v2 Arena"
    pvp_pretty_names["3v3"] = "3v3 Arena"
    pvp_pretty_names["rbg"] = "Rated BGs"
    
    mode_slugs = {}
    for e in pvp_canonical_order:
        mode_slugs[e] = slugify.slugify(unicode(e))

    mode_slug = slugify.slugify(unicode(mode))
        
    mode_slug = slugify.slugify(unicode(mode))
    
    template = env.get_template('stats-pvp.html')
    rendered = template.render(title=mode,
                               active_section = "raid",
                               prefix=prefix,
                               mode = mode,
                               mode_slug = mode_slug,
                               pvp_stats = spec_stats,
                               role_package = specs_report,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))
    return rendered


def render_main_index(prefix=""):
    template = env.get_template("main-index.html")

    rendered = template.render(prefix=prefix,
                               title_override = "Subcreation",
                               active_section = "main",
                               active_page = "main-index")


    return rendered


def render_main_covenants(prefix=""):
    template = env.get_template("main-covenants.html")

    covenants, n_parses, m_data, r_data = gen_covenants_report()
    
    rendered = template.render(prefix=prefix,
                               covenants = covenants,
                               n_parses = n_parses,
                               m_data = m_data,
                               r_data = r_data,
                               title = "Top Covenants for Mythic+ Season 3 and Sepulcher of the First Ones",
                               active_section = "main",
                               active_page = "main-covenants",
                               last_updated = localized_time(last_updated))


    return rendered


def render_privacy(prefix=""):
    template = env.get_template("privacy.html")
    rendered = template.render(prefix=prefix,
                               title_override = "Privacy Policy - Subcreation",
                               active_section = "main",
                               active_page = "main-privacy")


    return rendered

def render_faq(prefix=""):
    template = env.get_template("faq.html")
    rendered = template.render(prefix=prefix,
                               title_override = "Frequently Asked Questions - Subcreation",
                               active_section = "main",
                               active_page = "main-faq")


    return rendered

def render_wcl_raid_spec(spec, encounter="all", prefix="", difficulty=MAX_RAID_DIFFICULTY):
    logging.info("%s %s %s" % (spec, encounter, difficulty))
    spec_slug = slugify.slugify(unicode(spec))
    affixes = "N/A"
    n_parses, n_uniques, available_difficulty, _, tea, talents, legendaries, gear, enchants, gems, gem_builds, shards, shard_builds, covenants, soulbinds, soulbind_abilities, conduits, conduit_builds, spells, items, enchant_ids = gen_wcl_raid_spec_report(spec, encounter, difficulty=difficulty)

    encounter_pretty = encounter
    if encounter_pretty == "all":
        encounter_pretty = "All Bosses"

    encounter_slugs = {}
    for e in raid_canonical_order:
        encounter_slugs[e] = slugify.slugify(unicode(e))

    encounter_slug = slugify.slugify(unicode(encounter))
    difficulty_slug = slugify.slugify(unicode(difficulty))

    metric = "dps"
    if spec in healers:
        metric = "hps"
        
    template = env.get_template('spec-raid.html')
    rendered = template.render(title = "%s - %s - %s" % (encounter_pretty, spec, RAID_NAME),
                               active_section = "raid",
                               spec = spec,
                               spec_slug = spec_slug,
                               archetype = get_archetype(spec),
                               active_page = spec_slug + "-" + encounter_slug + "-" + difficulty_slug,
                               tea = tea,
                               talents = talents,
                               legendaries = legendaries,
                               affixes = affixes,
                               gear = gear,
                               spells = spells,
                               items = items,
                               enchants = enchants,
                               enchant_ids = enchant_ids,
                               enchant_mapping = enchant_mapping,
                               metric = metric,
                               gems = gems,
                               gem_builds = gem_builds,
                               shards = shards,
                               shard_builds = shard_builds,                               
                               covenants = covenants,
                               covenantNameToID = covenantNameToID,
                               soulbinds = soulbinds,
                               soulbind_abilities = soulbind_abilities,
                               conduits = conduits, 
                               conduit_builds = conduit_builds,
                               raid_canonical_order = raid_canonical_order,
                               encounter_slugs = encounter_slugs,
                               raid_short_names = raid_short_names,
                               n_parses = n_parses,
                               n_uniques = n_uniques,
                               encounter = encounter,
                               encounter_slug = encounter_slug,
                               encounter_pretty = encounter_pretty,
                               difficulty = difficulty,
                               available_difficulty = available_difficulty,
                               max_raid_difficulty = MAX_RAID_DIFFICULTY,
                               prefix=prefix,
                               known_tanks = known_specs_subset_links(tanks, prefix=prefix),
                               known_healers = known_specs_subset_links(healers, prefix=prefix),
                               known_melee = known_specs_subset_links(melee, prefix=prefix),
                               known_ranged = known_specs_subset_links(ranged, prefix=prefix),
                               known_affixes = known_affixes_links(prefix=prefix),
                               last_updated = localized_time(last_updated))

    return rendered


## end html generation
    
## templates

from jinja2 import Environment, FileSystemLoader
env = Environment(
    loader=FileSystemLoader('templates'),
)

## end templates

## cloud storage -- low priority TODO: refactor this into one function instead of ugh, 3

def write_to_storage(filename, content):
    bucket_name = 'mplus.subcreation.net'
    original_filename = filename
    
    filename = "/%s/%s" % (bucket_name, filename)
    write_retry_params = gcs.RetryParams(backoff_factor=1.1)
    gcs_file = gcs.open(filename,
                        'w', content_type='text/html',
                        options={"cache-control" : "public, max-age=28800"},
                        retry_params=write_retry_params)
    gcs_file.write(str(content))
    gcs_file.close()

    cloudflare_purge_cache(bucket_name, original_filename)


def main_write_to_storage(filename, content, cache_control="public, max-age=86400", content_type="text/html"):
    bucket_name = 'subcreation.net'
    original_filename = filename
    
    filename = "/%s/%s" % (bucket_name, filename)
    write_retry_params = gcs.RetryParams(backoff_factor=1.1)
    gcs_file = gcs.open(filename,
                        'w', content_type=content_type,
                        options={"cache-control" : cache_control},
                        retry_params=write_retry_params)
    gcs_file.write(str(content))
    gcs_file.close()

    cloudflare_purge_cache(bucket_name, original_filename)

def pvp_write_to_storage(filename, content, cache_control="public, max-age=86400", content_type="text/html"):
    bucket_name = 'pvp.subcreation.net'
    original_filename = filename
    
    filename = "/%s/%s" % (bucket_name, filename)
    write_retry_params = gcs.RetryParams(backoff_factor=1.1)
    gcs_file = gcs.open(filename,
                        'w', content_type=content_type,
                        options={"cache-control" : cache_control},
                        retry_params=write_retry_params)
    gcs_file.write(str(content))
    gcs_file.close()

    cloudflare_purge_cache(bucket_name, original_filename)    

def raid_write_to_storage(filename, content):
    bucket_name = 'raid.subcreation.net'
    original_filename = filename
    
    filename = "/%s/%s" % (bucket_name, filename)
    write_retry_params = gcs.RetryParams(backoff_factor=1.1)
    gcs_file = gcs.open(filename,
                        'w', content_type='text/html',
                        options={"cache-control" : "public, max-age=86400"},
                        retry_params=write_retry_params)
    gcs_file.write(str(content))
    gcs_file.close()

    cloudflare_purge_cache(bucket_name, original_filename)


def render_and_write(af):
    rendered = render_affixes(af)
    
    filename_slug = slugify.slugify(unicode(af))

    if af == current_affixes():
        filename_slug = "index"

    affix_slug = slugify.slugify(unicode(af))

    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(write_to_storage, filename_slug + ".html", rendered,
                   _retry_options=options)

def render_and_write_compositions(af):
    rendered = render_compositions(af)
    
    filename_slug = slugify.slugify(unicode(af))

    affix_slug = slugify.slugify(unicode(af))

    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(write_to_storage, "compositions-" + filename_slug + ".html", rendered,
                   _retry_options=options)


def render_and_write_stats(af):
    rendered = render_stats(af)
    
    filename_slug = slugify.slugify(unicode(af))

    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(write_to_storage, "stats-" + filename_slug + ".html", rendered,
                   _retry_options=options)

def render_and_write_raid_stats(encounter, difficulty=MAX_RAID_DIFFICULTY):
    rendered = render_raid_stats(encounter, difficulty)
    
    filename_slug = slugify.slugify(unicode(encounter))
    if difficulty == "Heroic":
        filename_slug += "-heroic"

    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(raid_write_to_storage, "raid-stats-" + filename_slug + ".html", rendered,
                   _retry_options=options)        
    
def write_overviews():
    affixes_to_write = []
    affixes_to_write += ["All Affixes"]
    affixes_to_write += known_affixes()

    for af in affixes_to_write:
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(render_and_write, af,
                       _retry_options=options)

    for af in affixes_to_write:
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(render_and_write_compositions, af,
                       _retry_options=options)

    for af in affixes_to_write:
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(render_and_write_stats, af,
                       _retry_options=options)

    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(write_apis, _retry_options=options)    

def write_all_affixes():
    affixes_to_write = []
    affixes_to_write += ["All Affixes"]

    for af in affixes_to_write:
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(render_and_write, af,
                       _retry_options=options)   


def create_spec_overview(s, d="all"):
    spec_slug = slugify.slugify(unicode(s))
    rendered = render_wcl_spec(s, dungeon=d)
    if d == "all":
        filename = "%s.html" % (spec_slug)
    else:
        dungeon_slug = slugify.slugify(unicode(d))
        filename = "%s-%s.html" % (spec_slug, dungeon_slug)
    options = TaskRetryOptions(task_retry_limit = 1)        
    deferred.defer(write_to_storage, filename, rendered,
                       _retry_options=options)


def create_pvp_pages():
    global pvp_modes
    modes_to_generate = []
    modes_to_generate += ["all"]
    modes_to_generate += pvp_modes
    
    for mode in modes_to_generate:
        rendered = render_pvp_index(mode)
        options = TaskRetryOptions(task_retry_limit = 1)
        filename = "%s.html" % mode
        if mode == "all":
            filename = "index.html"

        deferred.defer(pvp_write_to_storage, filename, rendered,
                       _retry_options=options)

    write_pvp_stats()
    write_pvp_apis()


def write_pvp_stats():
    global pvp_modes
    modes_to_generate = []
    modes_to_generate += ["all"]
    modes_to_generate += pvp_modes
    
    for mode in modes_to_generate:
        rendered = render_pvp_stats(mode)
        options = TaskRetryOptions(task_retry_limit = 1)
        filename = "pvp-stats-%s.html" % mode

        deferred.defer(pvp_write_to_storage, filename, rendered,
                       _retry_options=options)    
    
def write_pvp_apis():
    global pvp_modes
    modes_to_generate = []
    modes_to_generate += ["all"]
    modes_to_generate += pvp_modes
    
    for mode in modes_to_generate:
        rendered = api_pvp_specs(mode)
        options = TaskRetryOptions(task_retry_limit = 1)
        filename = mode
        deferred.defer(write_api_json, "api/v0/pvp/" + filename, rendered, _retry_options=options)    


def write_api_json(filename, rendered):
    main_write_to_storage(filename,
                          rendered,
                          cache_control="public, max-age=28800",
                          content_type="application/json")
        
def create_main_pages():
    main_pages = [["index.html", render_main_index],
                  ["top-covenants.html", render_main_covenants]]

    for (filename, render_function) in main_pages:
        rendered = render_function()
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(main_write_to_storage, filename, rendered,
                       _retry_options=options)                        
    
def create_static_pages():
    static_pages = [["privacy.html", render_privacy],
                    ["faq.html", render_faq]]

    for (filename, render_function) in static_pages:
        rendered = render_function()
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(main_write_to_storage, filename, rendered,
                       _retry_options=options) 
    
def create_raid_index(difficulty=MAX_RAID_DIFFICULTY):
    rendered = render_raid_index(difficulty=difficulty)
    filename = "index.html"
    if difficulty == "Heroic":
        filename = "index-heroic.html"
    options = TaskRetryOptions(task_retry_limit = 1)        
    deferred.defer(raid_write_to_storage, filename, rendered,
                       _retry_options=options)

    # if it's heroic week then heroic is also the index
    if MAX_RAID_DIFFICULTY == "Heroic":
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(raid_write_to_storage, "index.html", rendered,
                       _retry_options=options)

    encounters_to_write = []
    encounters_to_write += raid_canonical_order

    for encounter in encounters_to_write:
        rendered = render_raid_index(encounter, difficulty)
        filename = slugify.slugify(unicode(encounter))
        if difficulty == "Heroic":
            filename += "-heroic"
        filename += ".html"
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(raid_write_to_storage, filename, rendered,
                       _retry_options=options)        

    # make sure to include all in stats
    encounters_to_write += ["all"]
    for encounter in encounters_to_write:
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(render_and_write_raid_stats, encounter, difficulty,
                       _retry_options=options)        
    
def create_raid_spec_overview(s, e="all", difficulty=MAX_RAID_DIFFICULTY):
    spec_slug = slugify.slugify(unicode(s))
    rendered = render_wcl_raid_spec(s, encounter=e, difficulty=difficulty)
    filename_slug = ""
    if e == "all":
        filename_slug = "%s" % (spec_slug)
    else:
        encounter_slug = slugify.slugify(unicode(e))
        filename_slug = "%s-%s" % (spec_slug, encounter_slug)
    if difficulty == "Heroic":
        filename_slug += "-heroic"
    filename = filename_slug + ".html"
    options = TaskRetryOptions(task_retry_limit = 1)        
    deferred.defer(raid_write_to_storage, filename, rendered,
                       _retry_options=options)

def write_spec_overviews():
    for s in specs:
        options = TaskRetryOptions(task_retry_limit = 1)        
        deferred.defer(create_spec_overview, s, "all",
                       _retry_options=options)

        for k, v in dungeon_encounters.iteritems():
            options = TaskRetryOptions(task_retry_limit = 1)        
            deferred.defer(create_spec_overview, s, k,
                           _retry_options=options)


def write_api_dungeon_ease():
    main_write_to_storage("api/v0/dungeon_ease_tier_list",
                          api_affixes_dungeons(current_affixes()),
                          cache_control="public, max-age=28800",
                          content_type="application/json")

def write_api_dungeon_ease_overall():
    main_write_to_storage("api/v0/dungeon_ease_tier_list_overall",
                          api_affixes_dungeons_overall(),
                          cache_control="public, max-age=28800",
                          content_type="application/json")    

def write_api_dungeon_specs():
    main_write_to_storage("api/v0/mplus_spec_tier_list",
                          api_affixes_specs(current_affixes()),
                          cache_control="public, max-age=28800",
                          content_type="application/json")

def write_api_affix_tier_list():
    main_write_to_storage("api/v0/affixes_ease_tier_list",
                          api_affixes_tier_list(),
                          cache_control="public, max-age=28800",
                          content_type="application/json")        
            
def write_apis():
    options = TaskRetryOptions(task_retry_limit = 1)    
    deferred.defer(write_api_dungeon_ease, _retry_options=options)
    deferred.defer(write_api_dungeon_specs, _retry_options=options)
    deferred.defer(write_api_affix_tier_list, _retry_options=options)

    deferred.defer(process_dungeon_ease_tier_lists_for_all_known_affixes,
                   _retry_options=options)

    deferred.defer(write_api_dungeon_ease_overall, _retry_options=options)

def write_raid_indices():
    # update the counts
    process_generate_raid_counts()    
    
    # write the index page
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    options = TaskRetryOptions(task_retry_limit = 1)
    for d in difficulties:
        deferred.defer(create_raid_index, d,
                       _retry_options=options)   

    
def write_raid_spec_overviews():

    # update the counts
    process_generate_raid_counts()    
    
    # write the index page
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    options = TaskRetryOptions(task_retry_limit = 1)
    for d in difficulties:
        deferred.defer(create_raid_index, d,
                       _retry_options=options)   

    for s in specs:
        for d in difficulties:
            options = TaskRetryOptions(task_retry_limit = 1)        
            deferred.defer(create_raid_spec_overview, s, "all", d,
                           _retry_options=options)
        
            for k, v in raid_encounters.iteritems():
                options = TaskRetryOptions(task_retry_limit = 1)        
                deferred.defer(create_raid_spec_overview, s, k, d,
                           _retry_options=options)


## end cloud storage

## cloudflare cache purge

def cloudflare_purge_cache(bucket, filename):
    url = "http://%s/%s" % (bucket, filename)
    cf_endpoint = "https://api.cloudflare.com/client/v4/zones/%s/purge_cache" % cloudflare_zone
    headers = { }
    headers["Content-Type"] = "application/json"
    headers["Authorization"] = "Bearer %s" % cloudflare_api_key
    data = {}
    data["files"] = [url]
    data = json.dumps(data)

    result = urlfetch.fetch(cf_endpoint, payload=data,
                            headers=headers, 
                            method=urlfetch.POST)

    return json.loads(result.content)["success"], url

## end cloudflare cache purge

## test reset db

def reset_db():
    kind_list = [DungeonAffixRegion, KnownAffixes, SpecRankings, SpecRankingsRaid]
    for a_kind in kind_list:
        kind_keys = a_kind.gql("").fetch(keys_only=True)
        ndb.delete_multi(kind_keys)
    return "resetDB for " + str(kind_list)

# just spec rankings raid
def reset_spec_rankings_raid():
    kind_list = [SpecRankingsRaid]
    for a_kind in kind_list:
        kind_keys = a_kind.gql("").fetch(keys_only=True)
        ndb.delete_multi(kind_keys)
    return "resetDB for " + str(kind_list)

##

def test_view(destination):
    affixes = current_affixes()
    spec = "all"
    dung = "all"
    prefix = "view?goto="

    if destination == "index.html":
        affixes = current_affixes()

    if "all-affixes" in destination:
        affixes = "All Affixes"

    for k in known_affixes():
        if slugify.slugify(unicode(k)) in destination:
            affixes = k
            break

    for s in specs:
        if slugify.slugify(unicode(s)) in destination:
            spec = s

    for i, k in enumerate(dungeon_slugs):
        if k in destination:
            dung = dungeons[i]

    if spec != "all":
        if dung != "all":
            return render_wcl_spec(spec, dung, prefix=prefix)        
        return render_wcl_spec(spec, dungeon="all", prefix=prefix)



    if "compositions" in destination:
        return render_compositions(affixes, prefix=prefix)        

    if "stats" in destination:
        return render_stats(affixes, prefix=prefix)
    
    return render_affixes(affixes, prefix=prefix)


def test_raid_view(destination):
    affixes = current_affixes()
    spec = "all"
    encounter = "all"
    prefix = "raid?goto="
    difficulty = MAX_RAID_DIFFICULTY

    if "heroic" in destination:
        difficulty = "Heroic"
    
    for s in specs:
        if slugify.slugify(unicode(s)) in destination:
            spec = s

    for e in raid_canonical_order:
        if slugify.slugify(unicode(e)) in destination:
            encounter = e

    if "index" in destination:
        return render_raid_index(prefix=prefix, difficulty=difficulty)

    if "stats" in destination:
        return render_raid_stats(encounter, prefix=prefix, difficulty=difficulty)
    
    if spec == "all":
        if encounter != "all":
            return render_raid_index(prefix=prefix, encounter=encounter, difficulty=difficulty)            
            

    return render_wcl_raid_spec(spec, encounter=encounter, prefix=prefix, difficulty=difficulty)


def test_main_view(destination):
    prefix = "main?goto="
    if "index" in destination:
        return render_main_index(prefix=prefix)

    if "privacy" in destination:
        return render_privacy(prefix=prefix)

    if "faq" in destination:
        return render_faq(prefix=prefix)    

    if "top-covenants" in destination:
        return render_main_covenants(prefix=prefix)

def test_pvp_view(destination):
    prefix = "pvp?goto="
    if "index" in destination:
        return render_pvp_index(prefix=prefix)

    mode = "all"
    global pvp_modes
    for m in pvp_modes:
        if m in destination:
            mode = m
    
    if "stats" in destination:
        return render_pvp_stats(mode, prefix=prefix)
    
    return render_pvp_index(mode, prefix=prefix)            
    
## wcl querying
# @@season update
def _rankings(encounterId, class_id, spec, page=1, season=WCL_SEASON):
    # filter to the last 4 weeks, or 9.0.5 date, whichever is sooner

    # 9.0.5 date
    latest_patch = datetime.datetime(2021, 3, 11, 0, 0)
    
    now = datetime.datetime.now()

    latest_patch_mkt = time.mktime(latest_patch.timetuple())
    four_weeks_ago = time.mktime(now.timetuple())-4*7*60*60*24

    filter_back_to = four_weeks_ago
    if latest_patch_mkt > four_weeks_ago:
        filter_back_to = latest_patch_mkt
    
    wcl_date = "date."
    wcl_date += "%d000" % (filter_back_to)
    wcl_date += "." + "%d000" % (time.mktime(now.timetuple()))
    
    url = "https://www.warcraftlogs.com:443/v1/rankings/encounter/%d?partition=%d&class=%d&spec=%d&page=%d&filter=%s&includeCombatantInfo=true&api_key=%s" % (encounterId, season, class_id, spec, page, wcl_date, api_key)

    result = urlfetch.fetch(url, deadline=60)
    data = json.loads(result.content)
    return data


def update_wcl_rankings(spec, dungeon, page):
    if spec not in wcl_specs:
        return "invalid spec [%s]" % spec
    spec_key = slugify.slugify(unicode(spec))
    if dungeon not in dungeon_encounters:
        return "invalid dungeon [%s]" % dungeon
    dungeon_id = dungeon_encounters[dungeon]
    dungeon_slug = slugify.slugify(unicode(dungeon))

    aggregate = []
    
    rankings = _rankings(dungeon_id, wcl_specs[spec][0], wcl_specs[spec][1], page=page)

    for k in rankings["rankings"]:
        aggregate += [k]
    
    key = ndb.Key('SpecRankings', "%s-%s-%d" % (spec_key, dungeon_slug, page))
    sr = SpecRankings(key=key)
    sr.spec = spec
    sr.dungeon = dungeon
    sr.page = page
    sr.rankings = json.dumps(aggregate)
    sr.put()

# 4 - heroic
# 5 - mythic
def _rankings_raid(encounterId, class_id, spec, difficulty=4, page=1, season=WCL_PARTITION, metric="dps"):
    # filter to the last 4 weeks
    now = datetime.datetime.now()
    wcl_date = "date."
    wcl_date += "%d000" % (time.mktime(now.timetuple())-4*7*60*60*24 )
    wcl_date += "." + "%d000" % (time.mktime(now.timetuple()))

    #partition = 4 # for prepatch
    # &partition=%d
    url = "https://www.warcraftlogs.com:443/v1/rankings/encounter/%d?difficulty=%d&class=%d&spec=%d&page=%d&filter=%s&metric=%s&includeCombatantInfo=true&api_key=%s" % (encounterId, difficulty, class_id, spec, page, wcl_date, metric, api_key)

    result = urlfetch.fetch(url, deadline=60)
    data = json.loads(result.content)
    return data


def update_wcl_raid_rankings(spec, encounter, page=1, difficulty=MAX_RAID_DIFFICULTY):
    if spec not in wcl_specs:
        return "invalid spec [%s]" % spec
    spec_key = slugify.slugify(unicode(spec))
    if encounter not in raid_encounters:
        return "invalid encounter [%s]" % encounter
    encounter_id = raid_encounters[encounter]
    encounter_slug = slugify.slugify(unicode(encounter))

    logging.info("%s %s %s %s" % (spec, encounter, difficulty, page))
    
    aggregate = []
    
    difficulty_code = 5
    if difficulty == "Normal":
        difficulty_code = 3    
    if difficulty == "Heroic":
        difficulty_code = 4
    if difficulty == "Mythic":
        difficulty_code = 5

    metric = "dps"
    if spec in healers:
        metric = "hps"
        
    rankings = _rankings_raid(encounter_id, wcl_specs[spec][0], wcl_specs[spec][1], difficulty_code, page=page, metric=metric)

    no_data_yet = False
    if "rankings" not in rankings:
        no_data_yet = True
    else:
        if len(rankings["rankings"]) == 0:
            no_data_yet = True
    
    # no data yet
    if no_data_yet:
        logging.info("No parses found for %s %s %s (page %d)." % (spec, difficulty, encounter, page))
        if difficulty == "Heroic" and page == 1: # fall back to normal only if no heroic parses at all
            logging.info("Falling back to Normal")
            # queue up all pages for normal
            i = 1
            while (i <= 5):
                options = TaskRetryOptions(task_retry_limit = 1)
                deferred.defer(update_wcl_raid_rankings, spec, encounter, page=i, difficulty="Normal",
                               _retry_options=options)
                i += 1
            return True
        return False # otherwise fail
    else:
        logging.info("%d parses found for %s %s %s (page %d)" % (len(rankings["rankings"]), spec, difficulty, encounter, page))
    
    for k in rankings["rankings"]:
        aggregate += [k]

    key = ndb.Key('SpecRankingsRaid', "%s-%s-%s-%d" % (spec_key, encounter_slug, slugify.slugify(unicode(difficulty)), page))
    sr = SpecRankingsRaid(key=key)
    sr.spec = spec
    sr.encounter = encounter
    sr.difficulty = difficulty
    sr.page = page
    sr.rankings = json.dumps(aggregate)
    sr.put()
    
    return True

    
def update_wcl_spec(spec):
    if spec not in wcl_specs:
        return "invalid spec [%s]" % spec
    spec_key = slugify.slugify(unicode(spec))

    aggregate = []
    for k, v in dungeon_encounters.iteritems():
        i = 1
        while (i <= 5):
            options = TaskRetryOptions(task_retry_limit = 1)            
            deferred.defer(update_wcl_rankings, spec, k, page=i,
                           _retry_options=options)
            i += 1

    return spec, spec_key,  wcl_specs[spec]



# get the data for dungeons
def update_wcl_update():
    for i, s in enumerate(specs):
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(update_wcl_spec, s, _retry_options=options)


def update_wcl_update_subset(subset):
    for i, s in enumerate(subset):
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(update_wcl_spec, s, _retry_options=options)
        

def update_wcl_raid_spec(spec, difficulty=MAX_RAID_DIFFICULTY):
    logging.info("%s %s" % (spec, difficulty))
    if spec not in wcl_specs:
        return "invalid spec [%s]" % spec
    spec_key = slugify.slugify(unicode(spec))

    aggregate = []
    for k, v in raid_encounters.iteritems():
        i = 1
        while (i <= 5):
            options = TaskRetryOptions(task_retry_limit = 1)
            deferred.defer(update_wcl_raid_rankings, spec, k, page=i, difficulty=difficulty,
                           _retry_options=options)
            i += 1

    return spec, spec_key,  wcl_specs[spec]
        
# update wcl for raids
def update_wcl_raid_update():
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    for i, s in enumerate(specs):
        for d in difficulties:
            options = TaskRetryOptions(task_retry_limit = 1)    
            deferred.defer(update_wcl_raid_spec, s, d, _retry_options=options)

def update_wcl_raid_update_subset(subset):
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    for i, s in enumerate(subset):
        for d in difficulties:
            options = TaskRetryOptions(task_retry_limit = 1)    
            deferred.defer(update_wcl_raid_spec, s, d, _retry_options=options)
        
def update_wcl_raid_all():
    update_wcl_raid_update()
    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(write_raid_spec_overviews, _retry_options=options)
    
# update all the wcl for dungeons
def update_wcl_all():
    update_wcl_update()
    options = TaskRetryOptions(task_retry_limit = 1)
    deferred.defer(write_spec_overviews, _retry_options=options)

# update pvp ladder stas

# region = us or eu
# mode = 2v2 or 3v3 or rbg
def _pvp_rankings(region, mode):
    global ludus_access_key
    url = "https://luduslabs.org/api/leaderboard/%s/%s?access_key=%s" % (region, mode, ludus_access_key)
    result = urlfetch.fetch(url, deadline=60)
    data = json.loads(result.content)
    return data


def update_pvp_rankings(region, mode):
    data = _pvp_rankings(region, mode)
    key_slug = "%s-%s" % (region, mode)
    ls = PvPLadderStats(id = key_slug,
                        region = region,
                        mode = mode,
                        data = json.dumps(data))
    ls.put()   

def update_all_pvp_rankings():
    global pvp_regions, pvp_modes
    for region in pvp_regions:
        for mode in pvp_modes:
            options = TaskRetryOptions(task_retry_limit = 1)            
            deferred.defer(update_pvp_rankings, region, mode,
                           _retry_options=options)

## handlers

# look at what the raw wcl rankings string looks like
def test_inspect(rh, mode):
    if mode == "mplus":
        spec = "Havoc Demon Hunter"
        wcl_query = SpecRankings.query(SpecRankings.spec==spec)
    elif mode=="raid":
        spec = "Havoc Demon Hunter"
        wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec)
        
    results = wcl_query.fetch()
    
    for x in results:
        for k, v in json.loads(x.rankings)[0].iteritems():
            rh.response.write("%s\n" % k)
            rh.response.write("%s\n\n" % v)
        return

class UpdateCurrentDungeons(webapp2.RequestHandler):
    def get(self):
        global dungeons, regions
        self.response.headers['Content-Type'] = 'text/plain'
        update_current()
        self.response.write("Updates queued.")

import datetime
import pytz
last_updated = None


class OnlyGenerateHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing templates to cloud storage...")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(write_overviews, _retry_options=options)

class OnlyGenerateAllAffixesHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing templates to cloud storage...")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(write_all_affixes, _retry_options=options)          

class TestView(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        destination = self.request.get("goto", "index.html")
        self.response.write(test_view(destination))

class TestRaidView(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        destination = self.request.get("goto", "index.html")
        self.response.write(test_raid_view(destination))

class TestMainView(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        destination = self.request.get("goto", "index.html")
        self.response.write(test_main_view(destination))        
        

class KnownAffixesShow(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(str(current_affixes()))
        self.response.write(str(known_affixes()))

class WCLGetRankings(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        update_wcl_all()

class WCLGetRankingsOnly(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        update_wcl_update()

class TestLudusPvP(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing PvP updates...\n")
        update_all_pvp_rankings()

class TestWCLGetRankings(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        update_wcl_update_subset(["Havoc Demon Hunter"])
#        update_wcl_update_subset(["Survival Hunter"])

class WCLGetRankingsRaid(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        update_wcl_raid_all()

class WCLGetRankingsRaidOnly(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        update_wcl_raid_update()

class TestWCLInspectMPlus(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Inspecting M+:\n")
        test_inspect(self, "mplus")

class TestWCLInspectRaid(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Inspecting Raid:\n")
        test_inspect(self, "raid")

class TestWCLGetRankingsRaid(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        update_wcl_raid_update_subset(["Havoc Demon Hunter"]) 

class WCLGenHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing WCL HTML...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(write_spec_overviews, _retry_options=options)

class WCLRaidGenHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing WCL Raid HTML...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(write_raid_spec_overviews, _retry_options=options)

class WCLRaidIndicesGenHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing WCL Raid HTML (Indices Only)...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(write_raid_indices, _retry_options=options)        

class GenStaticHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing Static HTML pages...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(create_static_pages, _retry_options=options)

class GenMainHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing Main HTML pages...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(create_main_pages, _retry_options=options)

class GenPVP(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing PvP HTML pages...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(create_pvp_pages, _retry_options=options)            

class GenAPIs(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing APIs...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        deferred.defer(write_apis, _retry_options=options)

        
class TestCloudflarePurgeCache(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Testing cloudflare purge cache...\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        self.response.write(cloudflare_purge_cache("mplus.subcreation.net", "index.html"))

class TestResetDB(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Clearing db\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        self.response.write(reset_db())

class TestResetSpecRankingsRaid(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Clearing spec_rankings_raid\n")
        options = TaskRetryOptions(task_retry_limit = 1)
        self.response.write(reset_spec_rankings_raid())           

class APIDungeonEase(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_affixes_dungeons(current_affixes()))

class APIDungeonEaseOverall(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_affixes_dungeons_overall())        

class APIAffixEase(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_affixes_tier_list())        

class APIDungeonSpecs(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_affixes_specs(current_affixes()))

class ProcessDungeonEaseTierLists(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        process_dungeon_ease_tier_lists_for_all_known_affixes()        
        self.response.write("Queueing processing tier lists...")

class ProcessRaidCounts(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        process_generate_raid_counts()
        self.response.write("Queueing processing raid counts...")

class ProcessPvPCounts(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write("Queueing processing pvp counts...")        
        process_pvp_counts()

class TestPvPView(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        destination = self.request.get("goto", "index.html")        
        self.response.write(test_pvp_view(destination))

class APIPvPAll(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("all"))


class APIPvP2v2(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("2v2"))


class APIPvP3v3(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("3v3"))

        
class APIPvPRBG(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("rbg"))     

app = webapp2.WSGIApplication([
        ('/update_wcl', WCLGetRankings),
        ('/update_wcl_raid', WCLGetRankingsRaid),
    
        ('/refresh/affixes', UpdateCurrentDungeons),
        ('/refresh/dungeons', WCLGetRankingsOnly),
        ('/refresh/raids', WCLGetRankingsRaidOnly),
        ('/refresh/pvp', TestLudusPvP),    

        ('/process/dungeon_ease_tier_lists', ProcessDungeonEaseTierLists),
        ('/process/raid_counts', ProcessRaidCounts),
        ('/process/pvp', ProcessPvPCounts),
    
        ('/generate/affixes', OnlyGenerateHTML),
        ('/generate/all_affixes', OnlyGenerateAllAffixesHTML),
        ('/generate/dungeons', WCLGenHTML),
        ('/generate/raids', WCLRaidGenHTML),
        ('/generate/raid_indices', WCLRaidIndicesGenHTML),    
        ('/generate/main', GenMainHTML),    
        ('/generate/static', GenStaticHTML),
        ('/generate/apis', GenAPIs), # does not include pvp apis
        ('/generate/pvp', GenPVP),    

        ('/api/dungeon_ease', APIDungeonEase),
        ('/api/mplus_specs', APIDungeonSpecs),
        ('/api/mplus_affixes', APIAffixEase),
        ('/api/dungeon_ease_overall', APIDungeonEaseOverall),
        ('/api/pvp/all', APIPvPAll),
        ('/api/pvp/2v2', APIPvP2v2),
        ('/api/pvp/3v3', APIPvP3v3),
        ('/api/pvp/rbg', APIPvPRBG),
    
        ('/view', TestView),
        ('/raid', TestRaidView),
        ('/pvp', TestPvPView),    
        ('/main', TestMainView),    

        ('/test/known_affixes', KnownAffixesShow),
        ('/test/affixes', UpdateCurrentDungeons),
        ('/test/dungeons', TestWCLGetRankings),
        ('/test/raids', TestWCLGetRankingsRaid),
        ('/test/pvp', TestLudusPvP),
        ('/test/inspect_mplus', TestWCLInspectMPlus),
        ('/test/inspect_raid', TestWCLInspectRaid),    
        ('/test/cloudflare_purge', TestCloudflarePurgeCache),
        ('/test/reset_db', TestResetDB),
        ('/test/reset_spec_rankings_raid', TestResetSpecRankingsRaid),
    
        ], debug=True)
