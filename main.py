# coding: utf-8

import datetime
import logging
import os.path

from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.api import memcache

from django import newforms as forms
from django.newforms.util import ValidationError

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
    return template.render(path, template_values)


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
    keywords = " or ".join('"%s"' % word
            for word in parse_keywords(keywords))
    utcnow = datetime.datetime.utcnow()
    now = utcnow + LOCALE_UTC_OFFSET[locale]
    limit_date = now + datetime.timedelta(days=days)
    power = u"pubdate: after %s and keywords: %s" % (recentmonth(now), keywords)
    items = []
    for item in item_search(locale=locale,
                            SearchIndex='Books',
                            Power=power.encode("utf-8"),
                            Sort='daterank',
                            ResponseGroup='Medium'):
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


def parse_keywords(keywords):
    words = []
    for word in keywords.splitlines():
        word = word.replace('"', '')
        word = word.strip()
        if word != '':
            words.append(word)
    return words


class ABase(webapp.RequestHandler):
    def handle_exception(self, exception, debug_mode):
        if debug_mode:
            webapp.RequestHandler.handle_exception(self, exception, debug_mode)
        else:
            logging.exception(exception)
            self.error(500)
            self.response.out.write(exception)


class KeywordsField(forms.CharField):
    def clean(self, value):
        keywords = forms.CharField.clean(self, value)
        words = parse_keywords(keywords)
        if len(words) == 0:
            raise ValidationError(u'Enter a valid value')
        return keywords


class RssForm(forms.Form):
    locale = forms.ChoiceField(LOCALE_UTC_OFFSET.items())
    days = forms.IntegerField()
    keywords = KeywordsField()


class AIndex(ABase):
    def get(self):
        self.response.out.write(render("index.html", {}))


class ARss(ABase):
    def get(self):
        if "days" not in self.request.GET:
            self.request.GET["days"] = "0"
        form = RssForm(self.request.GET)
        if not form.is_valid():
            self.error(500)
            self.response.out.write(form.errors)
            return
        data = form.clean_data
        items = search(data["keywords"], data["locale"], data["days"])
        template_values = {
            "request": self.request,
            "data": data,
            "items": items,
        }
        self.response.headers["content-type"] = "application/atom+xml"
        self.response.out.write(render("atom1.xml", template_values))


application = webapp.WSGIApplication(
    [('/', AIndex),
     ('/rss', ARss),
    ],
    debug=config.debug)


def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()
