import argparse
import logging
import os
import re
import requests
import signal
import sys
import time
import uuid
import warnings
import datetime
import json
from pprint import pprint
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from bs4 import BeautifulSoup, Tag
from deepdiff import DeepDiff
from tabulate import tabulate
from tinydb import TinyDB, Query, where


"""
##############################################################
#### SETUP ###################################################
##############################################################
"""


# database tables and directories
basedirectory = os.path.expanduser('~')+'/.subitoo/'
db = TinyDB(basedirectory+'data/database.json', create_dirs=True)
configs = db.table('configs', cache_size=0)
queries = db.table('queries', cache_size=0)
listings = db.table('listings', cache_size=0)

# notifications parameters
pushover_app_token = ""
pushover_user_key = ""

# notifications 'buffer'
notifications = []
sent_notifications_uids = []

# some flood prevention
seconds_between_queries = int(5)
seconds_between_pages = int(3)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-User": "?1",
    "Priority": "u=0, i",
    "TE": "trailers"
}

hades_headers = {
    "Accept": "*/*",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.subito.it",
    "Referer": "https://www.subito.it/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "X-Subito-Channel": "web"
}

#import ipdb
#ipdb.set_trace()

# configure some logging
logging.basicConfig(filename=basedirectory+'data/execution.log', level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


"""
##############################################################
#### CLASSES #################################################
##############################################################
"""


class Listing:
    def __init__(self, name, sold, shipping, price, url, location, uid, queryuid, imageurl):
        self.name = name.strip()
        self.sold = sold
        self.shipping = shipping
        self.price = price
        self.url = url
        self.location = location
        self.uid = uid
        self.queryuid = queryuid
        self.imageurl = imageurl


class NotificationPushover:
    def __init__(self, title, message, url, imageurl):
        self.title = title
        self.message = message
        self.url = url
        self.imageurl = imageurl


class SearchQuery:
    def __init__(self, name, url, pages, regex_match, min_price, max_price, skip_no_price, skip_sold, first_run):
        self.name = re.sub("[^a-zA-Z0-9-_]", "", name)
        self.url = url.strip()
        self.pages = pages
        self.regex_match = regex_match
        self.min_price = max([min_price, 1])
        self.max_price = min([max_price, 9999999])
        self.skip_no_price = skip_no_price
        self.skip_sold = skip_sold
        self.first_run = first_run
        self.uid = str(uuid.uuid4())
        self.enabled = True

    @staticmethod
    def get_printable_fields():
        return ['name', 'pages', 'min_price', 'max_price', 'enabled']


"""
##############################################################
#### ARGPARSE DESTINATIONS ###################################
##############################################################
"""


def subitoo_list(args):
    """Main command call from argparse"""
    quit_if_already_running()
    print_search_queries(args)


def subitoo_delete(args):
    """Main command call from argparse"""
    quit_if_already_running()
    delete_search_query(args.name)


def subitoo_enable(args):
    """Main command call from argparse"""
    search_query_change_status(args.name, True)


def subitoo_disable(args):
    """Main command call from argparse"""
    search_query_change_status(args.name, False)


def subitoo_run(args):
    """Main command call from argparse"""
    quit_if_already_running()
    set_running(True)
    # Check the homepage before start
    allgood = check_homepage()
    time.sleep(1)

    if allgood:
        try:
            for idx, q in enumerate(queries.search(Query().enabled == True)):
                execute_run(q)
                if idx > 0: time.sleep(seconds_between_queries)
        except Exception as e:
            msg = "{}".format(e)
            logging.fatal(msg)
            print(msg)

    set_running(False)


def check_homepage():
    """Check (not too hard) if any promotion is active on the Subito.it homepage or if the website access is forbidden"""

    # No point to check promotions when notifications are not configured
    if not is_pushover_enabled():
        return True

    # get homepage
    response = requests.get("https://www.subito.it/", headers=headers)
    html = response.text

    # find access denied text
    targets_to_search = [
        "access denied",
    ]

    if any(text.lower() in html.lower() for text in targets_to_search):
        errors = get_current_errors_number() + 1
        tinydb_upsert_field_value(configs, 'errors', errors)
        # 3 consecutive?
        if errors == 3:
            ntf = NotificationPushover("Subito.it error!", "Access denied from Subito.it", "", "")
            send_pushover_notification(ntf)
        return False
    else:
        tinydb_upsert_field_value(configs, 'errors', 0)

    # promotion already sent this week?
    current_yearweek = get_current_yearweek()
    promotion_config = configs.get(where('promotions').exists())
    if promotion_config and promotion_config.get('promotions') == current_yearweek:
        # This week we already sent a notification
        return True

    # proceed with the check
    targets_to_search = [
        "0,99 €",
        "0,99€",
        "spedizioni inpost scontate"
    ]
    # Check if any of the strings is found
    if any(text.lower() in html.lower() for text in targets_to_search):
        # Do not execute again for a week
        tinydb_upsert_field_value(configs, 'promotions', current_yearweek)
        # Send a notification
        message = "A promotion appears to be available on the homepage! Be sure to check it out!"
        ntf = NotificationPushover("Subito.it promotion!", message, "", "")
        send_pushover_notification(ntf)

    return True


def subitoo_add(args):
    """Main command call from argparse"""
    query = SearchQuery(args.name, args.url, args.pages, args.regex, args.min_price, args.max_price, args.skip_no_price, args.skip_sold, True)
    add_search_query(query)


def subitoo_configuration(args):
    """Main command call from argparse"""
    if args.PushoverKeys:
        set_pushover_keys(args.PushoverKeys.strip())


def subitoo_maintenance(args):
    """Main command call from argparse"""
    if args.notificationTest:
        ntf = NotificationPushover("Subitoo Test", "Lorem ipsum dolor sit amet, consectetur adipiscing elit.", "https://www.subito.it/", None)
        if send_pushover_notification(ntf):
            print('Done!')
        else:
            print('Something went wrong!')

    if args.resetSearch is not False:
        quit_if_already_running()
        reset_search_query(args.resetSearch)

    if args.forceUnlock is not False:
        set_running(False)
        print('Done!')

    #if args.dataPath is not False:
    #    global basedirectory
    #    print("Data is stored here: "+basedirectory)

    if args.pythonVersion is not False:
        print("Python version: "+sys.version)

    if args.justSleep is not False:
        quit_if_already_running()
        set_running(True)
        r = range(args.justSleep, 0, -1)
        for s in r:
            print(s)
            logging.info("Sleep: {}".format(s))
            time.sleep(1)
        set_running(False)


"""
##############################################################
#### ARGPARSE DEFINITIONS ####################################
##############################################################
"""


def set_pushover_keys(keys):
    splitted = keys.split(":")
    if len(splitted) == 2:
        app_token = splitted[0]
        user_key = splitted[1]
        tinydb_upsert_field_value(configs, 'pushover_app_token', app_token)
        tinydb_upsert_field_value(configs, 'pushover_user_key', user_key)
        print("Pushover keys saved successfully!")
        reload_pushover_keys()
    else:
        print("Format not valid! Please use APP_TOKEN:USER_KEY format")
    return True


def type_url(arg):
    """This check if the URL is valid"""
    url = urlparse(arg)
    if all((url.scheme, url.netloc)):
        return arg
    raise argparse.ArgumentTypeError('Invalid URL')


def quit_if_already_running():
    """Prevent concurrent executions"""
    is_running = configs.search(Query().running == True)
    if len(is_running) >  0 :
        message = "Another instance is 'running', please wait it to finish before running again. If stuck use 'subitoo maintenance --forceUnlock'"
        ntf = NotificationPushover("Subitoo warning!", message, "", "")
        send_pushover_notification(ntf)
        sys.exit(message)


def set_running(status):
    """Poor man's behavioral software design pattern XD"""
    tinydb_upsert_field_value(configs, 'running', status)


def print_search_queries(args):
    """Console print a table with all the 'search_queries' saved into the database"""
    if len(queries.all()) == 0:
        print("Zero search query saved")
        return True

    if args.raw:
        for q in queries.all():
            pprint(q)
        return True

    # prepare data to print table
    tabledata_true = []
    tabledata_false = []
    allowed_keys = SearchQuery.get_printable_fields()
    for q in queries.all():
        values = [dict(q)[k] for k in allowed_keys]
        if q['enabled'] == True:
            tabledata_true.append(dict(zip(allowed_keys, values)))
        else:
            tabledata_false.append(dict(zip(allowed_keys, values)))
    print()
    if len(tabledata_true) > 0:
        print(tabulate(tabledata_true, headers="keys", tablefmt="rounded_grid", numalign="center", stralign="left"))
    if len(tabledata_false) > 0:
        print(tabulate(tabledata_false, headers="keys", tablefmt="rounded_grid", numalign="center", stralign="left"))
    print()


def search_query_change_status(names, status):
    """Enable or disable a 'search_query', disabled ones will not run"""
    for name in names:
        name = name.strip()
        exists = queries.search(where('name') == name)
        if len(exists) > 0:
            queries.update({'enabled': status}, Query().uid == exists[0]['uid'])
            print("'{}' search query new status is: '{}'".format(name, str(status)))
        else:
            print("'{}' search query not found!".format(name))


def reset_search_query(name):
    """Set a search query as 'new' and remove all the listings saved"""
    name = name.strip()
    exists = queries.search(where('name') == name)
    if len(exists) > 0:
        queries.update({'first_run': True}, Query().uid == exists[0]['uid'])
        listings.remove(Query().queryuid == exists[0]['uid'])
        print("'{}' search query reset completed!".format(name))
    else:
        print("'{}' search query not found!".format(name))


def add_search_query(SearchQuery):
    """Add a search query to the database"""
    name = SearchQuery.name
    exists = queries.search(where('name') == name)
    if len(exists) > 0:
        print("'{}' already exists!".format(name))
    else:
        queries.insert(SearchQuery.__dict__)
        print("'{}' saved successfully!".format(name))


def delete_search_query(names):
    """Remove a search query from the database"""
    for name in names:
        name = name.strip()
        found = queries.search(where('name') == name)

        if len(found) > 0:
            queries.remove(Query().name == found[0]['name'])
            listings.remove(Query().queryuid == found[0]['uid'])
            print("'{}' removed!".format(found[0]['name']))
        else:
            print("'{}' not found!".format(name))


def get_query_name_by_uuid(quuid):
    """Get the search query name from uuid"""
    found = queries.search(where('uid') == quuid.strip())
    if len(found) > 0:
        return found[0]['name']
    else:
        return ""


"""
##############################################################
#### HELPERS #################################################
##############################################################
"""


def tinydb_get_field_value(table, field_name):
    """Why the F is so hard to get a field value from TinyDB? Am I doing something wrong?"""
    value = table.get(where(field_name) != "some-random-string-9zshy27famgv2euryet5")
    if value:
        return value.get(field_name)
    return None


def get_current_yearweek():
    """Return the current week of the year plus the year ex: 202538"""
    # Get the current date
    current_date = datetime.date.today()
    # Get the current week number and year
    week_number = current_date.strftime("%U")  # Week number of the year
    year = current_date.year
    # Combine the year and week number into the desired format
    week_year = f"{year}{week_number.zfill(2)}"
    return week_year


def tinydb_upsert_field_value(table, field, value):
    """I have no idea what I'm doing"""
    table.upsert({field: value}, Query()[field] != None)
    return True


def get_current_errors_number():
    config_doc = configs.get(Query().errors.exists())
    errors = config_doc.get('errors', 0) if config_doc else 0
    return errors


def is_pushover_enabled():
    global pushover_app_token
    global pushover_user_key
    if len(pushover_app_token) > 1 and len(pushover_user_key) > 1:
        return True
    return False


def reload_pushover_keys():
    global pushover_app_token
    global pushover_user_key
    token = tinydb_get_field_value(configs, "pushover_app_token")
    key = tinydb_get_field_value(configs, "pushover_user_key")
    if token and key:
        pushover_app_token = token
        pushover_user_key = key


def make_wide(formatter, w=600, h=200):
    """Return a wider HelpFormatter, if possible. Needed to get a wider console print"""
    try:
        # https://stackoverflow.com/a/5464440
        # beware: "Only the name of this class is considered a public API."
        kwargs = {'width': w, 'max_help_position': h}
        formatter(None, **kwargs)
        return lambda prog: formatter(prog, **kwargs)
    except TypeError:
        warnings.warn("argparse help formatter failed, falling back.")
        return formatter


# If needed, apply some rate limits here
# ntf is class NotificationPushover
def send_pushover_notification(ntf):
    """Send a Pushover notification"""
    global pushover_app_token
    global pushover_user_key

    if (not pushover_app_token) or (not pushover_user_key):
        return False

    attachment = None
    if ntf.imageurl:
        attachment = requests.get(ntf.imageurl).content

    r = requests.post("https://api.pushover.net/1/messages.json", data={
        "token": pushover_app_token,
        "user": pushover_user_key,
        "message": ntf.message,
        "title": ntf.title,
        "url": ntf.url,
        "url_title": "Visualizza su Subito",
        "html": 1,
    },
    files={"attachment": attachment}
    )
    if r.status_code == 200:
        return True
    return False


def generate_pushover_notification_from_listing(lst):
    """Take a class Listing and generate a class NotificationPushover from it"""
    title = lst.name
    query_name = get_query_name_by_uuid(lst.queryuid)

    message = " "
    if lst.price is not None and int(lst.price) > 0:
        message = message + " <font color='#db00ba'>"+str(lst.price)+" &euro;</font> &#183;"

    if lst.shipping:
        message = message + " <font color='#00b53c'>SPEDIZIONE &#10003;</font> &#183;"

    if lst.sold:
        message = message + " <font color='#d60000'>VENDUTO</font> &#183;"

    message = message.strip()
    message = message.strip("&#183;")
    message = message.strip()

    if len(str(lst.location)) > 1:
        message = message + "<br /><i> "+str(lst.location)+"</i>"

    if len(query_name) > 0:
        message = message + "<br /><br /><font color='#009dd6'> -> query: </font>" + "'" + str(query_name) + "'"

    obj = NotificationPushover(title, message, lst.url, lst.imageurl)
    return obj


def signal_handler(sig, frame):
    """Registering Ctrl+C handler"""
    msg = 'Manual force close!'
    print(msg)
    logging.error(msg)
    set_running(False)
    sys.exit(0)


# Ctrl+C catcher
signal.signal(signal.SIGINT, signal_handler)


"""
##############################################################
#### CORE ####################################################
##############################################################
"""


def execute_run(query):
    """Where the web parsing/scraping of Subito.it happens"""
    total_pages = query['pages']
    hades_limit = 30
    global notifications
    if total_pages == 0: total_pages = 300

    for page_counter in range(total_pages):
        hades_start = page_counter * hades_limit
        current_page = page_counter + 1

        # Switch old subito url (partially working) vs new hades url (fully working)
        # and apply pagination
        url_parsed = urlparse(query['url'])
        url_hostname = url_parsed.netloc.lower()
        if url_hostname in ('www.subito.it', 'subito.it'):
            hades_url = build_hades_url_from_subito_url(query['url'], hades_limit, hades_start)
        else:
            hades_url = hades_url_with_pagination(query['url'], hades_limit, hades_start)

        if current_page > 1: time.sleep(seconds_between_pages)
        try:
            logging.info("")
            logging.info("==========")
            logging.info("")
            logging.info("START: '{}' page {}".format(query['name'], current_page))
            dom = requests.get(hades_url, headers=hades_headers)
        except Exception as e:
            logging.error("{}".format(e))
            continue

        if dom.status_code == 404:
            logging.warning("Got a 404! End of pages?")
            break

        response_data = json.loads(dom.text)

        # Print the data to debug
        #with open('response_data.json', 'w', encoding='utf-8') as f:
        #    json.dump(response_data, f, indent=2, ensure_ascii=False)

        # Extract listings
        found_listings = response_data['ads']

        if not found_listings:
            logging.warning("Zero listings found! End of pages?")
            break

        logging.info(f"Found {len(found_listings)} listings!")

        for lst in found_listings:
            Listing = extract_listing_data(lst, query['uid'])

            if Listing is False:
                logging.warning("This listing returned False:")
                logging.warning(print(json.dumps(lst, indent=0)))
                continue

            logging.info("")
            logging.info("'{}'".format(Listing.name))
            logging.info("'{}'".format(Listing.url))
            changed = is_something_changed(Listing, query['uid'])
            reason = is_skippable(query, Listing)
            if reason is not False:
                logging.info("--> Skipped ({})".format(reason))
                continue

            # Ok let's save this listing on the db then!
            if changed:
                QueryBuilder = Query()
                listings.upsert(Listing.__dict__, ((QueryBuilder.uid == Listing.uid) & (QueryBuilder.queryuid == query['uid'])))
                if not query['first_run']: logging.info("--> Changes detected (or new)")
            else:
                logging.info("--> No changes detected")

            # Need to send notifications?
            if not query['first_run'] and changed:
                notifications.append(Listing)
                logging.info("--> Notification queued!")

        # after a page have been read, send notifications!
        logging.info("")
        logging.info("Page {} done, sending all queued notifications ({})".format(current_page, len(notifications)))
        send_notifications()

    # remove first_run from this query
    if query['first_run']:
        queries.upsert({'first_run': False}, Query().uid.matches(query['uid'], flags=re.IGNORECASE))

    logging.info("")
    logging.info("END: '{}'".format(query['name']))


def hades_url_with_pagination(hades_url, limit=30, start=0):
    hades_parsed_url = urlparse(hades_url)
    hades_query_params = parse_qs(hades_parsed_url.query)

    # Limit & pagination
    hades_query_params['lim'] = [str(limit)]
    hades_query_params['start'] = [str(start)]

    # Rebuild the URL
    new_query = urlencode(hades_query_params, doseq=True)
    hades_url = urlunparse((hades_parsed_url.scheme, hades_parsed_url.netloc, hades_parsed_url.path, hades_parsed_url.params, new_query,hades_parsed_url.fragment))
    return hades_url


def build_hades_url_from_subito_url(subito_url, limit=30, start=0):
    """Start from a Subito url and build Hades url"""

    # Extract parameters from the Subito query
    subito_parsed_url = urlparse(subito_url)
    subito_query_params = parse_qs(subito_parsed_url.query)

    # Use the parameters to build a Hades query url
    parsed_hades = urlparse("https://hades.subito.it/v1/search/items")
    hades_params = parse_qs(parsed_hades.query)

    # Query string
    hades_params['q'] = subito_query_params.get('q')
    # Type sell
    hades_params['t'] = ['s']
    # Search only title?
    hades_params['qso'] = subito_query_params.get('qso', 'false')
    # Only with shipping available?
    hades_params['shp'] = subito_query_params.get('shp', 'false')
    # ?
    hades_params['urg'] = ['false']
    # Sorting
    hades_params['sort'] = subito_query_params.get('order', 'datedesc')
    # Limit & pagination
    hades_params['lim'] = [str(limit)]
    hades_params['start'] = [str(start)]

    # Rebuild the URL
    new_query = urlencode(hades_params, doseq=True)
    hades_url = urlunparse((parsed_hades.scheme, parsed_hades.netloc, parsed_hades.path, parsed_hades.params, new_query,parsed_hades.fragment))
    return hades_url


def send_notifications():
    """Send the notifications buffered into the global variable 'notifications'"""
    global notifications
    global sent_notifications_uids

    if len(notifications) == 0:
        return True

    if not is_pushover_enabled():
        logging.warning("Missing Pushover keys!")
        return True

    print("Sending notifications")
    for listing in notifications:
        # do not send the same notification multiple times
        if listing.uid in sent_notifications_uids: continue
        pushover_ntf = generate_pushover_notification_from_listing(listing)
        is_sent = send_pushover_notification(pushover_ntf)
        if is_sent: sent_notifications_uids.append(listing.uid)

    # reset before next cycle
    notifications = []
    return True


def is_something_changed(Listing, queryuid):
    """Is this new Listing equal to the previous one saved into the database? Something has changed?"""
    QueryBuilder = Query()
    old = listings.search(((QueryBuilder.uid == Listing.uid) & (QueryBuilder.queryuid == queryuid)))
    # Not found this listing so technically, it is changed
    if len(old) == 0: return True
    old = dict(old[0])
    new = Listing.__dict__
    diff = DeepDiff(old, new, ignore_string_case=True, ignore_type_subclasses=True)
    if diff.items().__len__() > 0: return True
    return False


def is_skippable(query, Listing):
    """Return true if a Listing does not meet the defined requirements"""
    if Listing.sold and query['skip_sold']: return 'Item is sold'
    if Listing.price is None and query['skip_no_price']: return 'The price is missing'
    if Listing.price is not None:
        if query['max_price'] == 0 and Listing.price < query['min_price']: return 'Price range not matched'
        if query['max_price'] > 0 and (Listing.price > query['max_price'] or Listing.price < query['min_price']): return 'Price range not matched'
    if query['regex_match'] is not None:
        my_regex = r'' + query['regex_match'] + r''
        got_match = re.search(my_regex, Listing.name, re.IGNORECASE)
        if got_match is None: return 'Regex not matched'
    return False


def extract_listing_data(item, query_uid):
    """Build a Listing object from the Beautifulsoup raw data"""

    #print(json.dumps(item, indent=2))

    # Extract the url of the item, if not valid exit here
    link = item.get('urls', {}).get('default', '')
    if not link.startswith('https://www.subito.it/') and not link.startswith('https://subito.it/'):
        link = None
    if link is None:
        return False

    # Extract the name of the item
    name = item.get('subject', 'Unknown')

    # Extract the price
    price_feature = next((f for f in item.get('features', []) if f.get('uri') == '/price'), None)
    price = int(price_feature['values'][0]['key']) if price_feature else None

    # Extract images
    item_images = item.get('images', [])
    image_url = (item_images[0].get('cdn_base_url') + "?rule=card-desktop-new-small-3x-auto") if item_images else None

    # Is shipping available?
    shipping_feature = next((f for f in item.get('features', []) if f.get('uri') == '/item_shipping_allowed'), None)
    shipping = bool(shipping_feature['values'][0]['key']) if shipping_feature else False

    # Extract location
    geo = item.get('geo', {})
    town_value = geo.get('town', {}).get('value')
    city_short = geo.get('city', {}).get('shortName')
    if town_value and city_short:
        location = f"{town_value} ({city_short})"
    else:
        location = town_value or city_short or "Unknown"

    # Sold items are no longer appearing?
    sold = False

    # custom uuid
    splitted = link.rsplit('/', 1)[-1]
    splitted = splitted.split('.')[0]
    cuuid = splitted.strip()

    listing_obj = Listing(name, sold, shipping, price, link, location, cuuid, query_uid, image_url)

    return listing_obj


"""
##############################################################
#### COMMAND-LINE INTERFACE PARSING OF ARGUMENTS #############
##############################################################
"""


def initialization():
    """All the functions that need to be run before everything else"""
    reload_pushover_keys()


def main():
    """Main"""
    initialization()

    # global argument parser
    parser = argparse.ArgumentParser(prog='subitoo', formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    subparsers = parser.add_subparsers(help='commands available')

    # subparser for the 'add' command
    parser_add = subparsers.add_parser('add', help='Add a new search query', aliases=['create'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_add.set_defaults(func=subitoo_add)
    parser_add_required = parser_add.add_argument_group('required arguments')
    parser_add_optional = parser_add.add_argument_group('additional arguments')
    parser_add_required.add_argument('--name', '-n', '-id', dest='name', metavar="NO_SPACES_NAME", help='The name of this new search query to add', required=True)
    parser_add_required.add_argument('--url', '-u', '--link', '-l', dest='url', help='The search query url', required=True, type=type_url)
    parser_add_optional.add_argument('--pages', '-p', dest='pages', help='The amount of pages to search, 0 means \'all\'', default='1', type=int)
    parser_add_optional.add_argument('--minPrice', dest='min_price', metavar="PRICE", help='Price range minimum', default=1, type=int)
    parser_add_optional.add_argument('--maxPrice', dest='max_price', metavar="PRICE", help='Price range maximum', default=0, type=int)
    parser_add_optional.add_argument('--skipNoPrice', dest='skip_no_price', help='Skip a listing if the price is not set', action="store_true", default=False)
    parser_add_optional.add_argument('--skipSold', dest='skip_sold', help='Skip a listing if the item is sold', action="store_true", default=False)
    parser_add_optional.add_argument('--regex', '-re', dest='regex', help='Case insensitive regex applied on listings title', default=None)

    # subparser for the 'list' command
    parser_list = subparsers.add_parser('list', help='List the saved search queries', aliases=['ls'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_list.set_defaults(func=subitoo_list)
    parser_list_required = parser_list.add_argument_group('required arguments')
    parser_list_optional = parser_list.add_argument_group('additional arguments')
    parser_list_optional.add_argument('--raw', dest='raw', help='Print raw json instead of the table', action="store_true", default=False)

    # subparser for the 'delete' command
    parser_delete = subparsers.add_parser('delete', help='Delete a saved search query', aliases=['remove', 'rm'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_delete.set_defaults(func=subitoo_delete)
    parser_delete_required = parser_delete.add_argument_group('required arguments')
    parser_delete_optional = parser_delete.add_argument_group('additional arguments')
    parser_delete_required.add_argument('--name', '-n', dest='name', nargs="+", help='Names of search queries to delete, space separated', required=True)

    # subparser for the 'enable' command
    parser_enable = subparsers.add_parser('enable', help='Enable a saved search query', formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_enable.set_defaults(func=subitoo_enable)
    parser_enable_required = parser_enable.add_argument_group('required arguments')
    parser_enable_optional = parser_enable.add_argument_group('additional arguments')
    parser_enable_required.add_argument('--name', '-n', '-id', dest='name', nargs="+", help='Names of search queries to enable, space separated', required=True)

    # subparser for the 'disable' command
    parser_disable = subparsers.add_parser('disable', help='Disable a saved search query', formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_disable.set_defaults(func=subitoo_disable)
    parser_disable_required = parser_disable.add_argument_group('required arguments')
    parser_disable_optional = parser_disable.add_argument_group('additional arguments')
    parser_disable_required.add_argument('--name', '-n', '-id', dest='name', nargs="+", help='Names of search queries to disable, space separated', required=True)

    # subparser for the 'run' command
    parser_run = subparsers.add_parser('run', help='Execute the search', aliases=['start'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_run.set_defaults(func=subitoo_run)
    parser_run_required = parser_run.add_argument_group('required arguments')
    parser_run_optional = parser_run.add_argument_group('additional arguments')

    # subparser for the 'maintenance' command
    parser_maintenance = subparsers.add_parser('maintenance', help='Some troubleshooting commands', aliases=['debug'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_maintenance.set_defaults(func=subitoo_maintenance)
    parser_maintenance_required = parser_maintenance.add_argument_group('required arguments')
    parser_maintenance_optional = parser_maintenance.add_argument_group('additional arguments')
    parser_maintenance_optional.add_argument('--notificationTest', '--testNotification', dest='notificationTest', action="store_true", default=False, help='This will only send you a notification')
    parser_maintenance_optional.add_argument('--resetSearch', dest='resetSearch', metavar='SEARCH_QUERY_NAME', default=False, help='Reset a search query to a \'first run\' status')
    parser_maintenance_optional.add_argument('--forceUnlock', dest='forceUnlock', default=False, action="store_true", help='Force running status to \'false\'')
    parser_maintenance_optional.add_argument('--justSleep', '--sleep', metavar='SECONDS', dest='justSleep', default=False, type=int, help='This is just a test command, sleep for X seconds')
    #parser_maintenance_optional.add_argument('--dataPath', dest='dataPath', default=False, action="store_true", help='Print the database system path')
    parser_maintenance_optional.add_argument('--pythonVersion', dest='pythonVersion', default=False, action="store_true", help='Print the python version')

    # subparser for the 'configuration' command
    parser_configuration = subparsers.add_parser('configuration', help='Save or edit configuration parameters', aliases=['config'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_configuration.set_defaults(func=subitoo_configuration)
    parser_configuration_required = parser_configuration.add_argument_group('required arguments')
    parser_configuration_optional = parser_configuration.add_argument_group('additional arguments')
    parser_configuration_optional.add_argument('--setPushoverKeys', dest='PushoverKeys', metavar='APP_TOKEN:USER_KEY', help='Save Pushover keys', default=False)

    # if there are no arguments then fallback to '--help'
    args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])
    args.func(args)


if __name__ == '__main__':
    main()
