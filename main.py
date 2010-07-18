# coding: utf-8

import datetime
import logging
import os.path
import re
import urllib

from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.api import memcache

from django import newforms as forms
from django.newforms.util import ErrorList, ValidationError

import config
import amazonaws
import ssha


LOCALE_UTC_OFFSET = {
    'ca': datetime.timedelta(hours=-3, minutes=-30),
    'de': datetime.timedelta(hours=1),
    'fr': datetime.timedelta(hours=1),
    'jp': datetime.timedelta(hours=9),
    'uk': datetime.timedelta(),
    'us': datetime.timedelta(hours=-5),
}


class RssItem(db.Model):
    name = db.StringProperty()
    title = db.StringProperty()
    locale = db.StringProperty(choices=LOCALE_UTC_OFFSET.keys())
    days = db.IntegerProperty()
    keywords = db.TextProperty()
    password = db.StringProperty()
    lastaccess = db.DateTimeProperty(auto_now=True)


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
        if e.code != 'AWS.ECommerceService.NoExactMatches':
            raise
    return root


def parse_keywords(keywords):
    words = []
    for word in keywords.splitlines():
        word = word.replace('"', '')
        word = word.strip(" \t\r\n")
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
    name = forms.RegexField(re.compile(r"\S+"))
    title = forms.RegexField(re.compile(r"\S+"))
    locale = forms.ChoiceField(LOCALE_UTC_OFFSET.items())
    days = forms.IntegerField()
    keywords = KeywordsField()
    password = forms.RegexField(re.compile(r"\S+"))
    newpassword = forms.CharField(required=False)


class AIndex(ABase):

    def get(self):
        name = self.request.get("name", None)
        form = RssForm(self.request.GET)
        if name is not None:
            rssitem = RssItem.get_by_key_name(name)
            if rssitem is not None:
                form = RssForm({
                    "name": rssitem.name,
                    "title": rssitem.title,
                    "locale": rssitem.locale,
                    "days": str(rssitem.days),
                    "keywords": rssitem.keywords,
                })
        template_values = {
            'request': self.request,
            'form': form,
        }
        self.response.out.write(render("index.html", template_values))

    def post(self):
        form = RssForm(self.request.POST)
        if form.is_valid():
            data = form.clean_data
            rssitem = RssItem.get_by_key_name(data["name"])
            if (rssitem is not None
                    and not ssha.equals(rssitem.password, data["password"])):
                form.errors["password"] = ErrorList(
                        ["Name is already used and Password is not matched"])
            else:
                if rssitem is None:
                    rssitem = RssItem(key_name=data["name"])
                rssitem.name = data["name"]
                rssitem.title = data["title"]
                rssitem.locale = data["locale"]
                rssitem.days = data["days"]
                rssitem.keywords = data["keywords"]
                rssitem.password = ssha.ssha(data["password"])
                if data["newpassword"] != "":
                    rssitem.password = ssha.ssha(data["newpassword"])
                rssitem.put()
                self.redirect('/?' + urllib.urlencode({
                    "name": data["name"].encode("utf-8"),
                    "saved": "1",
                }))
                return
        template_values = {
            'request': self.request,
            'form': form,
        }
        self.response.out.write(render("index.html", template_values))


class ARss(ABase):

    def get(self, name):
        name = urllib.unquote_plus(name).decode("utf-8")
        rssitem = RssItem.get_by_key_name(name)
        if rssitem is None:
            self.error(404)
            self.response.out.write(u"404 Not Found")
            return
        # touch lastaccess
        rssitem.put()
        items = self._search(rssitem.keywords, rssitem.locale, rssitem.days)
        template_values = {
            "request": self.request,
            "rssitem": rssitem,
            "items": items,
        }
        self.response.headers["content-type"] = "application/atom+xml"
        self.response.out.write(render("atom1.xml", template_values))

    def _search(self, keywords, locale, days):
        keywords = " or ".join('"%s"' % word
                for word in parse_keywords(keywords))
        utcnow = datetime.datetime.utcnow()
        now = utcnow + LOCALE_UTC_OFFSET[locale]
        limit_date = now + datetime.timedelta(days=days)
        lastmonth = self._lastmonth(now)
        power = u"pubdate: after %s and keywords: %s" % (lastmonth, keywords)
        items = []
        for item in item_search(locale=locale,
                                SearchIndex='Books',
                                Power=power.encode("utf-8"),
                                Sort='daterank',
                                ResponseGroup='Medium'):
            # ignore collection item
            if item.find('ItemAttributes/ISBN') is None:
                continue
            (release_date_obj, release_date) = self._get_release_date(item)
            if release_date is None:
                continue
            if release_date_obj.date() <= limit_date.date():
                items.append({
                    'release_date': release_date,
                    'title': item.findtext('ItemAttributes/Title'),
                    'author': '/'.join(attr.text
                        for attr in item.findall('ItemAttributes/Author')),
                })
        return items

    def _lastmonth(self, now):
        m = now.replace(day=1) - datetime.timedelta(days=1)
        return m.strftime("%m-%Y")

    def _get_release_date(self, item):
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


application = webapp.WSGIApplication(
    [('/', AIndex),
     ('/rss/(.*)', ARss),
    ],
    debug=config.debug)


def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()
