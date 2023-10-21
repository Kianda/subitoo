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
from pprint import pprint
from urllib.parse import urlparse
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
seconds_between_queries = int(4)
seconds_between_pages = int(2)

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
        return ['name', 'pages', 'regex_match', 'min_price', 'max_price', 'enabled']


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
    try:
        for idx, q in enumerate(queries.search(Query().enabled == True)):
            execute_run(q)
            if idx > 0: time.sleep(seconds_between_queries)
    except Exception as e:
        msg = "{}".format(e)
        logging.fatal(msg)
        print(msg)
    set_running(False)


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

    if args.dataPath is not False:
        global basedirectory
        print("Data is stored here: "+basedirectory)

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
    if len(is_running) > 0 : sys.exit("Another instance is 'running', please wait it to finish before running again")


def set_running(status):
    """Poor man's behavioral software design pattern XD"""
    tinydb_upsert_field_value(configs, 'running', status)


def print_search_queries(args):
    """Console print a table with all the 'seach_queries' saved into the database"""
    if len(queries.all()) == 0:
        print("Zero search query saved")
        return True

    if args.raw:
        for q in queries.all():
            pprint(q)
        return True

    # prepare data to print table
    tabledata = []
    allowed_keys = SearchQuery.get_printable_fields()
    for q in queries.all():
        values = [dict(q)[k] for k in allowed_keys]
        tabledata.append(dict(zip(allowed_keys, values)))

    print()
    print(tabulate(tabledata, headers="keys", tablefmt="rounded_grid", numalign="center", stralign="left"))
    print()


def search_query_change_status(names, status):
    """Enable or disable a 'search_query', disabled ones will not run"""
    for name in names:
        name = name.strip()
        exists = queries.search(Query().name.matches(name, flags=re.IGNORECASE))
        if len(exists) > 0:
            queries.update({'enabled': status}, Query().uid == exists[0]['uid'])
            print("'{}' search query new status is: '{}'".format(name, str(status)))
        else:
            print("'{}' search query not found!".format(name))


def reset_search_query(name):
    """Set a search query as 'new' and remove all the listings saved"""
    name = name.strip()
    exists = queries.search(Query().name.matches(name, flags=re.IGNORECASE))
    if len(exists) > 0:
        queries.update({'first_run': True}, Query().uid == exists[0]['uid'])
        listings.remove(Query().queryuid == exists[0]['uid'])
        print("'{}' search query reset completed!".format(name))
    else:
        print("'{}' search query not found!".format(name))


def add_search_query(SearchQuery):
    """Add a search query to the database"""
    exists = queries.search(Query().name.matches(SearchQuery.name, flags=re.IGNORECASE))
    if len(exists) > 0:
        print("'{}' already exists!".format(SearchQuery.name))
    else:
        queries.insert(SearchQuery.__dict__)
        print("'{}' saved successfully!".format(SearchQuery.name))


def delete_search_query(names):
    """Remove a search query from the database"""
    for name in names:
        found = queries.search(Query().name.matches(name.strip(), flags=re.IGNORECASE))
        if len(found) > 0:
            queries.remove(Query().name == found[0]['name'])
            listings.remove(Query().queryuid == found[0]['uid'])
            print("'{}' removed!".format(found[0]['name']))
        else:
            print("'{}' not found!".format(name.strip()))


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


def tinydb_upsert_field_value(table, field, value):
    """I have no idea what I'm doing"""
    table.upsert({field: value}, Query()[field] != None)
    return True


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


# If needed apply some rate limits here
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

    message = " "
    if int(lst.price) > 0:
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
    global notifications
    if total_pages == 0: total_pages = 300

    for page_counter in range(total_pages):
        current_page = page_counter + 1
        paged_url = query['url'] + '&o=' + str(current_page)
        if current_page > 1: time.sleep(seconds_between_pages)
        try:
            logging.info("")
            logging.info("==========")
            logging.info("")
            logging.info("START: '{}' page {}".format(query['name'], current_page))
            dom = requests.get(paged_url)
        except Exception as e:
            logging.error("{}".format(e))
            continue

        if dom.status_code == 404:
            logging.warning("Got a 404! End of pages?")
            break

        bsoup = BeautifulSoup(dom.text, 'html.parser')
        found_listings = bsoup.find_all('div', class_='item-card')

        if len(found_listings) == 0:
            logging.info("")
            logging.warning("Found zero listings! End of pages?")
            break

        for lst in found_listings:
            Listing = extract_listing_data(lst, query['uid'])
            if Listing is False: continue
            logging.info("")
            logging.info("'{}'".format(Listing.name))
            changed = is_something_changed(Listing, query['uid'])
            reason = is_skippable(query, Listing)
            if reason is not False:
                logging.info("--> Skipped ({})".format(reason))
                continue

            # Ok let's save this listing on the db then!
            if changed:
                QueryBuilder = Query()
                listings.upsert(Listing.__dict__, ((QueryBuilder.uid == Listing.uid) & (QueryBuilder.queryuid == query['uid'])))
                if not query['first_run']: logging.info("--> Changes detected")
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


