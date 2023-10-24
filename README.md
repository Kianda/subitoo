# Subitoo

Command-line interface price tracker and crawler for [Subito.it](https://www.subito.it)




## Requirements
- [Pushover](https://pushover.net) (*not free*)
- [Snapcraft](https://snapcraft.io/docs/installing-snapd) or [Docker](https://docs.docker.com/get-docker/)


## Features

- Scan all the search result pages
- Smartphone notifications with Pushover
- Detect changes on old listings
- Sold items can be excluded
- Price range filtering
- Filter items applying regex matching on titles (BETA)


## Installation

**I will publish *Subitoo* on the snapcraft.io/store asap**

```bash
sudo snap install subitoo
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
Run ***Subitoo*** and it will notify you if new items appear on that search:
```bash
subitoo run
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

```bash
subitoo add --name "MyiPhone" --url "https://www.subito.it/annunci-italia/vendita/usato/?q=iPhone&qso=true&shp=true" --pages 2 --minPrice 200 --maxPrice 450 --skipNoPrice --skipSold
```

## Docker

### Build
You can build your own image:
```bash
docker build -f Dockerfile --no-cache -t kianda/subitoo:0.1.1 -t kianda/subitoo:latest .
docker push kianda/subitoo:0.1.1; docker push kianda/subitoo:latest
```
Or use mine on [Dockerhub](https://hub.docker.com/r/kianda/subitoo)

### Run
```bash
docker run --rm \
-v /host/data/folder:/root/.subitoo/ \
kianda/subitoo:latest --help
```

If you need ***Subitoo*** to run automatically use your operating system job scheduler, like [cron](https://en.wikipedia.org/wiki/Cron)

## Auto Run

Once configured, you will probably need to run ***Subitoo*** automatically:

Just use a job scheduler! (like [cron](https://en.wikipedia.org/wiki/Cron))
```bash
crontab -e
```
```bash
# (if you installed Subitoo with Snap)
# This will run subitoo every 2 hours
0 */2 * * * subitoo run
```
```bash
# (if you are using Docker)
# This will run subitoo every 2 hours
0 */2 * * * docker run --rm -v /host/data/folder:/root/.subitoo/ kianda/subitoo:latest run
```
```bash
# (if you are using Docker and don't want to use the OS job scheduler)
# This will run subitoo every 2 hours 
docker run --name subitoo_scheduler -d --rm \
-v /var/run/docker.sock:/var/run/docker.sock:ro \
--label ofelia.job-run.subitoo-job.schedule="@every 120m" \
--label ofelia.job-run.subitoo-job.image="kianda/subitoo:latest" \
--label ofelia.job-run.subitoo-job.volume="/host/data/folder:/root/.subitoo/" \
--label ofelia.job-run.subitoo-job.command="run" \
mcuadros/ofelia:latest daemon --docker
```

## FAQ

### Why I did not receive any notifications on first run?

The first run of a search query will not send notifications, it will only populate the database.

You will receive notifications on consecutive runs if there is a new item or any old one is changed.

### It's safe to set *--pages 0* as search parameter?

Only if your URL is safe, by safe I mean that it will give out not-too-many results.

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

### What happens if my cron will *subitoo run* multiple times in too short time?
Nothing, there is a built-in lock, if you execute *subitoo run* and the previous execution is still running it will do nothing.

### Where ***Subitoo*** save data?
Run this:
```bash
subitoo maintenance --dataPath
```

### How the *'old listings detect changes'* work?
If an item is already in the database and get scanned again then it will be matched vs the old one.

> **NOTICE:** If your search is reading only page 1 (of the results) then all the items that end up into pages > 1 will never be read again until they go back into page 1.
