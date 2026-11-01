"""Reads the text of a public web page from a link.

This is the one place where Felixa Community Health reaches out to the internet, and only for a link the person typed. Because the app may run on
a server, the fetch refuses anything that is not a public address: a link to localhost, a private network, or a cloud metadata service
would otherwise let a visitor use the app to read things only the server can reach. The check happens on the address the connection is
actually made to (so a name that changes its answer between the check and the connection cannot slip through), on every redirect,
and the page is cut at a size limit.
"""
import html
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
from html.parser import HTMLParser

MAX_BYTES = 2_000_000
TIMEOUT = 12
MAX_REDIRECTS = 3
MAX_LINKS = 5
PORTS = {80, 443, 8080, 8443}
USER_AGENT = 'FelixaCommunityHealth/1.0 (reads one public page a person asked for)'


class LinkError(ValueError):
    """A reason, in words for the person, why a link could not be read."""


def public_address(host, port):
    """The first public IP address `host` resolves to. Raises LinkError when it has none, or when any address it resolves to is not public."""
    try:
        found = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError:
        raise LinkError('that address could not be found') from None
    addresses = [ipaddress.ip_address(a.split('%')[0]) for a in found]
    if not addresses or not all(a.is_global for a in addresses):
        raise LinkError('that address is not a public web page')
    return str(sorted(addresses, key=lambda a: (a.version, str(a)))[0])


class _Plain(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((public_address(self.host, self.port), self.port), self.timeout)


class _Secure(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((public_address(self.host, self.port), self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)       # certificate checked against the NAME, connection made to the checked IP


def check_url(url):
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise LinkError('use a full web address starting with http:// or https://')
    if parts.username or parts.password:
        raise LinkError('a web address with a password in it is not accepted')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    if port not in PORTS:
        raise LinkError('that port is not allowed')
    return parts, port


def fetch_html(url, opener=None):
    """The page at `url` as text, following up to MAX_REDIRECTS redirects. `opener(scheme, host, port)` returns a connection; tests replace it."""
    for _ in range(MAX_REDIRECTS + 1):
        parts, port = check_url(url)
        make = opener or (lambda scheme, host, p: (_Secure if scheme == 'https' else _Plain)(host, p, timeout=TIMEOUT, **({'context': ssl.create_default_context()} if scheme == 'https' else {})))
        conn = make(parts.scheme, parts.hostname, port)
        try:
            path = urllib.parse.urlunsplit(('', '', parts.path or '/', parts.query, ''))
            conn.request('GET', path, headers={'Host': parts.netloc, 'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain;q=0.9',
                                               'Accept-Encoding': 'identity', 'Connection': 'close'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader('Location'):
                url = urllib.parse.urljoin(url, response.getheader('Location'))
                continue
            if response.status != 200:
                raise LinkError(f'the page answered with an error ({response.status})')
            kind = (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
            if not (kind.startswith('text/') or kind in ('application/xhtml+xml', '')):
                raise LinkError('that link is not a web page or a text file')
            body = response.read(MAX_BYTES + 1)
            charset = 'utf-8'
            for piece in (response.getheader('Content-Type') or '').split(';')[1:]:
                if piece.strip().lower().startswith('charset='):
                    charset = piece.split('=', 1)[1].strip().strip('"\'') or 'utf-8'
            try:
                text = body[:MAX_BYTES].decode(charset, errors='replace')
            except LookupError:
                text = body[:MAX_BYTES].decode('utf-8', errors='replace')
            return text, kind == 'text/plain'
        except (OSError, http.client.HTTPException) as e:
            raise LinkError('the page could not be reached') from e
        finally:
            conn.close()
    raise LinkError('the link redirects too many times')


class _Text(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'nav', 'header', 'footer', 'form', 'template', 'iframe', 'head'}
    BLOCK = {'p', 'div', 'li', 'br', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'section', 'article', 'pre', 'td', 'dd', 'dt'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def page_text(markup, plain=False):
    """The readable lines of a page: paragraphs and list items, one per line, with menus, scripts and footers left out."""
    if plain:
        raw = markup
    else:
        parser = _Text()
        parser.feed(markup)
        raw = ''.join(parser.parts)
    lines = [' '.join(html.unescape(line).split()) for line in raw.splitlines()]
    return [line for line in lines if len(line) >= 12]


def fetch_lines(url, opener=None):
    """(host, lines) for a public page. Raises LinkError."""
    markup, plain = fetch_html(url, opener)
    host = urllib.parse.urlsplit(url).hostname or 'link'
    lines = page_text(markup, plain)
    if not lines:
        raise LinkError('no readable text was found on that page (a page that needs a login or builds itself with scripts cannot be read)')
    return host, lines