def send_notifications():
    """Send the notifications buffered into the global variable 'notifications'"""
    global notifications
    global sent_notifications_uids

    if len(notifications) == 0:
        return True

    if not is_pushover_enabled:
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
    """Return true if a Listing does no meet the defined requirements"""
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


def extract_listing_data(lst, query_uid):
    """Build a Listing object from the Beautifulsoup raw data"""
    # find the link
    # first_a_tag = lst.find('a', class_='link', href=True)
    first_a_tag = lst.find('a', class_=re.compile('.*link.*'), href=True)
    link = None
    if first_a_tag is not None:
        link = first_a_tag.attrs['href']
        if not link.startswith('https://www.subito.it/') and not link.startswith('https://subito.it/'):
            link = None

    # not a valid link! skip!
    if link is None:
        return False

    # get the name
    name = 'Unknown'
    if lst.find('h2'):
        name = lst.find('h2').string.strip()

    # search for 'vetrina' badge
    div_module_with_badge = lst.find('div', class_=re.compile('PostingTimeAndPlace-module_with-badge.*'))
    item_vetrina_badge = False
    if div_module_with_badge:
        span_tagg = div_module_with_badge.find('span')
        if span_tagg:
            span_text = str(span_tagg.text)
            if len(span_text) > 0 and span_text.lower().strip() == 'vetrina':
                item_vetrina_badge = True

    if item_vetrina_badge:
        # Skip vetrina items because sometimes they are offtopic items
        logging.info("")
        logging.info("'{}'".format(name))
        logging.info("--> Skipped because it is a 'vetrina' item")
        return False

    # check if the item-sold-badge exists
    sold = bool(False)
    item_sold_badge = lst.find('span', class_=re.compile(r'item-sold-badge'))
    if type(item_sold_badge) == Tag: sold = bool(True)

    # check if the shipping-badge exists
    shipping = bool(False)
    item_shipping_badge = lst.find('span', class_=re.compile(r'shipping-badge'))
    if type(item_shipping_badge) == Tag: shipping = bool(True)

    # price
    price = None
    found_price = lst.find(string=re.compile(r'.*€.*'))
    if found_price is not None:
        price = int(re.sub("[^0-9]", "", found_price))

    # location
    location = ''
    city = town = ''
    found_city = lst.find('span', class_='city')
    if found_city is not None: city = found_city.string.strip()

    if len(city) > 0:
        found_town = found_city.previous_sibling
        if found_town is not None:
            town = found_town.string.strip()

    if len(city) > 0 and len(town) > 0: location = town + ' ' + city

    # images
    img_tag = lst.find('img')
    image_url = None
    if img_tag.has_attr('srcset'):
        # srcset attribute will have inside all the image URLS
        srcset_attr = img_tag.attrs['srcset']
        # extract all the links
        image_urls = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', srcset_attr)
        # the last image is the best quality one
        image_url = image_urls[-1]

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
    parser_maintenance_optional.add_argument('--dataPath', dest='dataPath', default=False, action="store_true", help='Print the database system path')
    parser_maintenance_optional.add_argument('--pythonVersion', dest='pythonVersion', default=False, action="store_true", help='Print the python version')

    # subparser for the 'configuration' command
    parser_configuration = subparsers.add_parser('configuration', help='Save or edit configuration parameters', aliases=['config'], formatter_class=make_wide(argparse.ArgumentDefaultsHelpFormatter))
    parser_configuration.set_defaults(func=subitoo_configuration)
    parser_configuration_required = parser_configuration.add_argument_group('required arguments')
    parser_configuration_optional = parser_configuration.add_argument_group('additional arguments')
    parser_configuration_optional.add_argument('--setPushoverKeys', dest='PushoverKeys', metavar='APP_TOKEN:USER_KEY', help='Save Pushover keys', default=False)

    # if there are no arguments then fallback to '--help'
    parser.parse_args(args=None if sys.argv[1:] else ['--help'])
    args.func(args)


if __name__ == '__main__':
    main()
