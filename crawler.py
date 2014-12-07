#!/usr/bin/env python3

###############################################################################
# Module Imports
###############################################################################

import arrow
import peewee
import re
import requests

from bs4 import BeautifulSoup
from collections import namedtuple

###############################################################################
# Global Constants
###############################################################################

DBPATH = "/home/anqxyr/heap/_scp/scp-wiki.2014-12-01.db"

###############################################################################
# Decorators
###############################################################################


###############################################################################
# Database ORM Classes
###############################################################################

db = peewee.SqliteDatabase(DBPATH)


class BaseModel(peewee.Model):

    class Meta:
        database = db


class DBPage(BaseModel):
    url = peewee.CharField(unique=True)
    html = peewee.TextField()
    history = peewee.TextField(null=True)
    votes = peewee.TextField(null=True)


class DBTitle(BaseModel):
    url = peewee.CharField(unique=True)
    skip = peewee.CharField()
    title = peewee.CharField()


class DBImage(BaseModel):
    image_url = peewee.CharField(unique=True)
    image_source = peewee.CharField()
    image_data = peewee.BlobField()
    # for future use:
    #image_status = peewee.CharField()


class DBRewrite(BaseModel):
    url = peewee.CharField(unique=True)
    author = peewee.CharField()
    override = peewee.BooleanField()


class DBTag(BaseModel):
    tag = peewee.CharField(index=True)
    url = peewee.CharField()


class DBInfo(BaseModel):
    time_created = peewee.DateTimeField()
    time_updated = peewee.DateTimeField(null=True)

###############################################################################
# Primary Classes
###############################################################################


class WikidotConnector:

    def __init__(self, site):
        if site[-1] != '/':
            site += '/'
        self.site = site
        req = requests.Session()
        req.mount(site, requests.adapters.HTTPAdapter(max_retries=5))
        self.req = req

    ###########################################################################
    # Helper Methods
    ###########################################################################

    def _module(self, name, pageid, **kwargs):
        """Retrieve data from the specified wikidot module."""
        headers = {'Content-Type': 'application/x-www-form-urlencoded;'}
        payload = {
            'page_id': pageid,
            'pageId': pageid,  # fuck wikidot
            'moduleName': name,
            'wikidot_token7': '123456'}
        cookies = {'wikidot_token7': '123456'}
        for i in self.req.cookies:
            cookies[i.name] = i.value
        for k, v in kwargs.items():
            payload[k] = v
        data = self.req.post(self.site + 'ajax-module-connector.php',
                             data=payload, headers=headers, cookies=cookies)
        return data.json()

    ###########################################################################
    # Site-wide Methods
    ###########################################################################

    def list_all_pages(self):
        baseurl = '{}system:list-all-pages/p/{}'.format(self.site, '{}')
        soup = BeautifulSoup(self.html(baseurl))
        counter = soup.select('div.pager span.pager-no')[0].text
        last_page = int(counter.split(' ')[-1])
        for index in range(1, last_page + 1):
            soup = BeautifulSoup(self.html(baseurl.format(index)))
            pages = soup.select('div.list-pages-item a')
            for link in pages:
                url = self.site.rstrip('/') + link["href"]
                yield url

    ###########################################################################
    # Read-only Methods
    ###########################################################################

    def html(self, url):
        data = self.req.get(url)
        if data.status_code != 404:
            return data.text
        else:
            return None

    def pageid(self, html):
        pageid = re.search("pageId = ([^;]*);", html)
        if pageid is not None:
            pageid = pageid.group(1)
        return pageid

    def title(self, html):
        soup = BeautifulSoup(html)
        if soup.select("#page-title"):
            title = soup.select("#page-title")[0].text.strip()
        else:
            title = ""
        return title

    def history(self, pageid):
        if pageid is None:
            return None
        return self._module(
            name='history/PageRevisionListModule',
            pageid=pageid,
            page=1,
            perpage=1000000)['body']

    def votes(self, pageid):
        if pageid is None:
            return None
        return self._module(
            name='pagerate/WhoRatedPageModule',
            pageid=pageid)['body']

    def source(self, pageid):
        if pageid is None:
            return None
        html = self._module(
            name='viewsource/ViewSourceModule',
            pageid=pageid)['body']
        source = BeautifulSoup(html).text
        source = source[11:].strip()
        return source

    ###########################################################################
    # Active Methods
    ###########################################################################

    def auth(self, username, password):
        payload = {
            'login': username,
            'password': password,
            'action': 'Login2Action',
            'event': 'login'}
        url = 'https://www.wikidot.com/default--flow/login__LoginPopupScreen'
        self.req.post(url, data=payload)

    def edit(self, pageid, url, source, title, comments=None):
        wiki_page = url.split('/')[-1]
        lock = self._module('edit/PageEditModule', pageid, mode='page')
        params = {
            'source': source,
            'comments': comments,
            'title': title,
            'lock_id': lock['lock_id'],
            'lock_secret': lock['lock_secret'],
            'revision_id': lock['page_revision_id'],
            'action': 'WikiPageAction',
            'event': 'savePage',
            'wiki_page': wiki_page}
        self._module('Empty', pageid, **params)

    def comment(self, thread_id, source, title=None):
        params = {
            'threadId': thread_id,
            'parentId': None,
            'title': title,
            'source': source,
            'action': 'ForumAction',
            'event': 'savePost'}
        self._module('Empty', None, **params)


