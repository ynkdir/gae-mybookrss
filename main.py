# coding: utf-8

import datetime
import logging
import os.path
import time

import webapp2
from google.appengine.api import memcache

from jinja2 import Template

import config
import amazonaws


LOCALE_UTC_OFFSET = {
    'ca': datetime.timedelta(hours=-3, minutes=-30),
    'de': datetime.timedelta(hours=1),
    'fr': datetime.timedelta(hours=1),
    'jp': datetime.timedelta(hours=9),
    'uk': datetime.timedelta(),
    'us': datetime.timedelta(hours=-5),
}


def dmemcache(time):
    def deco(f):
        def wrap(*args):
            key = str(args)
            v = memcache.get(key, namespace=f.__name__)
            if v is None:
                v = f(*args)
                memcache.add(key, v, time=time, namespace=f.__name__)
            return v
        return wrap
    return deco


def render(template_file, template_values):
    path = os.path.join(os.path.dirname(__file__), "templates", template_file)
    with open(path) as f:
        return Template(f.read()).render(template_values)


def item_search(locale, **params):
    params['Operation'] = 'ItemSearch'
    params['ItemPage'] = 1
    while True:
        root = item_search_page(locale, params)
        if root is None:
            break
        for item in root.findall('Items/Item'):
            yield item
        total_pages = int(root.findtext('Items/TotalPages'))
        params['ItemPage'] += 1
        if total_pages < params['ItemPage']:
            break


@dmemcache(config.item_search_cache_time)
def item_search_page(locale, params):
    client = amazonaws.Client(config.AWS_ACCESS_KEY,
                              config.AWS_SECRET_KEY,
                              locale)
    try:
        root = client.request(params)
    except amazonaws.AWSError, e:
        if e.code == 'AWS.ECommerceService.NoExactMatches':
            return None
        raise
    return root


def search(keywords, locale, days):
    utcnow = datetime.datetime.utcnow()
    now = utcnow + LOCALE_UTC_OFFSET[locale]
    limit_date = now + datetime.timedelta(days=days)
    # keywords is injectable
    power = u"pubdate: after %s and keywords: %s" % (recentmonth(now), keywords)
    items = []
    for item in item_search(locale=locale,
                            SearchIndex='Books',
                            Power=power.encode("utf-8"),
                            Sort='daterank',
                            ResponseGroup='Medium',
                            AssociateTag=config.AWS_ASSOCIATE_TAG):
        # ignore collection item
        if item.find('ItemAttributes/ISBN') is None:
            continue
        (release_date_obj, release_date) = get_release_date(item)
        if release_date is None:
            continue
        if release_date_obj.date() <= limit_date.date():
            items.append({
                'release_date': release_date,
                'title': item.findtext('ItemAttributes/Title'),
                'author': '/'.join(attr.text
                    for attr in item.findall('ItemAttributes/Author')),
                'large_image': item.findtext('LargeImage/URL'),
                'medium_image': item.findtext('MediumImage/URL'),
                'small_image': item.findtext('SmallImage/URL'),
            })
    return items


def recentmonth(now):
    m = now - datetime.timedelta(days=120)
    return m.strftime("%m-%Y")


def get_release_date(item):
    release_date = item.findtext('ItemAttributes/ReleaseDate')
    if release_date is None:
        release_date = item.findtext('ItemAttributes/PublicationDate')
    for date_format in ["%Y-%m-%d", "%Y-%m"]:
        try:
            obj = datetime.datetime.strptime(release_date, date_format)
        except ValueError:
            pass
        else:
            return (obj, release_date)
    return (None, None)


class ABase(webapp2.RequestHandler):
    def handle_exception(self, exception, debug_mode):
        if debug_mode:
            webapp2.RequestHandler.handle_exception(self, exception, debug_mode)
        else:
            logging.exception(exception)
            self.error(500)
            self.response.out.write(exception)


class AIndex(ABase):
    def get(self):
        self.response.out.write(render("index.html", {}))


class ARss(ABase):
    def get(self):
        if "days" not in self.request.GET:
            self.request.GET["days"] = "0"
        data = {}
        data["keywords"] = self.request.GET["keywords"]
        data["locale"] = self.request.GET["locale"]
        data["days"] = int(self.request.GET["days"])
        data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        items = search(data["keywords"], data["locale"], data["days"])
        template_values = {
            "request": self.request,
            "data": data,
            "items": items,
        }
        self.response.headers["content-type"] = "application/atom+xml"
        self.response.out.write(render("atom1.xml", template_values))


app = webapp2.WSGIApplication(
    [('/', AIndex),
     ('/rss', ARss),
    ],
    debug=config.debug)
