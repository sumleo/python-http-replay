
import dpkt
from .pcapload import HttpReplayPcapParser
from .log import HttpReplayLog
from .rules import HttpReplayRules

class HttpReplayUri:
    def __init__(self, uri):
        self.uri = uri
        self.lst = []

    def __str__(self):
        return self.uri

    def dump(self):
        import hashlib
        print('[%d] %s' % (len(self.lst), self.uri))
        for req, rep in self.lst:
            print('  %s %s (%d) %s | %s (%d) %s' % (req.method, req.uri,
                len(req.body), req.body, rep.status, len(rep.body),
                 hashlib.sha1(rep.body).hexdigest()))

    @staticmethod
    def same_req(a, b):
        # should we handle cookies?
        return a.uri == b.uri and a.method == b.method and a.body == b.body

    @staticmethod
    def same_rep(a, b):
        # should we handle headers?
        return a.status == b.status and a.body == b.body

    def add(self, req, rep):
        if rep.status in ['304', '401', '407', '503']:
            HttpReplayLog.debug('Ignoring %s response [%s]' % (rep.status, self.uri))
            return

        if rep.status == '206':
            # 206 Partial Content - Only accept (and convert in 200) if the
            # entire file is sent.
            import re
            rng = rep.headers.get('content-range', '')
            m = re.match('^bytes (\d+)-(\d+)/(\d+)$', rng)
            rng = [int(i) for i in m.groups()] if m else None
            if rng == [0, len(rep.body)-1, len(rep.body)]:
                HttpReplayLog.debug('Converting full 206 response into 200 [%s]' % self.uri)
                rep.status = '200'
                rep.reason = 'OK'
                del rep.headers['content-range']
            else:
                HttpReplayLog.debug('Ignoring 206 (partial content) response')

        if rep.status not in ['200', '301', '302', '404']:
            HttpReplayLog.warning('Unexpected response status %s for %s %s' % (rep.status, req.method, req.uri))

        if rep.headers.get('transfer-encoding', '').lower() == 'chunked':
            HttpReplayLog.debug('removing chunked transfer-encoding header')
            del rep.headers['transfer-encoding']

        if 'content-length' not in rep.headers and len(rep.body):
            HttpReplayLog.debug('adding content-length header')
            rep.headers['content-length'] = len(rep.body)

        for o_req, o_rep in self.lst:
            if self.same_req(o_req, req) and self.same_rep(o_rep, rep):
                HttpReplayLog.debug('Ignoring same reply/response tuple for %s' % self.uri)
                return

        self.lst.append((req, rep))

class HttpReplayDb:
    def __init__(self):
        self.db = {}

    def get(self, uri, create=False):
        if create and uri not in self.db:
            self.db[uri] = HttpReplayUri(uri)
        return self.db.get(uri)

    def count(self):
        return len(self.db)

    def dump(self):
        for obj in self.db.values():
            obj.dump()

    def finalize(self):
        for st_url, st_path in HttpReplayRules.static_files():
            self.add_static(st_url, st_path)
        for re_url1, re_url2 in HttpReplayRules.redirect_rules():
            self.add_redirect(re_url1, re_url2)

    def load_cap_file(self, fname, filt=''):
        for req, rep in HttpReplayPcapParser(fname, filt):
            req = HttpReplayRules.request_callback(req)
            if req:
                self.add_req_rep(req, rep)

    def load_fiddler_raw(self, fiddlerid, fclient, fserver):
        data_in = open(fclient, 'r').read()
        try:
            req = dpkt.http.Request(data_in)
            req.rawid = fiddlerid
        except:
            print 'Unable to load request from %s' % fclient
            return

        req = HttpReplayRules.request_callback(req)
        if not req or req.method == 'CONNECT':
            return

        data_out = open(fserver, 'r').read()
        try:
            rep = dpkt.http.Response(data_out)
            rep.rawid = fiddlerid
        except:
            print 'Unable to load %s %s response' % (req.method, req.uri)
            return

        HttpReplayLog.request(req, rep, loading=True)
        self.add_req_rep(req, rep)

    @staticmethod
    def uri_for(req):
        uri = req.uri
        if not uri.startswith('http://') and not uri.startswith('https://'):
            uri = 'http://' + req.headers.get('host', 'localhost') + uri
        if '?' in uri:
            uri = uri[:uri.index('?')]
        return uri

    def add_req_rep(self, req, rep):
        obj = self.get(self.uri_for(req), create=True)
        obj.add(req, rep)

    def add_static(self, uri, fname):
        req = dpkt.http.Request(method='GET', uri=uri)
        rep = dpkt.http.Response(status='200', reason='OK',
            body=open(fname).read())
        rep.rawid = 'S'
        HttpReplayLog.request(req, rep, loading=True)
        self.add_req_rep(req, rep)

    def add_redirect(self, uri, redir):
        req = dpkt.http.Request(method='GET', uri=uri)
        rep = dpkt.http.Response(status='302', reason='Found')
        rep.headers['location'] = redir
        rep.rawid = 'R'
        HttpReplayLog.request(req, rep, loading=True)
        self.add_req_rep(req, rep)

    def response_for(self, req):
        obj, lst = self.get(self.uri_for(req)), []
        if obj:
            for l_req, l_rep in obj.lst:
                if HttpReplayUri.same_req(l_req, req):
                    lst.append((l_req, l_rep))
            if len(lst) == 1:
                return lst[0][1]
            if len(lst) > 1:
                return HttpReplayRules.choose_reply(req, lst)
        return None