class Snapshot:

    RTAGS = ['scp', 'tale', 'hub', 'joke', 'explained', 'goi-format']

    def __init__(self):
        db.connect()
        db.create_tables([DBPage, DBTitle, DBImage, DBRewrite,
                          DBTag, DBInfo], safe=True)
        self.db = db
        self.wiki = WikidotConnector('http://www.scp-wiki.net')

    ###########################################################################
    # Scraping Methods
    ###########################################################################

    def _scrape_scp_titles(self):
        """Yield tuples of SCP articles' titles"""
        series_urls = [
            "http://www.scp-wiki.net/scp-series",
            "http://www.scp-wiki.net/scp-series-2",
            "http://www.scp-wiki.net/scp-series-3"]
        for url in series_urls:
            soup = BeautifulSoup(self.wiki.html(url))
            articles = [i for i in soup.select("ul > li")
                        if re.search("[SCP]+-[0-9]+", i.text)]
            for i in articles:
                url = "http://www.scp-wiki.net{}".format(i.a["href"])
                try:
                    skip, title = i.text.split(" - ", maxsplit=1)
                except:
                    skip, title = i.text.split(", ", maxsplit=1)
                yield {"url": url, "skip": skip, "title": title}

    def _scrape_image_whitelist(self):
        url = "http://scpsandbox2.wikidot.com/ebook-image-whitelist"
        req = requests.Session()
        req.mount('http://', requests.adapters.HTTPAdapter(max_retries=5))
        soup = BeautifulSoup(req.get(url).text)
        for i in soup.select("tr")[1:]:
            image_url = i.select("td")[0].text
            image_source = i.select("td")[1].text
            image_data = req.get(image_url).content
            yield {"image_url": image_url,
                   "image_source": image_source,
                   "image_data": image_data}

    def _scrape_rewrites(self):
        url = "http://05command.wikidot.com/alexandra-rewrite"
        req = requests.Session()
        site = 'http://05command.wikidot.com'
        req.mount(site, requests.adapters.HTTPAdapter(max_retries=5))
        soup = BeautifulSoup(req.get(url).text)
        for i in soup.select("tr")[1:]:
            url = "http://www.scp-wiki.net/{}".format(i.select("td")[0].text)
            author = i.select("td")[1].text
            if author.startswith(":override:"):
                override = True
                author = author[10:]
            else:
                override = False
            yield {"url": url, "author": author, "override": override}

    def _scrape_tag(self, tag):
        url = "http://www.scp-wiki.net/system:page-tags/tag/{}".format(tag)
        soup = BeautifulSoup(self.wiki.html(url))
        for i in soup.select("div.pages-list-item a"):
            url = "http://www.scp-wiki.net{}".format(i["href"])
            yield {"tag": tag, "url": url}

    ###########################################################################
    # Database Methods
    ###########################################################################

    def _page_to_db(self, url):
        print("saving\t\t\t{}".format(url))
        try:
            page = DBPage.get(DBPage.url == url)
        except DBPage.DoesNotExist:
            page = DBPage(url=url)
        html = self.wiki.html(url)
        # this will break if html is None
        # however html should never be None with the current code
        # so I'll leave it as is to signal bad pages on the site
        pageid = self.wiki.pageid(html)
        history = self.wiki.history(pageid)
        votes = self.wiki.votes(pageid)
        page.html = html
        page.history = history
        page.votes = votes
        page.save()

    def _meta_tables(self):
        print("collecting metadata")
        DBTitle.delete().execute()
        DBImage.delete().execute()
        DBRewrite.delete().execute()
        with db.transaction():
            titles = list(self._scrape_scp_titles())
            for idx in range(0, len(titles), 500):
                DBTitle.insert_many(titles[idx:idx + 500]).execute()
            images = list(self._scrape_image_whitelist())
            for idx in range(0, len(images), 500):
                DBImage.insert_many(images[idx:idx + 500]).execute()
            rewrites = list(self._scrape_rewrites())
            for idx in range(0, len(rewrites), 500):
                DBRewrite.insert_many(rewrites[idx:idx + 500]).execute()

    def _tag_to_db(self, tag):
        print("saving tag\t\t{}".format(tag))
        tag_data = list(self._scrape_tag(tag))
        urls = [i["url"] for i in tag_data]
        DBTag.delete().where((DBTag.tag == tag) & ~ (DBTag.url << urls))
        old_urls = DBTag.select(DBTag.url)
        new_data = [i for i in tag_data if i["url"] not in old_urls]
        with db.transaction():
            for idx in range(0, len(new_data), 500):
                DBTag.insert_many(new_data[idx:idx + 500]).execute()

    def _update_info(self, action):
        try:
            info_row = DBInfo.get()
        except DBInfo.DoesNotExist:
            info_row = DBInfo()
        if action == "created":
            time = arrow.utcnow().format("YYYY-MM-DD HH:mm:ss")
            info_row.time_created = time
        info_row.save()

    ###########################################################################
    # Public Methods
    ###########################################################################

    def take(self):
        self._update_info("created")
        self._meta_tables()
        for tag in Snapshot.RTAGS:
            self._tag_to_db(tag)
        for url in self.wiki.list_all_pages():
            self._page_to_db(url)

    def pagedata(self, url):
        """Retrieve PageData from the database"""
        try:
            data = DBPage.get(DBPage.url == url)
        except DBPage.DoesNotExist as e:
            raise e
        pd = namedtuple("PageData", "html history votes")
        return pd(data.html, data.history, data.votes)

    def tag(self, tag):
        """Retrieve list of pages with the tag from the database"""
        for i in DBTag.select().where(DBTag.tag == tag):
            yield i.url

    def rewrite(self, url):
        rd = namedtuple('DBRewrite', 'url author override')
        try:
            data = DBRewrite.get(DBRewrite.url == url)
            return rd(data.url, data.author, data.override)
        except DBRewrite.DoesNotExist:
            return False

    def images(self):
        images = {}
        im = namedtuple('Image', 'source data')
        for i in DBImage.select():
            images[i.image_url] = im(i.image_source, i.image_data)
        return images

    def title(self, url):
        try:
            return DBTitle.get(DBTitle.url == url).title
        except DBTitle.DoesNotExist:
            num = re.search('[0-9]+$', url).group(0)
            skip = 'SCP-{}'.format(num)
            return DBTitle.get(DBTitle.skip == skip).title


