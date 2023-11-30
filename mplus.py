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
from google.appengine.runtime import DeadlineExceededError

from google.appengine.ext import vendor
# add libraries in lib
vendor.add('lib')

import slugify
import cloudstorage as gcs

import datetime
import pytz

from dragonflight import dungeons, dungeon_slugs, dungeon_short_names, slugs_to_dungeons
from dragonflight import primordial_stones

from warcraft import specs, tanks, healers, melee, ranged, role_titles, regions, pvp_regions, pvp_modes
from warcraft import spec_short_names
from t_interval import t_interval
from talents_to_spells import talents_to_spells
from talent_ids import talent_id_order, talent_id_class, talent_id_spec, talent_id_heights

from models import Run, DungeonAffixRegion, KnownAffixes, PvPLadderStats, PvPCounts

# wcl handling
from models import SpecRankings, SpecRankingsRaid, RaidCounts, DungeonEaseTierList
from auth import api_key
from wcl import wcl_specs
from wcl_dragonflight import dungeon_encounters

# information about dragonflight talent trees
from tree import class_zero, class_eight, class_twenty
from tree import spec_zero, spec_eight, spec_twenty
from tree import talent_order
from priority_talents import priority_talents
from active_talents import class_active, spec_active

from encode_talent_string import encode_talent_string

from dragonflight import tier_items, embellished_items, crafted_items

from enchants import enchant_mapping, enchant_collapse

# cloudflare cache handling
from auth import cloudflare_api_key, cloudflare_zone

# ludus labs api
from auth import ludus_access_key

# internal api 
from auth import internal_api

## globals
from config import RIO_MAX_PAGE
from dragonflight import dungeons as DUNGEONS

from warcraft import regions as REGIONS
from config import RIO_MAX_PAGE, RIO_SEASON, RAID_NAME
from config import WCL_SEASON, WCL_PARTITION
from config import MIN_KEY_LEVEL
from config import MAX_RAID_DIFFICULTY

from config import latest_patch_us
from config import latest_patch_eu
from config import latest_patch_kr
from config import latest_patch_tw

last_updated = None

## raid rotation
known_raids = ["amirdrassil"]
from wcl_dragonflight import amirdrassil_encounters
from amirdrassil import amirdrassil_canonical_order, amirdrassil_short_names, amirdrassil_ignore

def get_raid_encounters(active_raid):
    return amirdrassil_encounters

def get_raid_canonical_order(active_raid):
    return amirdrassil_canonical_order  

def get_raid_short_names(active_raid):
    return amirdrassil_short_names

def get_raid_ignore(active_raid):
    return amirdrassil_ignore

# rotate updating raids every day
def determine_raids_to_update(current_time=None):        
    raids_to_update = ["amirdrassil"]
    return raids_to_update

# rotate updating raids every day
def determine_raids_to_generate(current_time=None):
    raids_to_update = ["amirdrassil"]
    return raids_to_update

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
        affixes += data[0]["run"]["weekly_modifiers"][2]["name"]
        # R.I.P. Seasonal Affix
#        affixes += data[0]["run"]["weekly_modifiers"][3]["name"]

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
        deferred.defer(update_dungeon_affix_region, dungeon, affixes,
                       region, season, page)

    except urlfetch.Error:
        logging.exception('caught exception fetching url: %s', req_url)

def update_current():
    '''Query the r.io api across all regions for each dungeon (current affixes)'''
    global DUNGEONS, REGIONS, RIO_MAX_PAGE
    for region in REGIONS:
        for dungeon in DUNGEONS:
            for page in range(0, RIO_MAX_PAGE):
                deferred.defer(update_dungeon_affix_region,
                               dungeon,
                               "current",
                               region,
                               page=page)


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


def create_package(name):
    package = {}
    package["name"] = name
    package["slug"] = slugify.slugify(unicode(name))
    return package


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
    return '<a href="%s.html"><img src="images/spec-icons/%s.jpg" width="%d" height="%d" title="%s" alt="%s" /><br/>%s</a>' % (prefix+dslug, dslug, size, size, dname, dname, dname)

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
        return '<img src="images/affixes/%s.jpg" class="zoom-icon" width="%d" height="%d" title="%s" alt="%s" />' % (aslug, size, size, aname, aname)


    affixen = dname.split(", ")
    output = []

    
    for af in affixen:
        afname = af
        afslug = slugify.slugify(af)
        output += [miniaffix(afname, afslug, size=size)]

    output_string = output[0]
    output_string += output[1]
    output_string += output[2]
#   R.I.P. Seasonal Affix  
#    output_string += output[3]
       
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
            
            slug_link = slugify.slugify(k[1])
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

def construct_analysis(counts, sort_by="lb_ci", limit=500):
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


        # filter to top 500
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
        # restrict the mean just to the runs actually used for lb_ci
        overall += [[name, sorted_mean, stddev, n, ci, maxi, all_runs]]

    overall = sorted(overall, key=lambda x: x[4][0], reverse=True)        
    if sort_by == "max":
        overall = sorted(overall, key=lambda x: x[5][0], reverse=True)
    if sort_by == "n":
        overall = sorted(overall, key=lambda x: x[3], reverse=True)          
    
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
        # restrict the mean just to the runs actually used for lb_ci        
        overall[encounter]= [ci[0], n, sorted_mean, data]

    return overall


# build a spec report for raid
# build for each boss, and overall
# save this to the db for each spec

