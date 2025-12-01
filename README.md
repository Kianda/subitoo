# Subitoo

Command-line interface crawler for [Subito.it](https://www.subito.it)




## Requirements
- [Pushover](https://pushover.net) (*not free*)
- [Docker](https://docs.docker.com/get-docker/)


## Features

- Scan all the search result pages
- Smartphone notifications with Pushover
- Detect changes on old listings
- Sold items can be excluded
- Price range filtering
- Filter items applying regex matching on titles


## Installation

```bash
# Clone this repo somewhere
git clone https://github.com/Kianda/subitoo.git subitoo && cd subitoo
```
```bash
chmod +x subitoo.sh
# Then use the file ./subitoo.sh to execute Subitoo (or set any alias you want)
```
```bash
# Optional: set a 'subitoo' alias
sed -i '/alias subitoo=/d' ~/.bash_aliases; echo "alias subitoo='$(pwd)/subitoo.sh'" >> ~/.bash_aliases && source ~/.bashrc
```

```bash
# Optional: create .env file to set a custom image tag (or will fallback to TAG "1")
cp .env.example .env
```

## Update
```bash
# cd /absolute/path/to/subitoo/ and do it manually
# (or set a cron for it -> check 'Cron' section)
docker compose pull
```
    
## Notifications

To enable notifications you need the **APPLICATION_TOKEN** and **USER_KEY** from your Pushover account.

You can copy your **USER_KEY** at the [Pushover homepage](https://pushover.net) after you logged in.

You can copy your **APPLICATION_TOKEN** after you've created a [Pushover app](https://pushover.net/apps/build); give it a name and, if you want, a 72x72 [image](https://github.com/Kianda/subitoo/blob/main/extra/images/subitoo_icon_circle.png).

Then save the keys inside ***Subitoo*** with **APPLICATION_TOKEN**:**USER_KEY** format like this:
```bash
# example
subitoo config --setPushoverKeys abcd11e25fg8h5i1yg14abc2c8u28o:abc52de1tx9z315ppq5zzb43a1v6hc
```

Execute a test:
```bash
subitoo maintenance --testNotification
```
## Basic usage
Go to [Subito.it](https://www.subito.it), permorm a search (apply all the filters you want) and copy the URL.

```bash
# example
https://www.subito.it/annunci-lombardia/vendita/usato/?q=nvidia+gtx+1060&qso=true
```
Save it on ***Subitoo***:
```bash
# example
subitoo add --name "GTX 1060" --url "https://www.subito.it/annunci-lombardia/vendita/usato/?q=nvidia+gtx+1060&qso=true"
```
You can check all your saved URLs with:
```bash
subitoo ls
```
Run ***Subitoo,*** it will notify you if new items appear on that search:
```bash
# This is a one-time run.
# You need to execute this everytime (check the 'Cron' section)
subitoo run
```

## Cron

To run ***Subitoo*** automatically use your operating system job scheduler, like [cron](https://en.wikipedia.org/wiki/Cron)

```bash
crontab -e
```
```
# This will run Subitoo every 2 hours
0 */2 * * * cd /your/absolute/path/to/subitoo/ && ./subitoo.sh run
# And update once a day
0 0 * * * cd /your/absolute/path/to/subitoo/ && docker compose pull
```

## Advanced Usage

To learn more please use the built-in helper
```bash
subitoo --help
subitoo run --help
subitoo add --help
subitoo list --help
subitoo delete --help
subitoo enable --help
subitoo disable --help
subitoo maintenance --help
subitoo configuration --help
```

More complex *subitoo add* example, this will search for:
- iPhone keyword (url parameter)
- All Italy as location (url parameter)
- Only in the listings title (url parameter)
- Only with shipping available (url parameter)
- Minimum price is 200
- Maximum price is 450
- Will ignore already sold items
- Will ignore if the price is missing
- Scan only the first 2 pages of the results
- Apply [regex](https://regex101.com/r/sjzhHv/3) (?i)^(?=.*plus)(?!.*iphone 12) on listing title

```bash
subitoo add --name MyiPhone --url "https://www.subito.it/annunci-italia/vendita/usato/?q=iPhone&qso=true&shp=true" --pages 2 --minPrice 200 --maxPrice 450 --skipNoPrice --skipSold --regex '(?i)^(?=.*plus)(?!.*iphone 12)' --skipSold --skipNoPrice```
```

## Build
If you want, you can build your own image:
```bash
# cd /your/absolute/path/to/subitoo/
export TAG_VERSION='1.1' && \
export TAG_MAJOR='1' && \
export HUB_PATH='kianda/subitoo' && \
docker build -f Dockerfile --no-cache -t $HUB_PATH:$TAG_VERSION -t $HUB_PATH:$TAG_MAJOR -t $HUB_PATH:latest . && \
docker push $HUB_PATH:$TAG_VERSION && \
docker push $HUB_PATH:$TAG_MAJOR && \
docker push $HUB_PATH:latest
```

## FAQ

### Why didn’t I receive any notifications on the first run?

The first run of a search query will not send notifications, it will only populate the database.

You will receive notifications on consecutive runs if there is a new item or any old one is changed.

### It's safe to set *--pages 0* as a search parameter?

Only if your URL is safe, by safe I mean that it doesn't return too many results.

Check this URL for example:
```
https://www.subito.it/annunci-italia/vendita/usato/?q=apple
```
This will give you more than 70.000 results! That's like 300 pages that ***Subitoo*** need to scan, **it will take time**!

So, please, use *--pages 0* only if you know what you are doing[.](https://knowyourmeme.com/memes/you-know-nothing-jon-snow)

Check all the parameters for the *add* command here:
```bash
subitoo add --help
```

### What happens if my cron job runs *subitoo run* multiple times in a short period?
Nothing, there is a built-in lock, if you execute *subitoo run* and the previous execution is still running it will be ignored.

### Where does ***Subitoo*** save data?
Inside the '*data*' folder you will find the database and the logs. Feel free to back it up to prevent data loss.

### How does the '*old listings detect changes*' feature work?
If an item is already in the database and gets scanned again, it will be compared against the existing version.

> **NOTICE:** If your search is reading only page 1 (of the results) then all the items that end up into pages > 1 will never be read again until they go back into page 1.