class Page:

    """Scrape and store contents and metadata of a page."""

    sn = Snapshot()   # Snapshot instance used to init the pages

    ###########################################################################
    # Constructors
    ###########################################################################

    def __init__(self, url=None):
        self.url = url
        self.data = None
        self.rating = None
        self.images = []
        self.history = []
        self.authors = []
        self.votes = []

        if url is not None:
            pd = self.sn.pagedata(url)
            self._parse_html(pd.html)
            self._raw_html = pd.html
            if pd.history is not None:
                self._parse_history(pd.history)
                self.authors = self._meta_authors()
            if pd.votes is not None:
                self._parse_votes(pd.votes)
        self._override()

    ###########################################################################
    # Misc. Methods
    ###########################################################################

    def _override(self):
        _inrange = lambda x: [Page("{}-{}".format(self.url, n)) for n in x]
        _except = lambda x: [p for p in self.children()
                             if p.url != "http://www.scp-wiki.net/" + x]
        ov_data = [("scp-1047-j", None)]
        ov_children = [
            ("scp-2998", _inrange, range(2, 11)),
            ("wills-and-ways-hub", _except, "marshall-carter-and-dark-hub"),
            ("serpent-s-hand-hub", _except, "black-queen-hub"),
            ("chicago-spirit-hub", list, "")]
        for partial_url, data in ov_data:
            if self.url == "http://www.scp-wiki.net/" + partial_url:
                self.data = data
        for partial_url, func, args in ov_children:
            if self.url == "http://www.scp-wiki.net/" + partial_url:
                new_children = func(args)
                self.children = lambda: new_children

    ###########################################################################
    # Parsing Methods
    ###########################################################################

    def _parse_html(self, raw_html):
        '''Retrieve title, data, and tags'''
        soup = BeautifulSoup(raw_html)
        rating_el = soup.select("#pagerate-button span")
        if rating_el:
            self.rating = rating_el[0].text
        try:
            comments = soup.select('#discuss-button')[0].text
            comments = re.search('[0-9]+', comments).group()
            self.comments = comments
        except:
            self.comments = 0
        self._parse_body(soup)
        self.tags = [a.string for a in soup.select("div.page-tags a")]
        self._parse_title(soup)
        if "scp" in self.tags:
            title_insert = "<p class='scp-title'>{}</p>{}"
        else:
            title_insert = "<p class='tale-title'>{}</p>{}"
        self.data = title_insert.format(self.title, self.data)

    def _parse_history(self, raw_history):
        soup = BeautifulSoup(raw_history)
        history = []
        Revision = namedtuple('Revision', 'number user time comment')
        for i in soup.select('tr')[1:]:
            rev_data = i.select('td')
            number = int(rev_data[0].text.strip('.'))
            user = rev_data[4].text
            time = arrow.get(rev_data[5].text, 'DD MMM YYYY HH:mm')
            time = time.format('YYYY-MM-DD HH:mm:ss')
            comment = rev_data[6].text
            history.append(Revision(number, user, time, comment))
        self.history = list(reversed(history))

    def _parse_votes(self, raw_votes):
        soup = BeautifulSoup(raw_votes)
        votes = []
        VoteData = namedtuple('VoteData', 'user vote')
        for i in soup.select('span.printuser'):
            user = i.text
            vote = i.next_sibling.next_sibling.text.strip()
            votes.append(VoteData(user, vote))
        self.votes = votes

    def _parse_body(self, soup):
        if not soup.select("#page-content"):
            self.data = None
            return
        data = soup.select("#page-content")[0]
        for i in data.select("div.page-rate-widget-box"):
            i.decompose()
        data = self._parse_tabviews(data)
        data = self._parse_collapsibles(data)
        data = self._parse_footnotes(data)
        data = self._parse_links(data)
        data = self._parse_quotes(data)
        data = self._parse_images(data)
        self.data = str(data)

    def _parse_title(self, soup):
        if soup.select("#page-title"):
            title = soup.select("#page-title")[0].text.strip()
        else:
            title = ""
        if "scp" in self.tags and re.search("[scp]+-[0-9]+$", self.url):
            title = "{}: {}".format(title, self.sn.title(self.url))
        self.title = title

    def _parse_images(self, data):
        images = self.sn.images()
        for i in data.select('img'):
            if i.name is None or not i.has_attr('src'):
                continue
            if i["src"] not in images:
                #loop through the image's parents, until we find what to cut
                for p in i.parents:
                    # old-style image formatting:
                    old_style = bool(p.select("table tr td img") and
                                     len(p.select("table tr td")) == 1)
                    new_style = bool("class" in p.attrs and
                                     "scp-image-block" in p["class"])
                    if old_style or new_style:
                        p.decompose()
                        break
                else:
                    # if we couldn't find any parents to remove,
                    # just remove the image itself
                    i.decompose()
            else:
                    self.images.append(i["src"])
                    page, image_url = i["src"].split("/")[-2:]
                    i["src"] = "images/{}_{}".format(page, image_url)
        return data

    def _parse_tabviews(self, data):
        soup = BeautifulSoup(str(data))
        for i in data.select("div.yui-navset"):
            wraper = soup.new_tag("div", **{"class": "tabview"})
            titles = [a.text for a in i.select("ul.yui-nav em")]
            tabs = i.select("div.yui-content > div")
            for k in tabs:
                k.attrs = {"class": "tabview-tab"}
                tab_title = soup.new_tag("div", **{"class": "tab-title"})
                tab_title.string = titles[tabs.index(k)]
                k.insert(0, tab_title)
                wraper.append(k)
            i.replace_with(wraper)
        return data

    def _parse_collapsibles(self, data):
        soup = BeautifulSoup(str(data))
        for i in data.select("div.collapsible-block"):
            link_text = i.select("a.collapsible-block-link")[0].text
            content = i.select("div.collapsible-block-content")[0]
            if content.text == "":
                content = i.select("div.collapsible-block-unfolded")[0]
                del(content["style"])
                content.select("div.collapsible-block-content")[0].decompose()
                content.select("div.collapsible-block-unfolded-link"
                               )[0].decompose()
            content["class"] = "collaps-content"
            col = soup.new_tag("div", **{"class": "collapsible"})
            content = content.wrap(col)
            col_title = soup.new_tag("div", **{"class": "collaps-title"})
            col_title.string = link_text
            content.div.insert_before(col_title)
            i.replace_with(content)
        return data

    def _parse_links(self, data):
        for i in data.select("a"):
            del(i["href"])
            i.name = "span"
            i["class"] = "link"
        return data

    def _parse_quotes(self, data):
        for i in data.select("blockquote"):
            i.name = "div"
            i["class"] = "quote"
        return data

    def _parse_footnotes(self, data):
        for i in data.select("sup.footnoteref"):
            i.string = i.a.string
        for i in data.select("sup.footnote-footer"):
            i["class"] = "footnote"
            del(i["id"])
            i.string = "".join([k for k in i.strings])
        return data

    def _meta_authors(self):
        au = namedtuple('author', 'username status')
        his_author = self.history[0].user
        rewrite = self.sn.rewrite(self.url)
        if rewrite:
            if rewrite.override:
                return [au(rewrite.author, 'original')]
            else:
                return [au(his_author, 'original'),
                        au(rewrite.author, 'rewrite')]
        else:
            return [au(his_author, 'original')]

    ###########################################################################

    def links(self):
        if self._raw_html is None:
            return []
        links = []
        soup = BeautifulSoup(self._raw_html)
        for a in soup.select("#page-content a"):
            if not a.has_attr("href") or a["href"][0] != "/":
                continue
            if a["href"][-4:] in [".png", ".jpg", ".gif"]:
                continue
            url = "http://www.scp-wiki.net{}".format(a["href"])
            url = url.rstrip("|")
            if url in links:
                continue
            links.append(url)
        return links

    def children(self):
        if not hasattr(self, "tags"):
            return []
        if not any(i in self.tags for i in [
                'scp', 'hub', 'goi2014', 'splash']):
            return []
        lpages = []
        for url in self.links():
            try:
                p = Page(url)
                try:
                    p.chapters = self.chapters
                except AttributeError:
                    pass
            except DBPage.DoesNotExist:
                continue
            if p.data is not None:
                lpages.append(p)
        if any(i in self.tags for i in ["scp", "splash"]):
            mpages = [i for i in lpages if
                      any(k in i.tags for k in ["supplement", "splash"])]
            return mpages
        if "hub" in self.tags and any(i in self.tags
                                      for i in ["tale", "goi2014"]):
            mpages = [i for i in lpages if any(
                k in i.tags for k in ["tale", "goi-format", "goi2014"])]

            def backlinks(page, child):
                if page.url in child.links():
                    return True
                soup = BeautifulSoup(child._raw_html)
                if soup.select("#breadcrumbs a"):
                    crumb = soup.select("#breadcrumbs a")[-1]
                    crumb = "http://www.scp-wiki.net{}".format(crumb["href"])
                    if self.url == crumb:
                        return True
                return False
            if any(backlinks(self, p) for p in mpages):
                return [p for p in mpages if backlinks(self, p)]
            else:
                return mpages
        return []

###############################################################################
# Methods For Retrieving Certain Pages
###############################################################################


def get_all():
    count = DBPage.select().count()
    for n in range(1, count // 50 + 2):
        query = DBPage.select().order_by(DBPage.url).paginate(n, 50)
        for i in query:
            yield Page(i.url)


def main():
    # test_url = 'http://testwiki2.wikidot.com/page1'
    # wiki_url = '/'.join(test_url.split('/')[:-1])
    # wiki = WikidotConnector(wiki_url)
    # pasw = '2A![]M/r}%t?,"GWQ.eH#uaukC3}#.*#uv=yd23NvkpuLgN:kPOBARb}:^IDT?%j'
    # wiki.auth(username='anqxyr', password=pasw)
    # wiki.edit_page(test_url,
    #                'this is page was edit by a robot',
    #                title='I am a Title too')
    #Snapshot().take()
    pass


if __name__ == "__main__":
    main()