# read from the db
def gen_raid_spec_analysis(difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    # raid_generate_counts is now a cached call
    raid_counts, raid_max_found, raid_max_link = raid_generate_counts(difficulty=difficulty, active_raid=active_raid)
    
    analysis = {}
    lb_ci_spec = {}

    lb_ci_spec["all"] = {}
    
    for s in specs:
        analysis[s] = construct_analysis_raid(raid_counts[s])

        scores = []
        all_scores = []
        n_scores = 0

        raid_encounters = get_raid_encounters(active_raid)
        for e in raid_encounters:
            raid_ignore = get_raid_ignore(active_raid)
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

def gen_raid_specs_role_package(encounter, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    global role_titles, specs

    # gen_raid_spec analysis uses the memoized raid_generate_counts
    lb_ci_spec, raid_max_found, raid_max_link = gen_raid_spec_analysis(difficulty=difficulty, active_raid=active_raid)
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
def gen_raid_spec_tier_list(specs_report, role, encounter_slug="all", prefix="", difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
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
                                       difficulty = difficulty,
                                       active_raid = active_raid,
                                       prefix = prefix)
            dtl[tm[i]] += rendered
    
    return dtl   

def gen_pvp_specs_role_package(mode):
    global role_titles, specs
    role_package = {}
    stats = {}

    logging.info(mode)
    key_slug = "us-%s" % mode
    pc = ndb.Key('PvPLadderStats', key_slug).get()
    data = json.loads(pc.data)

    proxy_role_titles = {}
    proxy_role_titles[0] = "tank"
    proxy_role_titles[1] = "healer"
    proxy_role_titles[2] = "melee"
    proxy_role_titles[3] = "ranged"
    
    for i in range(4):
        role_package[role_titles[i]] = data["%s_data" % proxy_role_titles[i]]
        stats[role_titles[i]] = {}
        stats[role_titles[i]]["n"] = data["counts"][proxy_role_titles[i]]

    logging.info(role_package)        
    logging.info(stats)

    # dirty hack to get around %z not working in strptime py2.7
    # we know the api always gives us US ET time, which is -5 from UTC
    this_updated = data["last_updated"][:-6] 
    this_updated = datetime.datetime.strptime(this_updated, "%Y-%m-%d %H:%M:%S.%f")
    this_updated += datetime.timedelta(hours=5)
    
    global last_updated
    if last_updated == None:
        last_updated = this_updated
    if this_updated > last_updated:
        last_updated = this_updated

    return role_package, stats

    
# solo shuffle tier list
# it has tier list embedded in the internal api call
def gen_pvp_spec_tier_list(specs_report, role, mode, api=False, prefix=""):
    key_slug = "us-%s" % mode
    pc = ndb.Key('PvPLadderStats', key_slug).get()
    data = json.loads(pc.data)

    proxy_role_titles = {}
    proxy_role_titles[0] = "tank"
    proxy_role_titles[1] = "healer"
    proxy_role_titles[2] = "melee"
    proxy_role_titles[3] = "ranged"
    
    proxy_role = ""
    if role == "Tanks":
        proxy_role = "tank"
    elif role == "Healers":
        proxy_role = "healer"
    elif role == "Melee":
        proxy_role = "melee"
    elif role == "Ranged":
        proxy_role = "ranged"
    
        
    tiers = {}
    tm = {}
    tm[5] = "S"
    tm[4] = "A"
    tm[3] = "B"
    tm[2] = "C"
    tm[1] = "D"
    tm[0] = "F"

    for i in range(0, 6):
        tiers[tm[i]] = data["%s_tier_list" % proxy_role][tm[i]]

    logging.info(tiers)

    

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
                rendered = template.render(spec_name = k,
                                           spec_short_name = spec_short_names[k],
                                           spec_slug = slugify.slugify(unicode(k)))
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
                dtl[tm[i]] += [k]
        
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
                                        if ch in spec_counts: # handle "fire paladin" errors
                                            spec_counts[ch] += [run]
                                            dung_spec_counts[dung][ch] += [run]
                            
    return dungeon_counts, spec_counts, set_counts, th_counts, dps_counts, affix_counts, dung_spec_counts


# for constructing the raid tier list
# we'll have 3 -- all dps against each other (melee and ranged)
# all tanks against each other (since we only have dps for tanks and tank dps is so much lower)
# all healers against each other, based on hps
def process_raid_generate_counts_spec_encounter(spec, encounter, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    counts = []
    
    # only consider the difficulty (we'll need to call this twice, once each for Heroic and Mythic)
    wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec,
                                       SpecRankingsRaid.difficulty==difficulty,
                                       SpecRankingsRaid.encounter==encounter,
                                       SpecRankingsRaid.raid==active_raid)
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
                # @@TODO add fight id
            

    
    data = {}
    data["counts"] = counts
    data["max_found"] = max_found
    data["max_link"] = max_link

    spec_slug = slugify.slugify(unicode(spec))
    encounter_slug = slugify.slugify(unicode(encounter))
    difficulty_slug = slugify.slugify(unicode(difficulty))

    key_slug = "%s-%s-%s-%s" % (spec_slug, encounter_slug, difficulty_slug, active_raid)
    key = ndb.Key('RaidCounts', key_slug)
    
    raid_counts = RaidCounts(id = key_slug,
                             difficulty = difficulty,
                             spec = spec,
                             encounter = encounter,
                             raid = active_raid,
                             data = json.dumps(data),
                             last_updated = last_updated)

    raid_counts.put()


def process_generate_raid_counts_for_raids(raids):
    for r in raids:
        deferred.defer(process_generate_raid_counts, active_raid=r)
        
def process_generate_raid_counts(active_raid=""):
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    for d in difficulties:
        for s in specs:
            raid_encounters = get_raid_encounters(active_raid)
            for k, v in raid_encounters.iteritems():
                deferred.defer(process_raid_generate_counts_spec_encounter, s, k, d, active_raid=active_raid)

def raid_generate_counts_spec_encounter(spec, encounter, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    # read from the db
    spec_slug = slugify.slugify(unicode(spec))
    encounter_slug = slugify.slugify(unicode(encounter))
    difficulty_slug = slugify.slugify(unicode(difficulty))
    active_raid_slug = slugify.slugify(unicode(active_raid))

    key_slug = "%s-%s-%s-%s" % (spec_slug, encounter_slug, difficulty_slug, active_raid_slug)
    key = ndb.Key('RaidCounts', key_slug)
    
    rc = key.get()
    if rc == None:
        return [0], 0.0, ""
    data = json.loads(rc.data)
    counts = data["counts"]
    max_found = data["max_found"]
    max_link = data["max_link"]

    return counts, max_found, max_link


def raid_generate_counts_spec(spec, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    counts = {}
    max_found = {}
    max_link = {}
    raid_encounters = get_raid_encounters(active_raid)
    for k, v in raid_encounters.iteritems():
        counts[k], max_found[k], max_link[k] = raid_generate_counts_spec_encounter(spec, k, difficulty=difficulty, active_raid=active_raid)
    return counts, max_found, max_link
        

def raid_generate_counts(difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    counts = {}
    max_found = {}
    max_link = {}    
    for s in specs:
        counts[s], max_found[s], max_link[s] = raid_generate_counts_spec(s, difficulty=difficulty, active_raid=active_raid)

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

# all abrogated by the new pvp backend
def process_pvp_counts_for_a_mode(actual_mode):
    return


def process_pvp_counts_overall():
    return
        
def process_pvp_counts():
    return

## end getting data out into counts



## html generation start

##   generating common reports

def affix_rotation_affixes(affixes):
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
    set_overall = construct_analysis(set_counts, sort_by="n")

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
    affixes_overall = construct_analysis(affix_counts, limit=3200*5) # look at all runs for affixes
    
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

        # df prepatch: skip logs with broken talents
        if "talents" in k:
            if len(k["talents"]) < 10:
                continue

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
        fight_id = 0
        if "reportID" in k:
            report_id = k["reportID"]
            if "fightID" in k:
                fight_id = k["fightID"]
            else:
                logging.info("no fight ID found!")

        if flatten:
            for element in names_in_set:
                add_this = tuple([element])
                metadata[add_this] += [[sort_value, 0, link_text, report_id, fight_id]]            
        else:
            metadata[add_this] += [[sort_value, 0, link_text, report_id, fight_id]]

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

def wcl_extract_gems(ranking, primordial=False):
    names_in_set = []
    name_id_icons = []
    
    for i, j in enumerate(ranking["gear"]):
        if "gems" in j:
            for each_gem in j["gems"]:
                if primordial: # filter just to primordial
                    if each_gem["id"] not in primordial_stones:
                       continue
                else: # filter out primordials
                    if each_gem["id"] in primordial_stones:
                       logging.info("filtering out primordials")                        
                       continue                    
                names_in_set += [each_gem["id"]]
                name_id_icons += [each_gem]

    return names_in_set, name_id_icons

def wcl_extract_tier(ranking):
    names_in_set = []
    name_id_icons = []
    
    for i, j in enumerate(ranking["gear"]):
        if "id" in j:
            if j["id"] in tier_items:
                names_in_set += [j["id"]]
                name_id_icons += [j]

    return names_in_set, name_id_icons

def wcl_extract_embellishments(ranking):
    names_in_set = []
    name_id_icons = []

    for i, j in enumerate(ranking["gear"]):
        if "id" in j:
            if j["id"] in embellished_items:
                names_in_set += [j["id"]]
                name_id_icons += [j]

    return names_in_set, name_id_icons

def wcl_extract_crafted(ranking):
    names_in_set = []
    name_id_icons = []

    for i, j in enumerate(ranking["gear"]):
        if "id" in j:
            if j["id"] in crafted_items:
                names_in_set += [j["id"]]
                name_id_icons += [j]

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

def wcl_primordials(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_gems(e, primordial=True),
                     only_use_ids=True,
                     flatten=True)

def wcl_primordial_builds(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_gems(e, primordial=True),                     
                     only_use_ids=True)

def wcl_tier_items(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_tier(e),
                     only_use_ids=True,
                     flatten=True)

def wcl_tier_builds(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_tier(e),                     
                     only_use_ids=True)

def wcl_embellished_items(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_embellishments(e),
                     only_use_ids=True,
                     flatten=True)

def wcl_embellished_builds(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_embellishments(e),                     
                     only_use_ids=True)

def wcl_crafted_items(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_crafted(e),
                     only_use_ids=True,
                     flatten=True)

def wcl_crafted_builds(rankings):
    return wcl_parse(rankings,
                     lambda e: wcl_extract_crafted(e),                     
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

# now operates on talent_ids, not spell_ids
# require_in needs to be talent_ids, not spell_ids
def canonical_talent_order(talent_ids, require_in=None):
    # talent_order has the talent order (using talent_ids!)
    d = {k:v for v,k in enumerate(talent_id_order)}

    talent_ids.sort(key=d.get)

    filtered_talent_ids = []
    for tid in talent_ids:
        if require_in is not None:
            if tid not in require_in:
                continue
        filtered_talent_ids += [tid]
   
    return filtered_talent_ids

# now operates on talent_ids, not spell_ids
def wcl_extract_talents(ranking, require_in=None):
    names_in_set = []
    name_id_icons = []

    for i, j in enumerate(ranking["talents"]):
        if j["talentID"] == 0: # talents are now numbers, not strings
            continue
        
        talent_id = j["talentID"]

        if require_in != None:
            if talent_id not in require_in:
                continue
        
        names_in_set += [talent_id] # need to make it a string since every other id is a string
        name_id_icons += [j]

    return canonical_talent_order(names_in_set, require_in), name_id_icons

# given a ranking, extra talent ids
def wcl_get_talent_ids(ranking):
    talent_ids = {}
    points = 0
    for i, j in enumerate(ranking["talents"]):
        if "talentID" not in j: # skip if we lack talent id info
            continue
        if j["talentID"] == 0: # skip empty talents
            continue
        if "points" not in j: # skip if we lack point info
            continue

        tid = j["talentID"]
        
        if tid not in talent_ids:
            talent_ids[tid] = 0
        talent_ids[tid] = j["points"]
        points += j["points"]

#    logging.info(talent_ids)
    if points < 52:
        logging.info("fewer than 52 points (only %d) in a talent string (%d, %d, %s)" % (points, ranking["class"], ranking["spec"], ranking["reportID"]))

    return talent_ids

# given a reportID, find that in rankings
def wcl_find_report(reportID, fightID, rankings):
    for v in rankings:
        if v["reportID"] == reportID:
            if v["fightID"] == fightID:
                return v
    return None

# get talent strings for the max logs in parsed
def wcl_get_talent_strings(parsed, rankings, spec_name):
    talent_strings = []
    for k in parsed:
        if len(k)<3:
            continue
        if len(k[2])<1:
            continue
        if len(k[2][0])<4:
            continue       
        tid = wcl_get_talent_ids(wcl_find_report(k[2][0][3], k[2][0][4], rankings))
        if tid == {}:
            talent_strings += [""] # no talent string available
            continue
        talent_strings += [encode_talent_string(tid, spec_name)]

    return talent_strings  

def wcl_talents(rankings, require_in=None):
    return wcl_parse(rankings, lambda e: wcl_extract_talents(e, require_in), is_sorted=False)

def wcl_talents_top(rankings, require_in=None):
    return wcl_parse(rankings, lambda e: wcl_extract_talents(e, require_in), is_sorted=False, flatten=True)

# we want enchants for particular set of slots
def wcl_extract_enchants(ranking, slots, type="permanentEnchant"):
    names_in_set = []
    name_id_icons = []
    for i, j in enumerate(ranking["gear"]):
        if i in slots:
            if type in j:
                enchant_id = j[type]
                # collapse lower ranks into the highest rank
                if enchant_id in enchant_collapse:
                    enchant_id = enchant_collapse[enchant_id]
                names_in_set += [enchant_id]
                name_id_icons += [{"id":enchant_id}]

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

# go through extracted talents and find commonalities
# common talents must be in require_in if is not None
def identify_common_talents(talents):
    talent_lists = []
    for k in talents:
        talent_lists += [k[1]]
    if len(talent_lists)>0:
        common = set(talent_lists[0])
    else:
        common = set()
    for s in talent_lists[1:]:
        common.intersection_update(s)
    return set(common)

# go through extracted talents and remove the common set once it's been found
# return talents with the common talents removed
def remove_common_talents(talents, common):
    for k in talents:
        k[1] = tuple(canonical_talent_order(list(set(k[1]) - common)))
    return talents


# spec report generation
def gen_wcl_spec_report(spec, dungeon="all"):
    return base_gen_spec_report(spec, "mplus", dungeon)

def gen_wcl_raid_spec_report(spec, encounter="all", difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    return base_gen_spec_report(spec, "raid", encounter, difficulty=difficulty, active_raid=active_raid)

def base_gen_spec_report(spec, mode, encounter="all", difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    wcl_query = None

    if mode == "mplus":
        if encounter == "all":
            wcl_query = SpecRankings.query(SpecRankings.spec==spec)
        else:
            wcl_query = SpecRankings.query(SpecRankings.spec==spec,
                                           SpecRankings.dungeon==encounter)            
    elif mode=="raid":       
        if encounter == "all":
            wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec,
                                               SpecRankingsRaid.raid==active_raid)
        else:
            wcl_query = SpecRankingsRaid.query(SpecRankingsRaid.spec==spec,
                                               SpecRankingsRaid.encounter==encounter,
                                               SpecRankingsRaid.raid==active_raid)
           
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
                raid_ignore = get_raid_ignore(active_raid)
                if k.encounter in raid_ignore:
                    continue
            
        latest = json.loads(k.rankings)

        no_blanks = []
        # filter out reports that lack info (e.g. notalents)
        for kk in latest:
            if kk['talents'] == []:
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

            raid_encounters = get_raid_encounters(active_raid)
            
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

    gems, update_items = wcl_gems(rankings)
    items.update(update_items)
            
    gem_builds, update_items = wcl_gem_builds(rankings)
    items.update(update_items)

    # 9.2: bye bye shards
    # 10.0.7: hello primordials
    primordials = {}
    primordial_builds = {}
    primordials, update_items = wcl_primordials(rankings)
    items.update(update_items)
            
    primordial_builds, update_items = wcl_primordial_builds(rankings)
    items.update(update_items)

    tier_items, update_items = wcl_tier_items(rankings)
    items.update(update_items)

    tier_builds, update_items = wcl_tier_builds(rankings)
    items.update(update_items)    

    embellished_items, update_items = wcl_embellished_items(rankings)
    items.update(update_items)

    embellished_builds, update_items = wcl_embellished_builds(rankings)
    items.update(update_items)   

    crafted_items, update_items = wcl_crafted_items(rankings)
    items.update(update_items)

    crafted_builds, update_items = wcl_crafted_builds(rankings)
    items.update(update_items)   
    
    enchants = {}
    enchant_ids = {}

    # slots are one LESS than what it is for macros because 0 indexing
    enchants["weapons"], update_enchant_ids = wcl_enchants(rankings, [15, 16])
    enchant_ids.update(update_enchant_ids)

    enchants["chests"], update_enchant_ids = wcl_enchants(rankings, [4])
    enchant_ids.update(update_enchant_ids)

    enchants["wrists"], update_enchant_ids = wcl_enchants(rankings, [8])
    enchant_ids.update(update_enchant_ids)

    enchants["leg"], update_enchant_ids = wcl_enchants(rankings, [6])
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

    talents_container = {}
    talents_container["common"] = {}
    
    # wcl_parse returns [n, (talents), [[max_n, band, text, report]]]
    talents, update_spells = wcl_talents(rankings)   
    talents_container["talents"] = talents
    spells.update(update_spells)
    talents_container["talents_string"] = wcl_get_talent_strings(talents, rankings, spec)

    talents_common = identify_common_talents(talents)
    talents = remove_common_talents(talents, talents_common)
    talents_container["common"]["talents"] = canonical_talent_order(list(talents_common))
    
#    talents_top, _ = wcl_talents_top(rankings, require_in=priority_talents)
#    talents_container["top"] = talents_top
#    talents_container["top_string"] = wcl_get_talent_strings(talents_top, rankings, spec)        

#    talents_priority, _ = wcl_talents(rankings, require_in=priority_talents)
#    talents_container["priority"] = talents_priority
#    talents_container["priority_string"] = wcl_get_talent_strings(talents_priority, rankings, spec)    

    talents_class, _ = wcl_talents(rankings, require_in=talent_id_class)
    talents_container["class"] = talents_class
    talents_container["class_string"] = wcl_get_talent_strings(talents_class, rankings, spec)

    talents_class_common = identify_common_talents(talents_class)
    talents_class = remove_common_talents(talents_class, talents_class_common)
    talents_container["common"]["class"] = canonical_talent_order(list(talents_class_common))
    
    talents_spec, _ = wcl_talents(rankings, require_in=talent_id_spec)
    talents_container["spec"] = talents_spec
    talents_container["spec_string"] = wcl_get_talent_strings(talents_spec, rankings, spec)

    talents_spec_common = identify_common_talents(talents_spec)
    talents_spec = remove_common_talents(talents_spec, talents_spec_common)
    talents_container["common"]["spec"] = canonical_talent_order(list(talents_spec_common))
    
    talents_class_active, _ = wcl_talents(rankings, require_in=(class_active))
    talents_container["class_active"] = talents_class_active
    talents_container["class_active_string"] = wcl_get_talent_strings(talents_class_active, rankings, spec)    
   
    talents_spec_active, _ = wcl_talents(rankings, require_in=(spec_active))
    talents_container["spec_active"] = talents_spec_active
    talents_container["spec_active_string"] = wcl_get_talent_strings(talents_spec_active, rankings, spec)    

    # raid won't have a max_maxima and a min_maxima (could use dps but not much point)
    # raid will return available_difficulty in max_maxima
    return len(rankings), n_uniques, max_maxima, min_maxima, talents_container, gear, enchants, gems, gem_builds, primordials, primordial_builds, spells, items, enchant_ids, tier_items, tier_builds, embellished_items, embellished_builds, crafted_items, crafted_builds

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
        deferred.defer(api_affixes_dungeons, af)
        

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
def render_raid_stats(encounter, prefix="", difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    specs_report, spec_stats = gen_raid_specs_role_package(encounter, difficulty=difficulty, active_raid=active_raid)

    encounter_slugs = {}
    raid_canonical_order = get_raid_canonical_order(active_raid)
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
                               active_raid = active_raid,
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
    n_parses, n_uniques, key_max, key_min, talents, gear, enchants, gems, gem_builds, primordials, primordial_builds, spells, items, enchant_ids, tier_items, tier_builds, embellished_items, embellished_builds, crafted_items, crafted_builds = gen_wcl_spec_report(spec, dungeon)

    talent_metadata = {}
    talent_metadata["talents_to_spells"] = talents_to_spells
    talent_metadata["class"] = talent_id_class
    talent_metadata["spec"] = talent_id_spec
    talent_metadata["heights"] = talent_id_heights    

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
                               talents = talents,
                               affixes = affixes,
                               gear = gear,
                               enchants = enchants,
                               enchant_ids = enchant_ids,
                               enchant_mapping = enchant_mapping,
                               talent_metadata = talent_metadata,
                               gems = gems,
                               gem_builds = gem_builds,
                               primordials = primordials,
                               primordial_builds = primordial_builds,                               
                               tier_items = tier_items,
                               tier_builds = tier_builds,   
                               embellished_items = embellished_items,
                               embellished_builds = embellished_builds,
                               crafted_items = crafted_items,
                               crafted_builds = crafted_builds,                            
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

def render_raid_index(encounter="all", difficulty=MAX_RAID_DIFFICULTY, prefix="", active_raid=""):
    template = env.get_template("raid-index.html")
    
    specs_report, spec_stats = gen_raid_specs_role_package(encounter, difficulty=difficulty, active_raid=active_raid)
    
    encounter_slugs = {}
    raid_canonical_order = get_raid_canonical_order(active_raid)    
    for e in raid_canonical_order:
        encounter_slugs[e] = slugify.slugify(unicode(e))

    encounter_slug = slugify.slugify(unicode(encounter))
   
    tankstl = gen_raid_spec_tier_list(specs_report, "Tanks",
                                      encounter_slug=encounter_slug, prefix=prefix,
                                      difficulty=difficulty,
                                      active_raid=active_raid)
    healerstl = gen_raid_spec_tier_list(specs_report, "Healers",
                                        encounter_slug=encounter_slug,
                                        prefix=prefix,
                                        difficulty=difficulty,
                                        active_raid=active_raid)
    meleetl = gen_raid_spec_tier_list(specs_report, "Melee",
                                      encounter_slug=encounter_slug,
                                      prefix=prefix,
                                      difficulty=difficulty,
                                      active_raid=active_raid)
    rangedtl = gen_raid_spec_tier_list(specs_report, "Ranged",
                                       encounter_slug=encounter_slug,
                                       prefix=prefix,
                                       difficulty=difficulty,
                                       active_raid=active_raid)
  
    active_page = "raid-index"
    if encounter != "all":
        active_page = "raid-" + encounter_slug

    raid_short_names = get_raid_short_names(active_raid)    
    raid_ignore = get_raid_ignore(active_raid)
    
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
                               active_raid=active_raid,
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
    pvp_canonical_order = ["solo-shuffle", "2v2", "3v3", "rbg"]
    pvp_pretty_names = {}
    pvp_pretty_names["solo-shuffle"] = "Solo Shuffle"    
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

    pvp_canonical_order = ["solo-shuffle", "2v2", "3v3", "rbg"]
    pvp_pretty_names = {}
    pvp_pretty_names["solo-shuffle"] = "Solo Shuffle"    
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

def render_wcl_raid_spec(spec, encounter="all", prefix="", difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    logging.info("%s %s %s" % (spec, encounter, difficulty))
    spec_slug = slugify.slugify(unicode(spec))
    affixes = "N/A"
    n_parses, n_uniques, available_difficulty, _, talents,gear, enchants, gems, gem_builds, primordials, primordial_builds, spells, items, enchant_ids, tier_items, tier_builds, embellished_items, embellished_builds, crafted_items, crafted_builds = gen_wcl_raid_spec_report(spec, encounter, difficulty=difficulty, active_raid=active_raid)

    talent_metadata = {}
    talent_metadata["talents_to_spells"] = talents_to_spells
    talent_metadata["class"] = talent_id_class
    talent_metadata["spec"] = talent_id_spec
    talent_metadata["heights"] = talent_id_heights    

    encounter_pretty = encounter
    if encounter_pretty == "all":
        encounter_pretty = "All Bosses"

    encounter_slugs = {}
    raid_canonical_order = get_raid_canonical_order(active_raid)
    for e in raid_canonical_order:
        encounter_slugs[e] = slugify.slugify(unicode(e))

    encounter_slug = slugify.slugify(unicode(encounter))
    difficulty_slug = slugify.slugify(unicode(difficulty))

    metric = "dps"
    if spec in healers:
        metric = "hps"

    raid_short_names = get_raid_short_names(active_raid)    
        
    template = env.get_template('spec-raid.html')
    rendered = template.render(title = "%s - %s - %s" % (encounter_pretty, spec, RAID_NAME),
                               active_section = "raid",
                               spec = spec,
                               spec_slug = spec_slug,
                               archetype = get_archetype(spec),
                               active_page = spec_slug + "-" + encounter_slug + "-" + difficulty_slug,
                               talents = talents,
                               affixes = affixes,
                               gear = gear,
                               spells = spells,
                               items = items,
                               enchants = enchants,
                               enchant_ids = enchant_ids,
                               enchant_mapping = enchant_mapping,
                               talent_metadata = talent_metadata,                               
                               metric = metric,
                               gems = gems,
                               gem_builds = gem_builds,
                               primordials = primordials,
                               primordial_builds = primordial_builds,
                               tier_items = tier_items,
                               tier_builds = tier_builds,
                               embellished_items = embellished_items,
                               embellished_builds = embellished_builds,
                               crafted_items = crafted_items,
                               crafted_builds = crafted_builds,         
                               raid_canonical_order = raid_canonical_order,
                               encounter_slugs = encounter_slugs,
                               raid_short_names = raid_short_names,
                               n_parses = n_parses,
                               n_uniques = n_uniques,
                               encounter = encounter,
                               encounter_slug = encounter_slug,
                               encounter_pretty = encounter_pretty,
                               active_raid=active_raid,
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

    write_to_storage(filename_slug + ".html", rendered)

def render_and_write_compositions(af):
    rendered = render_compositions(af)
    
    filename_slug = slugify.slugify(unicode(af))

    affix_slug = slugify.slugify(unicode(af))

    write_to_storage("compositions-" + filename_slug + ".html", rendered)


def render_and_write_stats(af):
    rendered = render_stats(af)
    
    filename_slug = slugify.slugify(unicode(af))

    write_to_storage("stats-" + filename_slug + ".html", rendered)

def render_and_write_raid_stats(encounter, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    rendered = render_raid_stats(encounter, difficulty, active_raid=active_raid)

    filename_slug = ""
    if active_raid != "nathria":
        filename_slug += active_raid + "-"
    
    filename_slug += slugify.slugify(unicode(encounter))

    if difficulty == "Heroic":
        filename_slug += "-heroic"
            
    raid_write_to_storage("raid-stats-" + filename_slug + ".html", rendered)
    
def write_overviews():
    affixes_to_write = []
    affixes_to_write += ["All Affixes"]
    affixes_to_write += known_affixes()

    for af in affixes_to_write:
        deferred.defer(render_and_write, af)

    for af in affixes_to_write:
        deferred.defer(render_and_write_compositions, af)

    for af in affixes_to_write:
        deferred.defer(render_and_write_stats, af)

    deferred.defer(write_apis)


def create_spec_overview(s, d="all"):
    spec_slug = slugify.slugify(unicode(s))
    rendered = render_wcl_spec(s, dungeon=d)
    if d == "all":
        filename = "%s.html" % (spec_slug)
    else:
        dungeon_slug = slugify.slugify(unicode(d))
        filename = "%s-%s.html" % (spec_slug, dungeon_slug)
    deferred.defer(write_to_storage, filename, rendered)


def create_pvp_pages():
    global pvp_modes
    modes_to_generate = []
    modes_to_generate += ["all"]
    modes_to_generate += pvp_modes
    
    for mode in modes_to_generate:
        rendered = render_pvp_index(mode)
        filename = "%s.html" % mode
        if mode == "all":
            filename = "index.html"

        deferred.defer(pvp_write_to_storage, filename, rendered)

    write_pvp_stats()
    write_pvp_apis()


def write_pvp_stats():
    global pvp_modes
    modes_to_generate = []
    modes_to_generate += ["all"]
    modes_to_generate += pvp_modes
    
    for mode in modes_to_generate:
        rendered = render_pvp_stats(mode)
        filename = "pvp-stats-%s.html" % mode

        deferred.defer(pvp_write_to_storage, filename, rendered)
    
def write_pvp_apis():
    global pvp_modes
    modes_to_generate = []
    modes_to_generate += ["all"]
    modes_to_generate += pvp_modes
    
    for mode in modes_to_generate:
        rendered = api_pvp_specs(mode)
        filename = mode
        deferred.defer(write_api_json, "api/v0/pvp/" + filename, rendered)


def write_api_json(filename, rendered):
    main_write_to_storage(filename,
                          rendered,
                          cache_control="public, max-age=28800",
                          content_type="application/json")
        
def create_main_pages():
    main_pages = [["index.html", render_main_index]]

    for (filename, render_function) in main_pages:
        rendered = render_function()
        deferred.defer(main_write_to_storage, filename, rendered)
    
def create_static_pages():
    static_pages = [["privacy.html", render_privacy],
                    ["faq.html", render_faq]]

    for (filename, render_function) in static_pages:
        rendered = render_function()
        deferred.defer(main_write_to_storage, filename, rendered)
    
def create_raid_index(difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    rendered = render_raid_index(difficulty=difficulty, active_raid=active_raid)
    filename = active_raid + ".html"
    if difficulty == "Heroic":
        filename = active_raid + "-heroic.html"

    raid_write_to_storage(filename, rendered)

    # also write as index 
    if difficulty == MAX_RAID_DIFFICULTY:
        filename = "index.html"
        raid_write_to_storage(filename, rendered)

    # if it's heroic week then heroic is also the index
    if MAX_RAID_DIFFICULTY == "Heroic":
        raid_write_to_storage("index.html", rendered)

    encounters_to_write = []
    raid_canonical_order = get_raid_canonical_order(active_raid)    
    encounters_to_write += raid_canonical_order

    for encounter in encounters_to_write:
        rendered = render_raid_index(encounter, difficulty, active_raid=active_raid)
        filename = ""
        if active_raid != "nathria": # nathria doesn't prefix, other raids do
            filename = active_raid + "-"
        filename += slugify.slugify(unicode(encounter))
        if difficulty == "Heroic":
            filename += "-heroic"
        filename += ".html"
        raid_write_to_storage(filename, rendered)

    # make sure to include all in stats
    encounters_to_write += ["all"]
    for encounter in encounters_to_write:
        deferred.defer(render_and_write_raid_stats, encounter, difficulty, active_raid=active_raid)
    
def create_raid_spec_overview(s, e="all", difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    spec_slug = slugify.slugify(unicode(s))
    rendered = render_wcl_raid_spec(s, encounter=e, difficulty=difficulty, active_raid=active_raid)
    filename_slug = ""
    if active_raid != "nathria":
        filename_slug += active_raid + "-"
    if e == "all":
        filename_slug += "%s" % (spec_slug)
    else:
        encounter_slug = slugify.slugify(unicode(e))
        filename_slug += "%s-%s" % (spec_slug, encounter_slug)

    # special handling for heroic week -- write both files
    if MAX_RAID_DIFFICULTY == "Heroic":
        filename = filename_slug + ".html"
        raid_write_to_storage(filename, rendered)
    
    if difficulty == "Heroic":
        filename_slug += "-heroic"
    filename = filename_slug + ".html"
    raid_write_to_storage(filename, rendered)

def write_spec_overviews():
    for s in specs:
        deferred.defer(create_spec_overview, s, "all")

        for k, v in dungeon_encounters.iteritems():
            deferred.defer(create_spec_overview, s, k)


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
    deferred.defer(write_api_dungeon_ease)
    deferred.defer(write_api_dungeon_specs)
    deferred.defer(write_api_affix_tier_list)

    deferred.defer(process_dungeon_ease_tier_lists_for_all_known_affixes)

    deferred.defer(write_api_dungeon_ease_overall)


def write_raid_spec_overviews(active_raid=""):
    # update the counts
    process_generate_raid_counts(active_raid=active_raid)    
    
    # write the index page
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    for d in difficulties:
        deferred.defer(create_raid_index, d, active_raid=active_raid)

    for s in specs:
        for d in difficulties:
            deferred.defer(create_raid_spec_overview, s, "all", d, active_raid=active_raid)

            raid_encounters = get_raid_encounters(active_raid)
            for k, v in raid_encounters.iteritems():
                deferred.defer(create_raid_spec_overview, s, k, d, active_raid=active_raid)


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
    kind_list = [DungeonAffixRegion, KnownAffixes, SpecRankings, SpecRankingsRaid, DungeonEaseTierList, PvPCounts, PvPLadderStats, RaidCounts ]
    for a_kind in kind_list:
        kind_keys = a_kind.gql("").fetch(keys_only=True)
        ndb.delete_multi(kind_keys)
    return "resetDB for " + str(kind_list)

# just elements for fated raids
def reset_fated_db():
    kind_list = [ SpecRankingsRaid, RaidCounts ]
    for a_kind in kind_list:
        kind_keys = a_kind.gql("").fetch(keys_only=True)
        ndb.delete_multi(kind_keys)
    return "resetDB for " + str(kind_list)

# just elements for prepatch
def reset_prepatch_db():
    kind_list = [ SpecRankings, SpecRankingsRaid, RaidCounts ]
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

    active_raid = "amirdrassil"
    raid_canonical_order = amirdrassil_canonical_order
                    
    for e in raid_canonical_order:
        if slugify.slugify(unicode(e)) in destination:
            encounter = e
            
    if "index" in destination:
        return render_raid_index(prefix=prefix, difficulty=difficulty, active_raid=active_raid)

    if "stats" in destination:
        return render_raid_stats(encounter, prefix=prefix, difficulty=difficulty, active_raid=active_raid)
    
    if spec == "all":
        return render_raid_index(prefix=prefix, encounter=encounter, difficulty=difficulty, active_raid=active_raid)            
            

    return render_wcl_raid_spec(spec, encounter=encounter, prefix=prefix, difficulty=difficulty, active_raid=active_raid)


def test_main_view(destination):
    prefix = "main?goto="
    if "index" in destination:
        return render_main_index(prefix=prefix)

    if "privacy" in destination:
        return render_privacy(prefix=prefix)

    if "faq" in destination:
        return render_faq(prefix=prefix)    

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
    # filter to the last 4 weeks, or latest patch, whichever is sooner

    global latest_patch_us
    latest_patch = latest_patch_us
    
    now = datetime.datetime.now()

    latest_patch_mkt = time.mktime(latest_patch.timetuple())
    four_weeks_ago = time.mktime(now.timetuple())-4*7*60*60*24

    filter_back_to = four_weeks_ago
    if latest_patch_mkt > four_weeks_ago:
        filter_back_to = latest_patch_mkt
    
    wcl_date = "date."
    wcl_date += "%d000" % (filter_back_to)
    wcl_date += "." + "%d000" % (time.mktime(now.timetuple())+60*24)    
    
    url = "https://www.warcraftlogs.com:443/v1/rankings/encounter/%d?partition=%d&class=%d&spec=%d&page=%d&filter=%s&includeCombatantInfo=true&api_key=%s" % (encounterId, season, class_id, spec, page, wcl_date, api_key)
    logging.info(url)

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

    # if there's no data, just get out
    if "rankings" not in rankings:
        return

    lpmkt_us = time.mktime(latest_patch_us.timetuple())*1000
    lpmkt_eu = time.mktime(latest_patch_eu.timetuple())*1000
    lpmkt_kr = time.mktime(latest_patch_kr.timetuple())*1000
    lpmkt_tw = time.mktime(latest_patch_tw.timetuple())*1000
        
    for k in rankings["rankings"]:
        # filtering for patches that change talents
        if "regionName" not in k:
            continue
        
        if "startTime" not in k:
            continue
        
        if k["regionName"] == "US":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_us:
                continue

        if k["regionName"] == "EU":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_eu:
                continue

        if k["regionName"] == "KR":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_kr:
                continue
            
        if k["regionName"] == "TW":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_tw:
                continue
            
        # add the log
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
    # prepatch - a few days after since initial logs were mesesed up with talents
    global latest_patch_us
    latest_patch = latest_patch_us
    
    now = datetime.datetime.now()

    latest_patch_mkt = time.mktime(latest_patch.timetuple())
    four_weeks_ago = time.mktime(now.timetuple())-4*7*60*60*24        
    
    filter_back_to = four_weeks_ago
    if latest_patch_mkt > four_weeks_ago:
        filter_back_to = latest_patch_mkt
    
    wcl_date = "date."
    wcl_date += "%d000" % (filter_back_to)
    wcl_date += "." + "%d000" % (time.mktime(now.timetuple())+60*24)

    url = "https://www.warcraftlogs.com:443/v1/rankings/encounter/%d?difficulty=%d&partition=%d&class=%d&spec=%d&page=%d&filter=%s&metric=%s&includeCombatantInfo=true&api_key=%s&partition=%s" % (encounterId, difficulty, season, class_id, spec, page, wcl_date, metric, api_key, season)

    logging.info(url)
    
    result = urlfetch.fetch(url, deadline=60)
    data = json.loads(result.content)
    return data


def update_wcl_raid_rankings(spec, encounter, page=1, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    
    if spec not in wcl_specs:
        return "invalid spec [%s]" % spec
    spec_key = slugify.slugify(unicode(spec))
    raid_encounters = get_raid_encounters(active_raid)
    if encounter not in raid_encounters:
        return "invalid encounter [%s]" % encounter
    encounter_id = raid_encounters[encounter]
    encounter_slug = slugify.slugify(unicode(encounter))

    logging.info("%s %s %s %s [%s]" % (spec, encounter, difficulty, page, active_raid))
    
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
        logging.info("No parses found for %s %s %s %s (page %d)." % (active_raid, spec, difficulty, encounter, page))
        if difficulty == "Heroic" and page == 1: # fall back to normal only if no heroic parses at all
            logging.info("Falling back to Normal")
            # queue up all pages for normal
            i = 1
            while (i <= 5):
                deferred.defer(update_wcl_raid_rankings, spec, encounter, page=i, difficulty="Normal", active_raid=active_raid)
                i += 1
            return True
        return False # otherwise fail
    else:
        logging.info("%d parses found for %s %s %s (page %d)" % (len(rankings["rankings"]), spec, difficulty, encounter, page))

    lpmkt_us = time.mktime(latest_patch_us.timetuple())*1000
    lpmkt_eu = time.mktime(latest_patch_eu.timetuple())*1000
    lpmkt_kr = time.mktime(latest_patch_kr.timetuple())*1000
    lpmkt_tw = time.mktime(latest_patch_tw.timetuple())*1000
        
    for k in rankings["rankings"]:
        # filtering for patches that change talents
        if "regionName" not in k:
            continue
        
        if "startTime" not in k:
            continue

        if k["regionName"] == "US":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_us:
                continue

        if k["regionName"] == "EU":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_eu:
                continue

        if k["regionName"] == "KR":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_kr:
                continue
       
        if k["regionName"] == "TW":
            # if the log happened before patch, skip it
            if k["startTime"] < lpmkt_tw:
                continue

        # add the log
        aggregate += [k]

    key = ndb.Key('SpecRankingsRaid', "%s-%s-%s-%s-%d" % (spec_key, encounter_slug, slugify.slugify(unicode(difficulty)), active_raid, page))
    sr = SpecRankingsRaid(key=key)
    sr.spec = spec
    sr.encounter = encounter
    sr.difficulty = difficulty
    sr.raid = active_raid
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
            deferred.defer(update_wcl_rankings, spec, k, page=i)
            i += 1

    return spec, spec_key,  wcl_specs[spec]



# get the data for dungeons
def update_wcl_update():
    for i, s in enumerate(specs):
        deferred.defer(update_wcl_spec, s)


def update_wcl_update_subset(subset):
    for i, s in enumerate(subset):
        deferred.defer(update_wcl_spec, s)
        

def update_wcl_raid_spec(spec, difficulty=MAX_RAID_DIFFICULTY, active_raid=""):
    logging.info("%s %s [%s]" % (spec, difficulty, active_raid))
    if spec not in wcl_specs:
        return "invalid spec [%s]" % spec
    spec_key = slugify.slugify(unicode(spec))

    aggregate = []
    raid_encounters = get_raid_encounters(active_raid)
    for k, v in raid_encounters.iteritems():
        i = 1
        while (i <= 5):
            deferred.defer(update_wcl_raid_rankings, spec, k, page=i, difficulty=difficulty,
                           active_raid=active_raid)
            i += 1

    return spec, spec_key,  wcl_specs[spec]
        
# update wcl for raids
def update_wcl_raid_update(active_raid=""):
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    for i, s in enumerate(specs):
        for d in difficulties:
            deferred.defer(update_wcl_raid_spec, s, d, active_raid)

def update_wcl_raid_update_subset(subset, active_raid=""):
    difficulties = ["Heroic"]
    if MAX_RAID_DIFFICULTY == "Mythic":
        difficulties = ["Mythic", "Heroic"]
    
    for i, s in enumerate(subset):
        for d in difficulties:
            deferred.defer(update_wcl_raid_spec, s, d, active_raid)
    
# update all the wcl for dungeons
def update_wcl_all():
    update_wcl_update()
    deferred.defer(write_spec_overviews)

# update pvp ladder stats

# region = us or eu
# mode = 2v2 or 3v3 or rbg or solo-shuffle

# alway use internal api
def _pvp_rankings_internal(region, mode):
    global internal_api
    url = "%s/%s.json" % (internal_api, mode)
    logging.info("fetching %s" % url)
    result = urlfetch.fetch(url, deadline=60)
    data = json.loads(result.content)
    return data

def _pvp_rankings(region, mode):
    if region == "us": # all regions in one since we pre-process all now
        return _pvp_rankings_internal(region, mode)
    else:
        return None

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
    use_these_modes = pvp_modes
    use_these_modes += ["all"]
    for region in ["us"]: # always us since we're using an internal api that aggregates for us
        for mode in use_these_modes:
            deferred.defer(update_pvp_rankings, region, mode)

## handlers

class UpdateCurrentDungeons(webapp2.RequestHandler):
    def get(self):
        global dungeons, regions
        self.response.headers['Content-Type'] = 'text/plain'
        update_current()
        self.response.write("Updates queued.")

class OnlyGenerateHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing templates to cloud storage...")
        deferred.defer(write_overviews)


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
#        update_wcl_update_subset(["Havoc Demon Hunter", "Fury Warrior"])
#        update_wcl_update_subset(["Survival Hunter"])
#        update_wcl_update_subset(["Arms Warrior"])
#        update_wcl_update_subset(["Feral Druid"])
#        update_wcl_update_subset(["Guardian Druid"])
#        update_wcl_update_subset(["Blood Death Knight"])        
#        update_wcl_update_subset(["Protection Warrior"])        
#        update_wcl_update_subset(["Devastation Evoker"])
#        update_wcl_update_subset(["Balance Druid"])
#        update_wcl_update_subset(["Marksmanship Hunter"])
#        update_wcl_update_subset(["Augmentation Evoker"])        
#        update_wcl_update_subset(["Shadow Priest"])
#        update_wcl_update_subset(["Demonology Warlock"])
#        update_wcl_update_subset(["Outlaw Rogue"])
        update_wcl_update_subset(["Mistweaver Monk"])        

class WCLGetRankingsRaidOnly(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
        raids_to_update = determine_raids_to_update()
        for r in raids_to_update:
            update_wcl_raid_update(active_raid = r)

class WCLGetRankingsRaidOnlyAll(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates for all known raids...\n")
        raids_to_update = known_raids
        for r in raids_to_update:
            update_wcl_raid_update(active_raid = r)            

class TestWCLGetRankingsRaid(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Queueing updates...\n")
#        update_wcl_raid_update_subset(["Havoc Demon Hunter", "Fury Warrior"])
#        update_wcl_raid_update_subset(["Survival Hunter"], active_raid="nathria")
#        update_wcl_raid_update_subset(["Survival Hunter"], active_raid="sanctum")
#        update_wcl_raid_update_subset(["Survival Hunter"], active_raid="sepulcher")
#        update_wcl_raid_update_subset(["Devastation Evoker"], active_raid="vault")
#        update_wcl_raid_update_subset(["Balance Druid"], active_raid="vault")
#        update_wcl_raid_update_subset(["Feral Druid"], active_raid="vault")
#        update_wcl_raid_update_subset(["Shadow Priest"], active_raid="vault")
#        update_wcl_raid_update_subset(["Demonology Warlock"], active_raid="aberrus")
        update_wcl_raid_update_subset(["Augmentation Evoker"], active_raid="amirdrassil")
#        update_wcl_raid_update_subset(["Outlaw Rogue"], active_raid="vault")                                

class WCLGenHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing WCL HTML...\n")
        deferred.defer(write_spec_overviews)

class WCLRaidGenHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing WCL Raid HTML...\n")
        raids_to_generate = determine_raids_to_generate()
        for r in raids_to_generate:
            deferred.defer(write_raid_spec_overviews, active_raid = r)

class WCLRaidGenHTMLAll(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing WCL Raid HTML for all known raids...\n")
        raids_to_generate = known_raids
        for r in raids_to_generate:
            deferred.defer(write_raid_spec_overviews, active_raid = r)

class GenStaticHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing Static HTML pages...\n")
        deferred.defer(create_static_pages)

class GenMainHTML(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing Main HTML pages...\n")
        deferred.defer(create_main_pages)

class GenPVP(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing PvP HTML pages...\n")
        deferred.defer(create_pvp_pages)

class GenAPIs(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Writing APIs...\n")
        deferred.defer(write_apis)

        
class TestCloudflarePurgeCache(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Testing cloudflare purge cache...\n")
        self.response.write(cloudflare_purge_cache("mplus.subcreation.net", "index.html"))

class TestResetDB(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Clearing db\n")
        self.response.write(reset_db())

class TestResetFatedDB(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Clearing db for fated\n")
        self.response.write(reset_fated_db())

class TestResetPrepatchDB(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Clearing db for prepatch\n")
        self.response.write(reset_prepatch_db())           

class TestResetSpecRankingsRaid(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write("Clearing spec_rankings_raid\n")
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
        process_generate_raid_counts_for_raids(known_raids) # crunch numbers for all
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

class APIPvPSoloShuffle(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("solo-shuffle"))        


class APIPvP3v3(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("3v3"))

        
class APIPvPRBG(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.write(api_pvp_specs("rbg"))     

app = webapp2.WSGIApplication([

    # called in cron jobs
    ## mplus
        ('/refresh/affixes', UpdateCurrentDungeons),
        ('/generate/affixes', OnlyGenerateHTML),
    
    ## dungeon top builds
        ('/refresh/dungeons', WCLGetRankingsOnly),
        ('/generate/dungeons', WCLGenHTML),         

    ## raid top builds
        ('/refresh/raids', WCLGetRankingsRaidOnly),
        ('/generate/raids', WCLRaidGenHTML),
    
    ## pvp
        ('/refresh/pvp', TestLudusPvP),
        ('/process/pvp', ProcessPvPCounts),
        ('/generate/pvp', GenPVP),        

    ## main page
        ('/generate/main', GenMainHTML),

    # manually called if needed
        ('/generate/static', GenStaticHTML), # make sure to call on migration

        # don't need to explicitly call, built into other functions
        # only here if needed for one off fixes to regen just part
        ('/process/dungeon_ease_tier_lists', ProcessDungeonEaseTierLists),
        ('/process/raid_counts', ProcessRaidCounts),

        # don't need to explicitly call, built into other functions
        # only here if needed for one off fixes to regen just part    
        ('/generate/apis', GenAPIs), # just mplus api, pvp api are incl generate/pvp

        # do this for all known_raids
        ('/refresh/raids_all', WCLGetRankingsRaidOnlyAll),
        ('/generate/raids_all', WCLRaidGenHTMLAll),
        
    # api functionality
        ('/api/dungeon_ease', APIDungeonEase),
        ('/api/mplus_specs', APIDungeonSpecs),
        ('/api/mplus_affixes', APIAffixEase),
        ('/api/dungeon_ease_overall', APIDungeonEaseOverall),
        ('/api/pvp/all', APIPvPAll),
        ('/api/pvp/solo-shuffle', APIPvPSoloShuffle),
        ('/api/pvp/2v2', APIPvP2v2),
        ('/api/pvp/3v3', APIPvP3v3),
        ('/api/pvp/rbg', APIPvPRBG),

    # testing
        ('/view', TestView),
        ('/raid', TestRaidView),
        ('/pvp', TestPvPView),    
        ('/main', TestMainView),    

        ('/test/known_affixes', KnownAffixesShow),
        ('/test/affixes', UpdateCurrentDungeons),
        ('/test/dungeons', TestWCLGetRankings),
        ('/test/raids', TestWCLGetRankingsRaid),
        ('/test/pvp', TestLudusPvP),
        ('/test/cloudflare_purge', TestCloudflarePurgeCache),
        ('/test/reset_db', TestResetDB),
        ('/test/reset_fated_db', TestResetFatedDB),
        ('/test/reset_prepatch_db', TestResetPrepatchDB),        
        ('/test/reset_spec_rankings_raid', TestResetSpecRankingsRaid),
    
        ], debug=True)
