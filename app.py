"""Felixa Community Health: web app.

Upload or paste any communication. Felixa Community Health rates 13 observable communication signals, writes
evidence-linked guidance, and runs Vibe Check: friendliness, sentiment, toxicity, flagged messages,
trend and forecast. Everything runs on a local Ollama model; no cloud API or key is used.
"""
import base64
import csv
import datetime
import hashlib
import zipfile
import io
import json
import logging
import math
import os
import re
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, List, Optional
from urllib.parse import quote, unquote

from fastapi.exceptions import RequestValidationError
from fastapi import Depends, FastAPI, File, Form, Path as PathParam, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

import actview
import analysis
import bei_charts
import config
import dashboard
import flagged
import detail
import measures
import pipeline
import prices
import track
import updater
import report
import rules
import report_export
import store
import cloud
import linkfetch
import voice
import workplace
from model import local_model_ready, local_models
import vibe_check
from vibe_check import SENSITIVITY_LABELS, ingest
from vibe_check.common import long_date, parse_ts

log = logging.getLogger('bei')
BASE_DIR = config.BASE_DIR
app = FastAPI(title=config.PRODUCT, docs_url=None, redoc_url=None, openapi_url=None)

SESSION_COOKIE = '__Host-felixa_session'
SESSION_SECONDS = 30 * 24 * 60 * 60            # a sign-in lasts 30 days, so the password is typed about once a month; FELIXA_SESSION_DAYS changes it


def session_seconds() -> int:
    try:
        days = float(os.getenv('FELIXA_SESSION_DAYS', ''))
        return max(60, int(days * 86400)) if days > 0 else SESSION_SECONDS
    except ValueError:
        return SESSION_SECONDS
MAX_QUEUED_JOBS = 5


# ---- sign-in -----------------------------------------------------------------------
class Throttled(Exception):
    """A client has sent too many wrong passwords and has to wait; `wait` is the seconds that are left."""

    def __init__(self, wait):
        super().__init__(wait)
        self.wait = wait


class SignIn:
    """What the server remembers about signing in, kept in one place so that the sign-in form and HTTP Basic obey the same rules.
    Guessing: attempt() is the only place a username and password are compared. A client (an address) may send a few wrong passwords;
    from the fifth in a row each one makes it wait longer (WAITS) before it is looked at again, and while it waits attempt() raises
    Throttled without comparing anything, so that a right guess is not told from a wrong one. Nothing sleeps: a throttled request is
    answered at once, so a flood of bad sign-ins cannot tie up the threads that serve everyone else. Only time clears a client's count
    (FORGET_SECONDS without a wrong password), not a sign-in that works: behind a shared address, a script using Basic could otherwise
    send one between the guesses of someone else.
    Sessions: the cookie holds a random token and nothing computed from the password, and the server keeps the only record of it (a
    hash of the token, and when it ends). Signing out deletes the record, and a cookie that someone captured says nothing about the
    password. The records are kept in the database as well as in memory, so a restart (a new deploy) does not sign anyone out."""

    WAITS = (0, 0, 0, 0, 2, 4, 8, 16, 32, 64, 128, 256, 300)       # seconds to wait after the 1st, 2nd ... wrong password in a row; the last repeats
    FORGET_SECONDS = 15 * 60
    MAX_CLIENTS = 10_000                                           # clients with wrong passwords remembered; the quietest are forgotten first
    MAX_SESSIONS = 10_000                                          # sessions kept; the oldest end first

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.failures = OrderedDict()       # client -> (wrong passwords in a row, when the last came); the longest quiet first
        self.sessions = OrderedDict()       # sha256 of a session token -> when it ends; the oldest first, so the ended ones come first too

    def attempt(self, client, username, password):
        """True when the username and password are the configured ones. Raises Throttled while the client has to wait."""
        expected = os.getenv('FELIXA_PASSWORD', '')
        with self.lock:
            now = self.clock()
            count, last = self.failures.get(client, (0, 0.0))
            if count and now - last > self.FORGET_SECONDS:
                count = 0
            wait = last + self.WAITS[count - 1] - now if count else 0
            if wait > 0:
                raise Throttled(math.ceil(wait))
            signed_in = bool(expected) & secrets.compare_digest(username.encode(), os.getenv('FELIXA_USERNAME', 'felixa').encode()) \
                & secrets.compare_digest(password.encode(), expected.encode())
            if not signed_in:
                self.failures.pop(client, None)                    # put back last, so the table stays in order of the last wrong password
                if len(self.failures) >= self.MAX_CLIENTS:
                    self.failures.popitem(last=False)
                self.failures[client] = (min(count + 1, len(self.WAITS)), now)
            return signed_in

    @staticmethod
    def session_key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def start_session(self):
        """Records a new session and returns the token for its cookie."""
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            dropped = []
            while self.sessions and (len(self.sessions) >= self.MAX_SESSIONS or next(iter(self.sessions.values())) <= now):
                dropped.append(self.sessions.popitem(last=False)[0])
            self.sessions[self.session_key(token)] = now + session_seconds()
        self._keep(lambda: (store.session_drop(*dropped) if dropped else None, store.session_save(self.session_key(token), time.time() + session_seconds())))
        return token

    @staticmethod
    def _keep(action):
        """Runs a database step of a session. A failure never stops a sign-in: the session then lives in memory only, as before."""
        try:
            return action()
        except Exception:
            log.warning('could not keep a session in the database')

    def valid_session(self, token):
        key = self.session_key(token)
        with self.lock:
            ends = self.sessions.get(key)
            if ends is not None:
                if ends > self.clock():
                    return True
                self.sessions.pop(key, None)                         # over: gone from memory and from the database
                self._keep(lambda: store.session_drop(key))
                return False
        left = self._keep(lambda: store.session_ends(key))           # not in memory: a session that began before a restart
        if left is None:
            return False
        with self.lock:
            self.sessions[key] = self.clock() + max(0.0, left - time.time())
        return True

    def end_session(self, token):
        key = self.session_key(token)
        with self.lock:
            self.sessions.pop(key, None)
        self._keep(lambda: store.session_drop(key))


sign_in = SignIn()


def client_of(request):
    """The address that stands for the sender of a request in the count of wrong passwords. Behind a proxy it is the proxy's, unless
    uvicorn is told to trust the proxy's X-Forwarded-For (FORWARDED_ALLOW_IPS), which it then puts in request.client itself."""
    return request.client.host if request.client else ''


def basic_credentials(request):
    """The (username, password) of an Authorization: Basic header, or None when there is none or it is malformed."""
    try:
        scheme, token = request.headers.get('authorization', '').split(' ', 1)
        username, password = base64.b64decode(token, validate=True).decode().split(':', 1)
    except (ValueError, UnicodeError):
        return None
    return (username, password) if scheme.lower() == 'basic' else None


LOGIN_SCRIPT = """if (window.isSecureContext === false) {document.getElementById('plain-http').hidden = false;
    document.getElementById('username').setAttribute('aria-describedby', 'plain-http');}"""


def login_page(error=False, wait=0):
    """The sign-in page: after a wrong password with `error`, and with `wait` (seconds) when the client has to wait before another try.
    The session cookie is Secure and __Host- prefixed, which a browser keeps only on an https page or at a localhost address. Opened over
    plain http at a LAN address or a host name, the right password was accepted and the browser then dropped the cookie, so the form came
    back with no word of why. Whether the browser will keep it is exactly window.isSecureContext, which the server cannot know (behind a
    proxy that ends https it sees http), so every version of the page carries a notice that a script shows when that is false."""
    notice = ''
    if wait:
        notice = f'Too many incorrect attempts. Please try again in {wait} second{"" if wait == 1 else "s"}.'
    elif error:
        notice = 'Incorrect username or password. Please try again.'
    message = f'<p role="alert" style="color:#a32121">{notice}</p>' if notice else ''
    return HTMLResponse("""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in | Felixa Community Health</title>
    <link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="48x48"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <style>body{margin:0;background:#fbf6ec;color:#2a2118;font:17px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}
    main{box-sizing:border-box;background:white;border-radius:20px;padding:40px;width:min(440px,92vw);box-shadow:0 12px 50px #7a481612;border-top:4px solid #F27435}
    h1{font-size:24px;margin:0 0 8px}.logo{display:block;height:64px;width:auto;margin:0 0 18px}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}p{line-height:1.5;color:#6a5b4b}label{display:block;margin:22px 0 8px}
    input,button{box-sizing:border-box;width:100%;padding:13px;font:inherit;border-radius:8px;border:1px solid #d9c9b3}
    button{margin-top:26px;background:#a8420a;color:white;border:0;cursor:pointer;font-weight:650}input:focus{outline:2px solid #c2540f}
    #plain-http{background:#fff4e0;color:#6b4300;padding:12px 16px;border-radius:8px;font-size:15px}</style></head>
    <body><main><img class="logo" src="/logo.png" alt="Felixa Community Health" width="179" height="64"><h1 class="sr">Sign in</h1><p>Sign in to your workplace.</p>
    <p id="plain-http" role="alert" hidden>Sign-in needs a secure connection. This page is open over plain http, so your browser would not keep
    your sign-in and you would land back here. Open the app over https, or on the computer that runs it at http://127.0.0.1 (same port).</p>""" + message + """
    <form method="post" action="/login"><label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" required autofocus>
    <label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button></form></main>
    <script>""" + LOGIN_SCRIPT + """</script></body></html>""",
                        status_code=429 if wait else 401 if error else 200, headers={'Retry-After': str(wait)} if wait else None)


@app.get('/login', response_class=HTMLResponse)
def login_get():
    return login_page()


@app.post('/login')
def login_post(request: Request, username: str = Form(''), password: str = Form('')):
    try:
        signed_in = sign_in.attempt(client_of(request), username, password)
    except Throttled as slow:
        return login_page(wait=slow.wait)
    if not signed_in:
        return login_page(error=True)
    response = RedirectResponse('/', status_code=303)
    response.set_cookie(SESSION_COOKIE, sign_in.start_session(), max_age=session_seconds(), secure=True, httponly=True, samesite='strict', path='/')
    return response


@app.post('/logout')
def logout(request: Request):
    sign_in.end_session(request.cookies.get(SESSION_COOKIE, ''))
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite='strict', path='/')
    return response


# Scripts may only come from this origin's /static files, plus the one inline script of the sign-in page (named by its hash, since
# /static needs a sign-in). Inline styles stay allowed: the report and sign-in pages carry their own <style>.
def script_hash(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


# The Vibe Check charts ship their own small script inline (the hover tooltips); it is allowed by its hash, so nothing else inline can run.
CSP = ("default-src 'self'; script-src 'self' " + script_hash(LOGIN_SCRIPT) + ' ' + script_hash(vibe_check.JS) + "; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def harden(response, path, request=None):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = CSP
    if request is not None and request.headers.get('x-forwarded-proto', request.url.scheme).split(',')[0].strip().lower() == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    if not path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


LOCAL_HOSTS = {'127.0.0.1', 'localhost', '[::1]'}
HOST_HEADER = re.compile(r'(\[[0-9a-f:]+\]|[a-z0-9.-]+)(?::\d{1,5})?')


def unexpected_host(request):
    """True when the Host header names something other than this computer or a name in FELIXA_ALLOWED_HOSTS. Without a password
    that is how DNS rebinding looks: a hostile site whose name now points at 127.0.0.1. The raw header is parsed here because
    libraries read a malformed one differently."""
    match = HOST_HEADER.fullmatch(request.headers.get('host', '').lower())
    allowed = LOCAL_HOSTS | {h.strip().lower() for h in os.getenv('FELIXA_ALLOWED_HOSTS', '').split(',') if h.strip()}
    return not match or match.group(1) not in allowed


def cross_site(request):
    """True for a write that a page on another site started. A browser says so in Sec-Fetch-Site and Origin. An Origin of
    'null' (sandboxed frames, https-to-http form posts) matches no host, so it is refused too. Scripts such as curl send
    neither header and pass."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return False
    if request.headers.get('sec-fetch-site', 'same-origin') not in ('same-origin', 'none'):
        return True
    host = request.headers.get('host', '').lower()
    origin = request.headers.get('origin', '').lower()
    return bool(origin) and origin not in ('http://' + host, 'https://' + host)


# ---- request size ------------------------------------------------------------------------
UPLOAD_PATH = re.compile(r'/analysis/\d+/(?:upload|raw-csv|add-voice)')
PASTE_PATH = re.compile(r'/analysis/\d+/add-text')


def body_limit(path):
    """The most bytes the body of a request to this path may hold: MAX_UPLOAD_FILES files of the full size and the form fields that go
    with them on the routes that take files, one field as large as a file (what read_paste accepts) on the paste route, a small form
    on every other. A path that matches neither pattern (a trailing slash, say) gets the smallest limit, so a mistake here refuses
    too much, never too little. Read when called, like the settings it is made of, so a test can patch them."""
    if UPLOAD_PATH.fullmatch(path):
        return config.MAX_UPLOAD_BYTES * config.MAX_UPLOAD_FILES + config.MAX_FORM_BYTES
    if PASTE_PATH.fullmatch(path):
        return config.MAX_UPLOAD_BYTES + config.MAX_FORM_BYTES
    return config.MAX_FORM_BYTES


class BodyTooLarge(MultiPartException):
    """Raised into the app when a body of undeclared length passes its limit while the app is reading it. A MultiPartException because the
    Starlette that requirements.txt pins closes the files it has spooled only on an error of that kind: any other leaves a half-received
    file open, and unlinked on disk, until the next garbage collection."""


async def refuse_body(scope, receive, send, close=False):
    """Sends the 413 for BodyLimit. The connection is closed after it only when a body was cut off in mid-stream, to stop the sender: a
    browser that declared too large a body is let finish sending it (the server reads and drops what is left), because a connection
    closed under a sender can reset before the sender has read the answer."""
    text = (f'That request is too large. A file may be up to {config.MAX_UPLOAD_BYTES // 1_000_000} MB and one upload '
            f'{config.MAX_UPLOAD_BYTES * config.MAX_UPLOAD_FILES // 1_000_000} MB in all; save a long paste as a .txt file and import it.')
    await HTMLResponse(text, status_code=413, headers={'Connection': 'close'} if close else None)(scope, receive, send)


class BodyLimit:
    """Answers a request whose body is too large with a 413 before anything reads it. Starlette parses a form before the route runs and
    spools every file in it to disk, so the 15 MB check in ingest_files came after a 400 MB upload had been received in full. /login,
    which anyone can reach, parses a form as well, and the Starlette that requirements.txt pins buffers an urlencoded field in memory
    without a limit. A body with a declared length over its limit is refused unread. A body with none (chunked, so not a browser's) is
    counted as the app reads it and cut off at the limit; FastAPI turns that failed read into a 400 of its own, so whatever the app
    answers after the cut is dropped and the 413 is sent in its place.
    It is added before the sign-in middleware, which makes it the innermost, and must stay inside it. That middleware answers a visitor
    without reading the body and lets /login, which is open to everyone, through to be covered here. It is also a BaseHTTPMiddleware,
    which wraps an error raised from the receive() beneath it in an ExceptionGroup: with BodyLimit outside it, the form parser would get
    that group in place of the error, and the pinned Starlette would not close the files it had spooled. It adds the security headers to
    the 413 as well."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        limit = body_limit(scope['path'])
        declared = Request(scope).headers.get('content-length', '')
        if declared.isdecimal() and int(declared) > limit:
            return await refuse_body(scope, receive, send)
        seen, cut = 0, False

        async def counted():
            nonlocal seen, cut
            message = await receive()
            seen += len(message.get('body', b''))
            if seen > limit:
                cut = True
                raise BodyTooLarge('The request body is too large.')
            return message

        async def unless_cut(message):
            if not cut:
                await send(message)

        try:
            await self.app(scope, counted, unless_cut)
        except Exception:                                   # BodyTooLarge, or what a layer of the app made of it
            if not cut:
                raise
        if cut:
            await refuse_body(scope, receive, send, close=True)


app.add_middleware(BodyLimit)


PUBLIC_ICONS = {'/favicon.ico': 'favicon.ico', '/favicon.svg': 'favicon.svg', '/apple-touch-icon.png': 'apple-touch-icon.png', '/logo.png': 'logo_wide.png'}


@app.middleware('http')
async def protect_online_workspace(request: Request, call_next):
    path = request.url.path
    if path == '/health':
        return harden(await call_next(request), path, request)
    if path in PUBLIC_ICONS:                                            # the tab icon and the logo must load on the sign-in page too
        return harden(FileResponse(BASE_DIR / 'static' / PUBLIC_ICONS[path], headers={'Cache-Control': 'public, max-age=86400'}), '/static/', request)
    if cross_site(request):
        return harden(HTMLResponse('Invalid request origin.', status_code=403), path, request)
    password = os.getenv('FELIXA_PASSWORD', '')
    if not password:
        if os.getenv('FELIXA_REQUIRE_AUTH') == '1':
            return harden(HTMLResponse('Set FELIXA_PASSWORD before opening this workspace.', status_code=503), path, request)
        if unexpected_host(request):
            return harden(HTMLResponse('Unexpected host name. Without a password this workspace answers only to localhost names. '
                                       'To open it under another name, list that name in FELIXA_ALLOWED_HOSTS.', status_code=400), path, request)
        return harden(await call_next(request), path, request)
    valid = sign_in.valid_session(request.cookies.get(SESSION_COOKIE, ''))
    given = None if valid else basic_credentials(request)
    if given:
        try:
            valid = sign_in.attempt(client_of(request), *given)
        except Throttled as slow:
            return harden(login_page(wait=slow.wait), path, request)
    if not valid and path != '/login':
        return harden(RedirectResponse('/login', status_code=303), path, request)
    return harden(await call_next(request), path, request)


# ---- templates and helpers -----------------------------------------------------------
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))
templates.env.globals.update(
    product=config.PRODUCT, product_long=config.PRODUCT_LONG, version=config.VERSION, with_problems=pipeline.WITH_PROBLEMS,
    max_file_mb=config.MAX_UPLOAD_BYTES // 1_000_000, max_upload_mb=config.MAX_UPLOAD_BYTES * config.MAX_UPLOAD_FILES // 1_000_000,
    asset_v=hashlib.sha1(b''.join((BASE_DIR / 'static' / f).read_bytes() for f in ('style.css', 'app.js'))).hexdigest()[:8])

store.init_db()
store.interrupt_stale_jobs()
config.sweep_uploads()


def redirect(url, msg=None):
    response = RedirectResponse(url, status_code=303)
    if msg:     # a one-shot flash cookie, not a URL parameter: nobody can link to a message and a reload does not repeat it
        response.set_cookie('flash', quote(msg), max_age=60, httponly=True, samesite='lax')
    return response


def check_steps_rail(a):
    """The side bar of a workplace page: New analytics and its steps, always open. The first step asks before it replaces the messages already loaded."""
    aid = a['id']
    first = f'/analysis/{aid}/check/1' if not store.count_units(aid) else f'/analysis/{aid}/workspace#new'
    steps = [{'num': n, 'label': label, 'href': first if n == 1 else f'/analysis/{aid}/check/{n}', 'state': 'todo'} for n, label in workplace.CHECK_STEPS]
    return {'steps': steps, 'foot': None, 'setup': False}


def current_workplace(row, names):
    """The workplace the side bar and the top bar are about: the one on the page, else the most recently touched. None before the first one exists."""
    if row is not None and row['status'] != 'draft' and row['id'] in {w['id'] for w in names}:
        wid = row['id']
    elif names:
        wid = names[0]['id']
    else:
        return None
    a = store.get_analysis(wid)
    name = next(w['name'] for w in names if w['id'] == wid)
    return {'id': wid, 'name': name, 'units': store.count_units(wid), 'has_results': report.has_results(analysis.metrics_for(wid), store.latest_vibe(wid)),
            'initial': ((a['user_name'] or name or 'F').strip()[:1] or 'F').upper()}


def render(request, name, **context):
    flash = request.cookies.get('flash')
    row = context.get('a')
    if 'rail' not in context:
        context['rail'] = check_steps_rail(row) if row is not None and row['status'] != 'draft' else {'steps': [], 'foot': None, 'setup': False}
    names = store.workplace_names()
    response = templates.TemplateResponse(request=request, name=name, context={'request': request, 'msg': unquote(flash) if flash else None,
                                                                              'top_workplaces': names, 'cur': current_workplace(row, names), 'update_ready': updater.cached_newer(), **context})
    if flash:
        response.delete_cookie('flash')
    return response


class PageError(Exception):
    def __init__(self, status, heading, message, back=None):
        self.status, self.heading, self.message, self.back = status, heading, message, back


def not_found():
    raise PageError(404, 'Analysis not found', 'This analysis does not exist. It may have been removed, or the link may be mistyped.')


def error_response(request, status, heading, message, back=None):
    accept = request.headers.get('accept', '')
    if 'application/json' in accept and 'text/html' not in accept:
        return JSONResponse({'detail': message}, status_code=status)
    return templates.TemplateResponse(request=request, name='error.html', status_code=status, context={
        'request': request, 'status': status, 'heading': heading, 'message': message, 'back': back})


@app.exception_handler(PageError)
def page_error_handler(request, exc):
    return error_response(request, exc.status, exc.heading, exc.message, exc.back)


@app.exception_handler(StarletteHTTPException)
def http_error_handler(request, exc):
    if exc.status_code == 404:
        return error_response(request, 404, 'Page not found', 'There is nothing at this address.')
    if exc.status_code == 405:
        return error_response(request, 405, 'Not available this way', 'This address cannot be opened like that.')
    return error_response(request, exc.status_code, 'Something went wrong', str(exc.detail))


@app.exception_handler(RequestValidationError)
def validation_error_handler(request, exc):
    if any(e.get('loc', ('',))[0] == 'path' for e in exc.errors()):
        return error_response(request, 404, 'Page not found', 'That address is not valid.')
    return error_response(request, 422, 'We could not read that request', 'Some of the information sent was missing or not valid.')


AnalysisId = Annotated[int, PathParam(ge=1, le=2 ** 63 - 1)]                       # SQLite integers are 64-bit; a larger id cannot exist, so it is a 404


def report_context(aid, what):
    ctx = report.build(aid)
    if not ctx:
        not_found()
    if not ctx['has_results']:
        raise PageError(404, 'No results yet', f'Run a check on the workplace page to create this {what}.',
                        back=(f'/analysis/{aid}/workspace', 'Open the workspace'))
    return ctx


# ---- creating a workplace ----------------------------------------------------------------
ENV_NAMES = [name for name, _ in workplace.ENVIRONMENTS]
SETUP_LAST = len(workplace.SETUP_STEPS)


def setup_rows(a):
    """What the right-hand panel shows while a workplace is being set up: (icon, label, value) for each answer so far."""
    goals = [g for g in json.loads(a['goals_json'] or '[]') if g] + ([a['goal_text']] if a['goal_text'] else [])
    you = ', '.join(x for x in (a['user_name'], a['user_role']) if x)
    members = f'{int(a["members"]):,}' if str(a['members'] or '').isdigit() else str(a['members'] or '')
    return [('users', 'Environment', a['subject_type']), ('target', 'Workplace', a['person_name']), ('users', 'Members', members), ('clock', 'Time zone', a['time_zone']),
            ('gauge', 'Area', a['person_role']), ('chat', 'Chat', a['channel']), ('person', 'You', you), ('check', 'Goals', '; '.join(goals))]


def setup_context(a, step):
    env, env_other = workplace.split_choice(a['subject_type'], ENV_NAMES)
    area, area_other = workplace.split_choice(a['person_role'], workplace.AREAS)
    channels_on, channel_other = workplace.split_channels(a['channel'])
    channels_on = channels_on + ([workplace.OTHER] if channel_other else [])
    role, role_other = workplace.split_choice(a['user_role'], workplace.USER_ROLES)
    goals = [g for g in json.loads(a['goals_json'] or '[]') if g]
    return {'a': a, 'step': step, 'steps': workplace.SETUP_STEPS, 'last': SETUP_LAST, 'environments': workplace.ENVIRONMENTS,
            'areas': workplace.AREAS, 'channels': workplace.CHANNELS, 'discord_url': workplace.DISCORD_BOT_URL, 'roles': workplace.USER_ROLES, 'goal_options': workplace.GOAL_OPTIONS,
            'env': env, 'env_other': env_other, 'area': area, 'area_other': area_other, 'channels_on': channels_on, 'channel_other': channel_other,
            'role': role, 'role_other': role_other, 'goals': goals, 'goal_other': a['goal_text'] or '', 'time_zones': workplace.time_zones(),
            'members_text': a['members'] or '',
            'summary_title': 'Your workplace', 'summary_rows': setup_rows(a),
            'rail': wizard_rail(a['id'], workplace.SETUP_STEPS, f'/analysis/{a["id"]}/step', step,
                                foot={'icon': 'eye', 'label': 'Workplace', 'href': f'/analysis/{a["id"]}/workspace'} if a['status'] != 'draft' else None)}


CHIP_WORDS = {'none': 'Low', 'small': 'Low', 'clear': 'Moderate', 'steep': 'High', 'early': 'Early', 'Rare': 'Low', 'Some': 'Moderate', 'Often': 'High'}


def sparkline(values, width=170, height=56):
    """A small line with a soft fill under it, as inline SVG points: (line points, area path). Empty when there is nothing to draw."""
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return '', ''
    top = max(max(vals), 10.0)
    pts = [(2 + (width - 4) * i / (len(vals) - 1), height - 4 - (height - 10) * v / top) for i, v in enumerate(vals)]
    line = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
    area = f'M{pts[0][0]:.1f},{height} L' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in pts) + f' L{pts[-1][0]:.1f},{height} Z'
    return line, area


def home_view(aid):
    """What the Home page shows for one workplace: the Vibe Check, the health score, the sentiment, the four modules, and how many things want a look. None until it has results."""
    ctx = report.build(aid)
    if not ctx:
        return None
    if not ctx['has_results']:                    # the same cards, empty, until the first analysis has run
        return {'ctx': {'answers': [], 'families': None}, 'kpi': {}, 'vibe': None, 'health': None, 'sentiment': None, 'alerts': 0, 'badges': 0, 'flagged': 0, 'read': 0, 'sources': 0,
                'model': '', 'empty': True, 'modules': [{'key': k, 'name': n, 'value': None, 'chip': '', 'tone': 'none', 'line': '', 'area': ''}
                                                         for k, n in (('escalation', 'Escalation'), ('conflict', 'Conflict'), ('empathy', 'Empathy'), ('passive', 'Passive-aggressive'))]}
    ms, vibe = ctx.get('measures'), ctx['vibe'] if ctx['vibe'] and ctx['vibe'].get('status') == 'ok' else None
    kp = {}
    if ctx.get('measures'):
        kp = {k['key']: k for k in actview.build(ctx, 'previous')['kpis']}
    view = {'ctx': ctx, 'kpi': kp, 'vibe': None, 'health': None, 'sentiment': None, 'modules': [], 'alerts': 0, 'badges': 0, 'flagged': 0, 'read': 0, 'sources': 0, 'model': ctx['a']['model_used'] or ''}
    if vibe:
        pct = (vibe.get('sentiment') or {}).get('pct') or {}
        view['flagged'], view['read'], view['sources'] = len(vibe.get('flagged') or []), vibe.get('analyzed_n') or 0, len(vibe.get('sources') or [])
        view['vibe'] = {'percent': round(vibe['score'] * 10), 'label': vibe.get('label', ''), 'score': vibe['score']}
        view['sentiment'] = {'friendly': pct.get('friendly', 0), 'neutral': pct.get('neutral', 0), 'unfriendly': pct.get('unfriendly', 0)}
    if ms:
        h = ms['health']
        view['health'] = {'score': round(h['score']), 'status': h['status'], 'tone': h['tone']}
        o = ms['m']['overall']
        n = o['n'] or 1
        for card in ms['cards']:
            rate_pct = round(100.0 * o[card['kind']] / n)
            line, area = sparkline(card['series'])
            view['modules'].append({'key': card['key'], 'name': card['name'], 'value': rate_pct, 'chip': CHIP_WORDS.get(card.get('word') or card['level'], card['level']), 'tone': card['tone'],
                                    'line': line, 'area': area})
        view['alerts'] = sum(1 for x in ms['alerts'] if x['level'] != 'good')
        view['badges'] = ms['badges_earned']
    return view


@app.get('/ask', response_class=HTMLResponse)
def ask_page(request: Request):
    return render(request, 'ask.html')


@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    names = store.workplace_names()
    cur = current_workplace(None, names)
    home = home_view(cur['id']) if cur else None
    return render(request, 'index.html', home=home, alert_count=home['alerts'] if home else 0)


@app.get('/new')
def new_workplace():
    return redirect(f'/analysis/{store.create_analysis()}/step/1')


@app.get('/analysis/{aid}/step/{step}', response_class=HTMLResponse)
def onboarding_get(request: Request, aid: AnalysisId, step: int):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    if not 1 <= step <= SETUP_LAST:
        raise PageError(404, 'Step not found', f'There is no step {step}. Setting up a workplace has steps 1 to {SETUP_LAST}.',
                        back=(f'/analysis/{aid}/step/1', 'Go to step 1'))
    return render(request, 'onboarding.html', **setup_context(a, step))


def setup_saved(aid, step, error, **fields):
    """Saves one step and moves on, or goes back to the step with what was wrong."""
    if error:
        return redirect(f'/analysis/{aid}/step/{step}', error)
    store.update_analysis(aid, **fields)
    return redirect(f'/analysis/{aid}/step/{step + 1}')


@app.post('/analysis/{aid}/step/1')
async def step1(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    value, error = workplace.pick(await request.form(), 'environment', ENV_NAMES, 'what kind of place this is')
    return setup_saved(aid, 1, error, subject_type=value)


@app.post('/analysis/{aid}/step/2')
async def step2(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    form = await request.form()
    name = ' '.join(str(form.get('workplace_name', '')).split())[:200]
    members, e1 = workplace.read_members(form)
    zone, e2 = workplace.read_time_zone(form)
    return setup_saved(aid, 2, ('' if name else 'Give the workplace a name.') or e1 or e2, person_name=name, members=members, time_zone=zone or None)


@app.post('/analysis/{aid}/step/3')
async def step3(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    value, error = workplace.pick(await request.form(), 'area', workplace.AREAS, 'the area it works in')
    return setup_saved(aid, 3, error, person_role=value)


@app.post('/analysis/{aid}/step/4')
async def step4(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    value, error = workplace.pick_channels(await request.form())
    return setup_saved(aid, 4, error, channel=value)


@app.post('/analysis/{aid}/step/5')
async def step5(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    form = await request.form()
    name = ' '.join(str(form.get('user_name', '')).split())[:120]
    role, error = workplace.pick(form, 'user_role', workplace.USER_ROLES, 'your role')
    return setup_saved(aid, 5, ('' if name else 'Tell us your name.') or error, user_name=name, user_role=role)


@app.post('/analysis/{aid}/step/6')
async def step6(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    goals, other, error = workplace.read_goals(await request.form())
    if error:
        return redirect(f'/analysis/{aid}/step/6', error)
    store.update_analysis(aid, goals_json=json.dumps(goals), goal_text=other, status='ready')
    return redirect(f'/analysis/{aid}/workspace')


def unit_total(units):
    """How many units a reader returned, for a message: '12,000', or '50,000+' when it stopped at its ceiling and the source may hold more."""
    return f'{len(units):,}' + ('+' if len(units) >= ingest.MAX_READ_UNITS else '')


REPEATED_FILE = 'identical to a file already imported'
LABEL_CHARS = 80                    # longest label an upload keeps: Vibe Check shows a source name at this width (see run_vibe_check)


def file_label(raw: str) -> str:
    """What an uploaded file is called in the evidence table and the reports, and the source its units are scored under: its own name,
    in any script, without a folder part (a script or an old browser may send a whole path), without control or invisible format
    characters (a bidirectional override among them), with one space for each run of white space, and cut to LABEL_CHARS with its
    extension kept. It is only shown and used to group units: the bytes are read from a randomly named temporary copy, so the name never
    has to be safe as a file name. Turning every character outside A-Z, a-z and 0-9 into '_' made labels unreadable and gave different
    files one name (any two Chinese names of the same length, 'team chat' and 'team_chat'), so they were scored as one source."""
    name = re.split(r'[\\/]', raw)[-1]
    name = ' '.join(''.join(ch for ch in name if ch.isprintable() or ch.isspace()).split())
    if len(name) > LABEL_CHARS:                                                 # cut the stem, not the extension
        suffix = Path(name).suffix
        stem, suffix = name[:len(name) - len(suffix)], suffix[:16].rstrip()     # an "extension" longer than that is no extension; this keeps room for the stem
        name = stem[:LABEL_CHARS - len(suffix)].rstrip() + suffix
    return name or 'upload'


UNREADABLE = {                                  # what a file of each kind is likely to be when its reader gives up; no full stop, upload_message adds it
    '.pdf': 'could not be read; it may be damaged, password-protected or not really a PDF',
    '.docx': 'could not be read; it may be damaged, password-protected or not really a Word (.docx) file',
    '.json': 'could not be read; it may be damaged or not really a JSON file',
    '.csv': 'could not be read; it may be damaged or not really a CSV file',
    '': 'could not be read; it may be damaged',
    '.xlsx': 'could not be read; it may be damaged, password-protected or not really an Excel file',
    '.xlsm': 'could not be read; it may be damaged, password-protected or not really an Excel file',
    '.xls': 'could not be read; it may be damaged, password-protected or not really an Excel file',
    '.ods': 'could not be read; it may be damaged or not really a spreadsheet',
    '.odt': 'could not be read; it may be damaged or not really an OpenDocument file',
    '.pptx': 'could not be read; it may be damaged, password-protected or not really a PowerPoint file',
    '.doc': 'could not be read; it may be damaged, password-protected or not really a Word file',
    '.rtf': 'could not be read; it may be damaged or not really an RTF file',
}


def safe_suffix(ext: str) -> str:
    """The extension a temporary copy of an upload is given: it decides which reader runs, so it is only ever letters and digits."""
    return ext if re.fullmatch(r'\.[a-z0-9]{1,10}', ext) else ''


ARCHIVE_MEMBERS = 200


def expanded_uploads(files):
    """The files of an upload as (name, bytes, problems): a zip archive is opened and each file inside is one more file, named 'archive.zip > file'. `bytes` is None for
    something that is left out, with the reason in `problems`. A file over the size limit comes back with one more byte than the limit, so the reader refuses it."""
    for f in files:
        raw_name = (f.filename or '').strip()
        if not raw_name:
            continue
        data = f.file.read(config.MAX_UPLOAD_BYTES + 1)
        label = file_label(raw_name)
        if Path(label).suffix.lower() != '.zip' or not data or len(data) > config.MAX_UPLOAD_BYTES:
            yield raw_name, data, []
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith('__MACOSX') and not Path(i.filename).name.startswith('.')]
        except zipfile.BadZipFile:
            yield raw_name, None, [f'{label}: the archive is damaged or is not really a zip file']
            continue
        if not infos:
            yield raw_name, None, [f'{label}: the archive has no files in it']
            continue
        if len(infos) > ARCHIVE_MEMBERS:
            yield raw_name, None, [f'{label}: only the first {ARCHIVE_MEMBERS} of {len(infos)} files in the archive were read']
        for info in infos[:ARCHIVE_MEMBERS]:
            inner = f'{label} > {Path(info.filename).name}'
            if info.flag_bits & 0x1:
                yield inner, None, [f'{inner}: the file in the archive is password protected']
            elif info.file_size > config.MAX_UPLOAD_BYTES:
                yield inner, None, [f'{inner}: larger than {config.MAX_UPLOAD_BYTES // 1_000_000} MB']
            else:
                with zf.open(info) as member:
                    yield inner, member.read(config.MAX_UPLOAD_BYTES + 1), []


def ingest_files(aid, files) -> tuple:
    """Reads uploads into communication units. Returns (units_added, problems). Files are parsed from a temporary copy that is
    deleted straight away, so nothing the user uploaded stays on disk. `problems` also names what the reader or the per-analysis
    limit left out of a file, so a file taken only in part is not reported as a plain success. A file whose bytes are those of a file
    already imported into the analysis is left out as well, or every message in it would be stored, rated and counted twice."""
    added_total, problems = 0, []
    for raw_name, data, skipped in expanded_uploads(files):
        problems.extend(skipped)
        if data is None:
            continue
        name = file_label(raw_name)
        ext = Path(name).suffix.lower()
        if not data:
            problems.append(f'{name}: the file is empty')
            continue
        if len(data) > config.MAX_UPLOAD_BYTES:
            problems.append(f'{name}: larger than {config.MAX_UPLOAD_BYTES // 1_000_000} MB')
            continue
        digest = hashlib.sha256(data).hexdigest()
        if store.has_upload(aid, digest):                                       # the usual repeat is caught here, before the file is read
            problems.append(f'{name}: {REPEATED_FILE}')
            continue
        with tempfile.NamedTemporaryFile(dir=config.upload_dir(), prefix=config.UPLOAD_PREFIX, suffix=safe_suffix(ext), delete=False) as tmp:
            tmp.write(data)
        notes = []
        try:
            if ext in voice.AUDIO_EXTENSIONS:                                   # a recording in the file tab is turned into text like one in the Voice tab
                units = ingest.units_from_text('\n'.join(voice.transcribe(tmp.name)), name, mode='lines', clean=False)
            else:
                units = ingest.units_from_file(tmp.name, label=name, clean=False, notes=notes)
        except Exception as e:
            log.warning('could not read upload %s: %s: %s', name, type(e).__name__, e)
            # An UnsupportedFileError carries a message written for the user. Any other exception is a reader's own (PdfStreamError,
            # BadZipFile, JSONDecodeError ...), whose name says nothing to the person who uploaded the file: it is logged, and the
            # person is told what is likely wrong with that kind of file.
            reason = str(e).rstrip('.') if isinstance(e, (ingest.UnsupportedFileError, voice.VoiceUnavailable)) else UNREADABLE.get(ext, UNREADABLE[''])
            problems.append(f'{name}: {reason}')
            continue
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        problems.extend(f'{name}: {note}' for note in notes)                    # what the reader left out (rows or pages beyond its ceiling)
        if not units:
            problems.append(f'{name}: no readable text found')
            continue
        added = store.add_upload(aid, name, units, digest)
        if added is None:                                                       # the same file, sent at the same moment, was stored first
            problems.append(f'{name}: {REPEATED_FILE}')
            continue
        added_total += added
        if added < len(units):                                                  # what the per-analysis limit left out
            problems.append(f'{name}: stored {added:,} of {unit_total(units)} units (limit {config.max_units():,} per analysis)')
    return added_total, problems


def upload_message(added, problems):
    parts = [f'Imported {added:,} communication unit{"" if added == 1 else "s"}.'] if added else []
    return ' '.join(parts + [('Skipped: ' if i == 0 else '') + p + '.' for i, p in enumerate(problems)]) or 'Choose a file to upload.'


# ---- workspace -------------------------------------------------------------------------
def wizard_rail(aid, steps, base, current, foot=None):
    """The left-hand list of steps: each with a state (on, done or todo), and the item below the rule."""
    items = [{'num': n, 'label': label, 'href': f'{base}/{n}', 'state': 'on' if n == current else 'done' if n < current else 'todo'} for n, label in steps]
    return {'steps': items, 'foot': foot, 'setup': base.endswith('/step')}      # the steps of setting a workplace up (once) do not carry the list of workplaces


@app.get('/search', response_class=HTMLResponse)
def search_page(request: Request, q: str = ''):
    """One box for workplaces, their reports and the messages stored for them. Everything is searched on this computer."""
    q = ' '.join(q.split())[:100]
    found_wp, found_msgs = [], []
    if q:
        low = q.lower()
        found_wp = [w for w in store.workplaces() if low in (w['person_name'] or '').lower() or low in ' '.join(str(x) for x in (w['subject_type'], w['person_role'], w['channel']) if x).lower()]
        found_msgs = store.search_messages(q)
        for m in found_msgs:
            at = m['text'].lower().find(low)
            start = max(0, at - 60)
            m['snippet'] = ('…' if start else '') + m['text'][start:start + 220] + ('…' if len(m['text']) > start + 220 else '')
    return render(request, 'search.html', q=q, search_q=q, found_wp=found_wp, found_msgs=found_msgs)


@app.get('/go')
def go(to: str = ''):
    """The workplace picker of the top bar without JavaScript: a workplace id opens its page, anything else opens the comparison of all of them."""
    if to.isdigit() and any(str(w['id']) == to for w in store.workplace_names()):
        return RedirectResponse(f'/analysis/{int(to)}/workspace', status_code=303)
    return RedirectResponse('/dashboard#compare', status_code=303)


def engine_choices():
    """The engines that can be a default: [(value, label, group)]. Only what this app can run now is offered."""
    out = [('', 'Choose for me', 'Automatic')]
    if cloud.login_offered():
        out += [(f'claudecode:{m}', f'{name} · {about}', 'Your Claude login') for m, name, about, _s in cloud.LOGIN_MODELS]
    if config.local_model_enabled():
        out.append((config.model_name(), f'{config.model_name()} · runs on this computer', 'Felixa engine'))
    out.append(('rules:words', 'Word rules · no AI, nothing leaves this computer', 'Other engines'))
    if cloud.available():
        out += [(f'claude:{m}', f'{name} · {about}', 'Anthropic API') for m, name, about, _s in cloud.CLAUDE_MODELS]
    if cloud.openai_available():
        out += [(f'openai:{m}', f'{name} · {about}', 'OpenAI API') for m, name, about, _s in cloud.OPENAI_MODELS]
    return out


@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request):
    choices = engine_choices()
    current = store.setting('default_engine')
    if current and current not in {v for v, _l, _g in choices}:
        current = ''
    return render(request, 'settings.html', rail={'steps': [], 'foot': None, 'setup': False}, engine_choices=choices, default_engine=current,
                  default_sensitivity=store.setting('default_sensitivity') if store.setting('default_sensitivity') in store.SENSITIVITIES else 'medium',
                  default_incognito=store.setting('default_incognito') == '1', sens_options=[(k, SENSITIVITY_LABELS[k], workplace.SENSITIVITY_HELP[k]) for k in pipeline.SENSITIVITIES],
                  version=config.VERSION, data_folder=str(config.data_dir()), workplace_count=len(store.workplaces()), claude_cli=bool(cloud.cli_path()),
                  login_offered=cloud.login_offered(), login_ready=cloud.login_available(), local_enabled=config.local_model_enabled(), hosted=bool(os.getenv('FELIXA_PASSWORD')))


@app.post('/update')
def update_now():
    """Installs the newest version from GitHub over this one and starts again on the same address."""
    if not updater.available():
        return redirect('/settings', 'This copy of Felixa updates itself only when it was opened with the Felixa app on a Mac.')
    try:
        version = updater.install()
    except Exception as e:
        return redirect('/settings', f'The update did not work: {e}. Nothing was changed.')
    updater.restart_soon()
    return redirect('/settings', f'Updated to {version}. Felixa is starting again: reload this page in a few seconds.')


@app.post('/settings')
async def settings_save(request: Request):
    form = await request.form()
    engine = str(form.get('default_engine', '')).strip()
    if engine not in {v for v, _l, _g in engine_choices()}:
        return redirect('/settings', 'Choose one of the engines in the list.')
    sens = str(form.get('default_sensitivity', 'medium'))
    if sens not in store.SENSITIVITIES:
        return redirect('/settings', 'Choose how strict the check should be.')
    store.set_setting('default_engine', engine)
    store.set_setting('default_sensitivity', sens)
    store.set_setting('default_incognito', '1' if form.get('default_incognito') else '0')
    return redirect('/settings', 'Settings saved. New workplaces and new checks start with them.')


@app.post('/settings/delete-all')
async def settings_delete_all(request: Request):
    form = await request.form()
    if str(form.get('confirm', '')).strip().lower() != 'delete everything':
        return redirect('/settings', 'Nothing was deleted. Type delete everything in the box to confirm.')
    done, left = store.delete_everything()
    return redirect('/settings', f'Deleted {done} workplace{"" if done == 1 else "s"} with everything saved for them.' + (f' {left} could not be deleted while a task was running for it. Try again when it finishes.' if left else ''))


@app.get('/dashboard', response_class=HTMLResponse)
def results_dashboard(request: Request):
    ctx = dashboard.build(request.query_params)
    ctx['behaviour_keys'] = dashboard.BEHAVIOUR
    ctx['assets'] = vibe_check.assets_html()
    ctx['rail'] = {'steps': [], 'foot': None, 'setup': False}
    ctx['detail_ctx'] = None
    detail = ctx['detail']
    if detail:                                                       # one workplace: its latest alerts, badges and signal profile come from its own report
        full = report.build(detail['aid'])
        ms = full and full.get('measures')
        if ms:
            ctx['detail_ctx'] = {'alerts': [x for x in ms['alerts'] if x['level'] != 'good'][:8], 'levels': {k: measures.LEVEL_NAMES[k] for k in measures.LEVELS},
                                 'badges': [b['name'] for b in ms['badges'] if b['earned']], 'signals': full.get('chart_html', '')}
    return render(request, 'dashboard.html', **ctx)


def read_setup(form):
    """Every answer of the workplace form at once, for the edit page: (fields to save, the list of things that are wrong)."""
    env, e_env = workplace.pick(form, 'environment', ENV_NAMES, 'what kind of place this is')
    name = ' '.join(str(form.get('workplace_name', '')).split())[:200]
    members, e_members = workplace.read_members(form)
    zone, e_zone = workplace.read_time_zone(form)
    area, e_area = workplace.pick(form, 'area', workplace.AREAS, 'the area it works in')
    channel, e_channel = workplace.pick_channels(form)
    user = ' '.join(str(form.get('user_name', '')).split())[:120]
    role, e_role = workplace.pick(form, 'user_role', workplace.USER_ROLES, 'your role')
    goals, other, e_goals = workplace.read_goals(form)
    errors = [e for e in (e_env, '' if name else 'Give the workplace a name.', e_members, e_zone, e_area, e_channel, '' if user else 'Tell us your name.', e_role, e_goals) if e]
    fields = {'subject_type': env, 'person_name': name, 'members': members, 'time_zone': zone or None, 'person_role': area, 'channel': channel, 'user_name': user,
              'user_role': role, 'goals_json': json.dumps(goals), 'goal_text': other}
    return fields, errors


@app.get('/analysis/{aid}/edit')
def edit_get(aid: AnalysisId):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    return redirect(f'/analysis/{aid}/workspace#edit' if a['status'] != 'draft' else f'/analysis/{aid}/step/1')      # edited in place on the workplace page


@app.post('/analysis/{aid}/edit')
async def edit_post(request: Request, aid: AnalysisId):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    if store.active_job(aid):
        return redirect(f'/analysis/{aid}/workspace', BUSY_SETTINGS)
    form = await request.form()
    fields, errors = read_setup(form)
    if errors:                                                           # the page comes back with what was typed, so nothing has to be typed again
        typed = {'members': str(form.get('members', ''))[:12], 'time_zone': str(form.get('time_zone', ''))[:60]}
        return workspace_page(request, aid, {**dict(a), **fields, **typed}, errors=errors, msg=errors[0], edit_open=True)
    store.update_analysis(aid, **fields)
    return redirect(f'/analysis/{aid}/workspace', 'Workplace saved.')


@app.post('/analysis/{aid}/members')
async def members_post(request: Request, aid: AnalysisId):
    """The number of members as it is now, set when a check starts: each check keeps the number it was made with, so the trend can follow the group."""
    if not store.get_analysis(aid):
        return not_found()
    if store.active_job(aid):
        return redirect(f'/analysis/{aid}/workspace', BUSY_SETTINGS)
    members, error = workplace.read_members(await request.form())
    if error:
        return redirect(f'/analysis/{aid}/check/1', error)
    store.update_analysis(aid, members=members)
    return redirect(f'/analysis/{aid}/check/1', 'Members saved.' if members else 'Members cleared.')


@app.post('/analysis/{aid}/delete')
def delete_workplace(aid: AnalysisId):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    if not store.delete_analysis(aid):
        return redirect(f'/analysis/{aid}/workspace#edit', 'Wait for the current task to finish, or cancel it, before deleting this workplace.')
    return redirect('/', f'Deleted {a["person_name"] or "the workplace"}, with its messages, results and the numbers of its earlier checks.')


@app.get('/analysis/{aid}/workspace', response_class=HTMLResponse)
def workspace(request: Request, aid: AnalysisId):
    return workspace_page(request, aid)


def workspace_page(request, aid, shown=None, **extra):
    """The workplace page. `shown` holds what the edit form shows when it is not the saved answers (what was typed, when something was wrong)."""
    ctx = report.build(aid)
    if not ctx:
        return not_found()
    a = ctx['a']
    job = store.latest_job(aid)
    form = setup_context(shown if shown is not None else a, 0)
    form['form_goals'] = form.pop('goals')                      # the report's own `goals` is another list
    form['fv'] = shown if shown is not None else a                # what the form shows: the saved answers, or what was typed when something was wrong
    form.pop('rail', None)
    form.pop('summary_rows', None)
    form.pop('summary_title', None)
    form.pop('a', None)
    goals = [g for g in json.loads(a['goals_json'] or '[]') if g] + ([a['goal_text']] if a['goal_text'] else [])
    when = ''
    try:
        when = datetime.datetime.fromisoformat(str(job['created_at'])[:19]).strftime('%b %-d, %Y') if job and job['created_at'] else ''
    except ValueError:
        when = ''
    return render(request, 'workspace.html', **ctx, **form, goal_items=goals, last_when=when, local_model_ready=local_model_ready(), job=job, job_progress=progress_of(job, bool(a['incognito'])), model=config.model_name(),
                  local_enabled=config.local_model_enabled(), claude_ready=cloud.available() or cloud.login_available(),
                  summary_title='Your workplace', summary_rows=setup_rows(a), **extra)


# ---- a new check ----------------------------------------------------------------------------
CHECK_LAST = len(workplace.CHECK_STEPS)


def check_destination(aid, back):
    """Where a form that adds messages sends the person afterwards: back into the New check, or to the workplace page."""
    return f'/analysis/{aid}/check/1' if back == 'check' else f'/analysis/{aid}/analytics' if back == 'analytics' else f'/analysis/{aid}/workspace'


def bound_label(bound):
    """A review-period bound for the summary panel: a day, or a minute in UTC."""
    return f'{bound:%Y-%m-%d %H:%M} UTC' if isinstance(bound, datetime.datetime) else bound.isoformat()


def check_rows(a, units_n, in_scope=None, sources=1, avg_chars=None):
    volume = a['check_volume']
    period = store.period_of(a)
    span = ' to '.join(bound_label(d) if d else 'any date' for d in period) if any(period) else 'All dates'
    return [('upload', 'Messages', f'{units_n:,} added' if units_n else ''),
            ('layers', 'Volume', '' if volume is None else 'All of them' if volume == 0 else f'{volume:,} messages'),
            ('lock', 'Incognito', 'On: names removed' if a['incognito'] else 'Off'),
            ('calendar', 'Time frame', span), ('gauge', 'Sensitivity', SENSITIVITY_LABELS.get(a['vibe_sensitivity'] or 'medium', '')),
            ('target', 'Price', price_label(a['engine'], units_n if in_scope is None else in_scope, volume, sources, avg_chars)), ('cpu', 'Engine', engine_label(a['engine']) if a['engine'] else '')]


def price_label(engine, units_n, volume, sources=1, avg_chars=None) -> str:
    """What one check on this engine costs, for the summary: 'Free', 'About $0.12', or 'Included in your Claude plan'. Empty while no engine is chosen."""
    if not engine:
        return ''
    if not prices.per_million(engine):
        return 'Free'
    text = prices.text(engine, workplace.calls_for(units_n, volume if volume is not None else workplace.DEFAULT_VOLUME, sources), avg_chars)
    if not text:
        return 'Add messages to see it'
    return 'Included in your Claude plan' if cloud.is_login(engine) else text


def engine_label(engine) -> str:
    return 'Word rules (no AI)' if rules.is_rules(engine) else cloud.label(engine)


def check_context(a, step):
    aid = a['id']
    period = store.period_coverage(a)
    units_n = store.count_units(aid)
    in_scope = period['kept'] if period else units_n
    avg_chars = store.average_chars(aid, in_period=bool(period))
    evidence = store.evidence_by_source(aid)
    volume = a['check_volume'] if a['check_volume'] is not None else workplace.DEFAULT_VOLUME
    stamps = sorted(x for x in (parse_ts(e[k]) for e in evidence for k in ('first_ts', 'last_ts') if e[k]) if x)
    timed = any(x.time() != datetime.time.min for x in stamps)
    label = lambda x: f'{x:%Y-%m-%d %H:%M}' if timed else f'{x:%Y-%m-%d}'      # noqa: E731
    ctx = {'a': a, 'step': step, 'steps': workplace.CHECK_STEPS, 'last': CHECK_LAST, 'units_n': units_n, 'in_scope': in_scope, 'evidence': evidence,
           'volumes': [v for v in workplace.VOLUMES if v < units_n], 'volume': volume, 'volume_min': workplace.VOLUME_MIN, 'volume_max': config.max_units(),
           'custom': a['check_volume'] if a['check_volume'] and a['check_volume'] not in workplace.VOLUMES else '', 'discord_url': workplace.DISCORD_BOT_URL, 'incognito': bool(a['incognito']),
           'dates': (label(stamps[0]), label(stamps[-1])) if stamps else None, 'period_form': period_form(a), 'presets': [(k, l) for k, l, _d in PRESETS], 'period_text': report.period_text(period),
           'sens_options': [(k, SENSITIVITY_LABELS[k], workplace.SENSITIVITY_HELP[k]) for k in pipeline.SENSITIVITIES],
           'sensitivity': a['vibe_sensitivity'] or 'medium', 'extensions': ', '.join(config.UPLOAD_EXTENSIONS),
           'voice_ok': voice.available(), 'audio': ', '.join(voice.AUDIO_EXTENSIONS), 'discord': 'Discord' in workplace.split_channels(a['channel'])[0], 'channel_tips': workplace.channel_tips(a['channel']),
           'rail': wizard_rail(aid, workplace.CHECK_STEPS, f'/analysis/{aid}/check', step,
                               foot={'icon': 'eye', 'label': 'Workplace', 'href': f'/analysis/{aid}/workspace'}),
           'summary_title': 'Your check', 'summary_rows': check_rows(a, units_n, in_scope, max(1, len(evidence)), avg_chars), 'busy': bool(store.active_job(aid))}
    if step == CHECK_LAST:
        local = config.local_model_enabled()
        models = local_models() if local else []
        example = not in_scope                                                              # no messages yet: show the time and price of a typical check
        est_n = in_scope or (volume or workplace.DEFAULT_VOLUME)
        ctx['estimate_example'], ctx['estimate_n'] = example, est_n
        ctx['engines'] = workplace.engine_options(models, config.model_name(), est_n, volume, max(1, len(evidence)), cloud.CLAUDE_MODELS, cloud.available(),
                                                  cloud.LOGIN_MODELS if cloud.login_offered() else (), cloud.login_available(), cloud.OPENAI_MODELS, cloud.openai_available(), rules=True, avg_chars=avg_chars)
        if not local:
            ctx['engines'] = [e for e in ctx['engines'] if e['kind'] != 'local']
        ctx['local_enabled'] = local
        installed = any(m['name'] in (config.model_name(), config.model_name() + ':latest') for m in models)
        if a['engine']:
            ctx['engine'] = a['engine']
        elif store.setting('default_engine') in {v for v, _l, _g in engine_choices() if v}:    # the engine chosen on the Settings page
            ctx['engine'] = store.setting('default_engine')
        elif local and installed and a['model_used']:                                       # a check that ran with no engine saved ran on the model on this computer
            ctx['engine'] = config.model_name()
        elif cloud.login_available():                                                       # otherwise your Claude login first when it can run, then the model on this computer
            ctx['engine'] = 'claudecode:' + cloud.LOGIN_MODELS[0][0]
        elif local and installed:
            ctx['engine'] = config.model_name()
        elif cloud.available():
            ctx['engine'] = 'claude:' + cloud.CLAUDE_MODELS[0][0]
        else:
            ctx['engine'] = config.model_name() if local else 'claude:' + cloud.CLAUDE_MODELS[0][0]
        ctx['model_server_up'] = bool(models) if local else True
        ctx['price_note'] = prices.as_of_note(openai=True)
        ctx['cloud_ready'] = cloud.available()
        ctx['openai_ready'] = cloud.openai_available()
        ctx['login_offered'] = cloud.login_offered()
        ctx['login_ready'] = cloud.login_available()
        ctx['cloud_ok'] = bool(a['cloud_ok'])
        ctx['summary_rows'] = ctx['summary_rows'][:-2] + [('target', 'Price', price_label(ctx['engine'], in_scope or ctx['estimate_n'], a['check_volume'], max(1, len(evidence)), avg_chars)), ('cpu', 'Engine', engine_label(ctx['engine']))]          # what is selected now is what will run
    return ctx


@app.post('/analysis/{aid}/check/new')
def check_new(aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    if store.active_job(aid):
        return redirect(f'/analysis/{aid}/workspace', 'Wait for the current task to finish before starting a new check.')
    store.new_check(aid)
    return redirect(f'/analysis/{aid}/analytics')


@app.post('/analysis/{aid}/source/remove')
async def source_remove(aid: AnalysisId, request: Request):
    """Removes one added file, recording or pasted text from the analysis."""
    if not store.get_analysis(aid):
        return not_found()
    form = await request.form()
    where = f'/analysis/{aid}/workspace' if form.get('back') == 'workspace' else f'/analysis/{aid}/analytics'
    if store.active_job(aid):
        return redirect(where, 'Wait for the current task to finish before removing messages.')
    label, kind = str(form.get('label', '')), str(form.get('kind', ''))
    n = store.remove_source(aid, label, kind)
    if not n:
        return redirect(where, 'That source was not found. It may already be removed.')
    return redirect(where, f'Removed {label}: {n:,} message{"" if n == 1 else "s"}. The results made from it were cleared; run the analysis again.')


@app.get('/analysis/{aid}/analytics', response_class=HTMLResponse)
def analytics_page(request: Request, aid: AnalysisId):
    """Setting up a new analysis on one page: the six choices of the check in a column, then Run Analysis."""
    a = store.get_analysis(aid)
    if not a or a['status'] == 'draft':
        return not_found()
    ctx = check_context(a, CHECK_LAST)
    volume_now = 'all' if a['check_volume'] == 0 else str(a['check_volume']) if a['check_volume'] else ('all' if ctx['volume'] >= ctx['units_n'] and ctx['units_n'] else str(ctx['volume']))
    ctx['volume_now'] = volume_now
    ctx['volume_options'] = [(str(v), f'{v:,} messages') for v in ctx['volumes']] + [('all', f'All messages ({ctx["units_n"]:,})' if ctx['units_n'] else 'All messages')]
    if ctx['custom']:
        ctx['volume_options'].insert(0, (str(ctx['custom']), f'{int(ctx["custom"]):,} messages'))
        ctx['volume_now'] = str(ctx['custom'])
    start, end = a['period_start'] or '', a['period_end'] or ''
    now, span = 'all', store.timestamp_span(aid)
    if start or end:
        now = 'custom'
        for key, _l, delta in PRESETS:
            if span and (start, end) == ((span[1] - delta).strftime('%Y-%m-%dT%H:%M'), span[1].strftime('%Y-%m-%dT%H:%M')):
                now = key
    ctx['period_now'] = now
    ctx['period_options'] = list(ANALYTICS_PERIODS) + ([('custom', 'Custom dates: ' + (ctx['period_text'] or 'saved'))] if ctx['period_now'] == 'custom' else [])
    ctx['busy'] = bool(store.active_job(aid))
    return render(request, 'analytics.html', **ctx)


@app.post('/analysis/{aid}/analytics')
async def analytics_run(request: Request, aid: AnalysisId):
    """Run Analysis: saves every choice of the page, then starts the check like the last step of the old flow."""
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    here = f'/analysis/{aid}/analytics'
    if store.active_job(aid):
        return redirect(f'/analysis/{aid}/workspace', BUSY_SETTINGS)
    form = await request.form()
    fields = {}
    choice = str(form.get('incognito', 'off'))
    fields['incognito'] = int(choice == 'on')
    sens = str(form.get('vibe_sensitivity', ''))
    if sens not in pipeline.SENSITIVITIES:
        return redirect(here, 'Choose how sensitive the check should be.')
    fields['vibe_sensitivity'] = sens
    vol = str(form.get('volume', ''))
    typed = str(form.get('volume_custom', '')).strip().replace(',', '')
    if typed:
        top = config.max_units()
        if not (typed.isdigit() and workplace.VOLUME_MIN <= int(typed) <= top):
            return redirect(here, f'Type a whole number of messages from {workplace.VOLUME_MIN} to {top:,}.')
        fields['check_volume'] = int(typed)
    elif vol == 'all':
        fields['check_volume'] = 0
    elif vol.isdigit() and workplace.VOLUME_MIN <= int(vol) <= config.max_units():
        fields['check_volume'] = int(vol)
    else:
        return redirect(here, 'Choose how many messages to read.')
    period = str(form.get('period', 'all'))
    day_from, day_to = str(form.get('period_start', '')).strip(), str(form.get('period_end', '')).strip()
    if day_from or day_to:
        try:
            d1, d2 = store.parse_day(day_from), store.parse_day(day_to)
        except ValueError:
            return redirect(here, 'Enter the start and the end as dates, or leave them blank.')
        start, end = utc_bound(d1, None, 0, False), utc_bound(d2, None, 0, True)
        if start and end and store.period_of({'period_start': start, 'period_end': end}) == (None, None):
            return redirect(here, 'The end cannot be before the start.')
        fields.update(period_start=start, period_end=end, period_tz=0)
    elif period == 'all':
        fields.update(period_start='', period_end='', period_tz=None)
    elif period != 'custom':
        chosen = next((delta for key, _l, delta in PRESETS if key == period), None)
        span = store.timestamp_span(aid)
        if chosen is None or span is None:
            return redirect(here, 'None of your messages has a readable date, so that time frame cannot be worked out. Choose all dates.')
        stamp = '%Y-%m-%dT%H:%M'
        fields.update(period_start=(span[1] - chosen).strftime(stamp), period_end=span[1].strftime(stamp), period_tz=0)
    engine = str(form.get('engine', '')).strip()
    sent = False
    if rules.is_rules(engine):
        pass
    elif cloud.is_cloud(engine):
        if not cloud.known(engine):
            return redirect(here, 'Choose an engine from the list.')
        if not cloud.available(engine):
            return redirect(here, 'Claude Code is not available here. Install it and sign in, or choose another engine.' if cloud.is_login(engine)
                            else f'{cloud.provider(engine)} is not set up on this server. Choose another engine.')
        if form.get('cloud_ok') != '1':
            return redirect(here, f'Tick the box to confirm that your messages may be sent to {cloud.provider(engine)} to be read.')
        sent = True
    else:
        if not config.local_model_enabled():
            return redirect(here, 'This app runs without a model of its own. Choose a Claude engine.')
        installed = [m['name'] for m in local_models()]
        if engine and engine != config.model_name() and engine not in installed:
            return redirect(here, 'That engine is not downloaded on this computer. Choose one from the list.')
    store.update_analysis(aid, engine=engine if engine and engine != config.model_name() else None, cloud_ok=int(sent), **fields)
    return start_job(aid, 'full')


@app.get('/analysis/{aid}/check/{step}', response_class=HTMLResponse)
def check_get(request: Request, aid: AnalysisId, step: int):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    if step == 1:                                                       # adding messages is part of the Analytics page now
        return redirect(f'/analysis/{aid}/analytics')
    if not 1 <= step <= CHECK_LAST:
        raise PageError(404, 'Step not found', f'There is no step {step}. A new check has steps 1 to {CHECK_LAST}.', back=(f'/analysis/{aid}/check/1', 'Go to step 1'))
    return render(request, 'check.html', **check_context(a, step))


BUSY_SETTINGS = 'Wait for the current task to finish before changing the check settings.'


def check_saved(aid, step, error='', **fields):
    if store.active_job(aid):                                     # a running task has already read its settings: a change now would say one thing and do another
        return redirect(f'/analysis/{aid}/workspace', BUSY_SETTINGS)
    if error:
        return redirect(f'/analysis/{aid}/check/{step}', error)
    store.update_analysis(aid, **fields)
    return redirect(f'/analysis/{aid}/check/{step + 1}')


@app.post('/analysis/{aid}/check/2')
async def check_volume(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    form = await request.form()
    choice = str(form.get('volume', ''))
    if choice == 'all':
        return check_saved(aid, 2, check_volume=0)
    if choice.isdigit() and int(choice) in workplace.VOLUMES:
        return check_saved(aid, 2, check_volume=int(choice))
    if choice == 'custom':
        typed = str(form.get('volume_custom', '')).strip().replace(',', '')
        top = config.max_units()
        if typed.isdigit() and workplace.VOLUME_MIN <= int(typed) <= top:
            return check_saved(aid, 2, check_volume=int(typed))
        return check_saved(aid, 2, f'Type a whole number from {workplace.VOLUME_MIN} to {top:,}.')
    return check_saved(aid, 2, 'Choose how many messages to read.')


@app.post('/analysis/{aid}/check/3')
async def check_incognito(request: Request, aid: AnalysisId):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    choice = str((await request.form()).get('incognito', ''))
    saved = check_saved(aid, 3, '' if choice in ('on', 'off') else 'Choose on or off.', incognito=int(choice == 'on'))
    turned_on = choice == 'on' and not a['incognito']
    if turned_on and saved.headers['location'] == f'/analysis/{aid}/check/4' and report.has_results(analysis.metrics_for(aid), store.latest_vibe(aid)):
        return redirect(f'/analysis/{aid}/check/4', 'Incognito saved. Run the check again to take names out of the results you already have.')      # names go out when a check runs
    return saved


CLOCK = re.compile(r'([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?')


def clock_of(value):
    """A time typed as HH:MM (a browser's time box), or None when blank. Raises ValueError for anything else."""
    text = str(value or '').strip()
    if not text:
        return None
    found = CLOCK.fullmatch(text)
    if not found:
        raise ValueError(text)
    return datetime.time(int(found.group(1)), int(found.group(2)))


def utc_bound(day, clock, offset, end):
    """One end of the review period as it is saved. `offset` is the browser's: minutes to add to local time to get UTC. A whole day typed in
    UTC stays a plain date; a time, or a day in another zone, becomes a minute in UTC."""
    if day is None:
        return ''
    if clock is None and not offset:
        return day.isoformat()
    local = datetime.datetime.combine(day, clock or (datetime.time(23, 59) if end else datetime.time.min))
    return (local + datetime.timedelta(minutes=offset)).strftime('%Y-%m-%dT%H:%M')


def period_form(a):
    """The saved period as the form shows it: the person's own day and time, from the zone it was typed in."""
    offset = a['period_tz'] or 0
    out = {}
    for key in ('start', 'end'):
        try:
            bound = store.parse_bound(a['period_' + key])
        except ValueError:
            bound = None
        if isinstance(bound, datetime.datetime):
            local = bound - datetime.timedelta(minutes=offset)
            out[key] = (local.date().isoformat(), f'{local:%H:%M}')
        else:
            out[key] = (bound.isoformat() if bound else '', '')
    return out


PRESETS = (('24h', 'Last 24 hours', datetime.timedelta(hours=24)), ('7d', 'Last 7 days', datetime.timedelta(days=7)),
           ('30d', 'Last 30 days', datetime.timedelta(days=30)), ('90d', 'Last 90 days', datetime.timedelta(days=90)))


ANALYTICS_PERIODS = (('all', 'All dates'),) + tuple((k, l) for k, l, _d in PRESETS)


@app.post('/analysis/{aid}/check/4')
async def check_period(request: Request, aid: AnalysisId):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    form = await request.form()
    if form.get('all_dates'):
        return check_saved(aid, 4, period_start='', period_end='', period_tz=None)
    offset = str(form.get('tz_offset', '0')).strip()
    offset = int(offset) if re.fullmatch(r'-?\d{1,4}', offset) and abs(int(offset)) <= 840 else 0
    chosen = next((delta for key, _label, delta in PRESETS if key == form.get('preset')), None)
    if chosen is not None:                                                                 # a quick choice counts back from the newest message, not from today
        span = store.timestamp_span(aid)
        if span is None:
            return check_saved(aid, 4, 'None of your messages has a readable date, so a quick choice cannot be worked out. Choose all dates.')
        stamp = '%Y-%m-%dT%H:%M'
        start, end = (span[1] - chosen).strftime(stamp), span[1].strftime(stamp)
    else:
        try:
            start_day, end_day = store.parse_day(form.get('period_start')), store.parse_day(form.get('period_end'))
        except ValueError:
            return check_saved(aid, 4, 'Enter the start and the end as dates, or choose all dates.')
        try:
            start_clock, end_clock = clock_of(form.get('period_start_time')), clock_of(form.get('period_end_time'))
        except ValueError:
            return check_saved(aid, 4, 'Enter each time as hours and minutes, for example 20:30, or leave it blank.')
        if (start_clock and not start_day) or (end_clock and not end_day):
            return check_saved(aid, 4, 'A time needs a date: choose the date it belongs to.')
        start, end = utc_bound(start_day, start_clock, offset, False), utc_bound(end_day, end_clock, offset, True)
        if start and end and store.period_of({'period_start': start, 'period_end': end}) == (None, None):
            return check_saved(aid, 4, 'The end cannot be before the start.')
    if store.active_job(aid):
        return redirect(f'/analysis/{aid}/workspace', BUSY_SETTINGS)
    changed = store.period_of(a) != store.period_of({'period_start': start, 'period_end': end})
    store.update_analysis(aid, period_start=start, period_end=end, period_tz=offset if start or end else None)
    if changed and report.has_results(analysis.metrics_for(aid), store.latest_vibe(aid)):
        return redirect(f'/analysis/{aid}/check/5', 'Time frame saved. Run the check again to apply it to the results you already have.')
    return redirect(f'/analysis/{aid}/check/5')


@app.post('/analysis/{aid}/check/5')
async def check_sensitivity(request: Request, aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    choice = str((await request.form()).get('vibe_sensitivity', ''))
    return check_saved(aid, 5, '' if choice in pipeline.SENSITIVITIES else 'Choose how sensitive the check should be.', vibe_sensitivity=choice)


@app.post('/analysis/{aid}/check/6')
async def check_run(request: Request, aid: AnalysisId):
    """The last step: saves the engine, fixes the volume if none was chosen, and starts the check."""
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    form = await request.form()
    engine = str(form.get('engine', '')).strip()
    sent = False
    if rules.is_rules(engine):
        pass                                                                       # word lists: no model to download, nothing to send
    elif cloud.is_cloud(engine):
        if not cloud.known(engine):
            return redirect(f'/analysis/{aid}/check/6', 'Choose an engine from the list.')
        if not cloud.available(engine):
            return redirect(f'/analysis/{aid}/check/6', 'Claude Code is not available here. Install it and sign in, or choose another engine.' if cloud.is_login(engine)
                            else f'{cloud.provider(engine)} is not set up on this server. Choose another engine.')
        if form.get('cloud_ok') != '1':
            return redirect(f'/analysis/{aid}/check/6', f'Tick the box to confirm that your messages may be sent to {cloud.provider(engine)} to be read.')
        sent = True
    else:
        if not config.local_model_enabled():
            return redirect(f'/analysis/{aid}/check/6', 'This app runs without a model of its own. Choose a Claude engine.')
        installed = [m['name'] for m in local_models()]
        if engine and engine != config.model_name() and engine not in installed:
            return redirect(f'/analysis/{aid}/check/6', 'That engine is not downloaded on this computer. Choose one from the list.')
    store.update_analysis(aid, engine=engine if engine and engine != config.model_name() else None, cloud_ok=int(sent),
                          **({'check_volume': workplace.DEFAULT_VOLUME} if a['check_volume'] is None else {}))
    return start_job(aid, 'full')


@app.post('/analysis/{aid}/check/6/test-login')
def check_test_login(aid: AnalysisId):
    """One tiny question to Claude Code, so a person can see at once whether their login works. No message of theirs is sent."""
    if not store.get_analysis(aid):
        return not_found()
    if not cloud.login_available():
        return redirect(f'/analysis/{aid}/check/6', 'Claude Code was not found on this computer, or this app is password-protected.')
    ok, said = cloud.login_test()
    return redirect(f'/analysis/{aid}/check/6', said)


@app.post('/analysis/{aid}/add-link')
def add_link(aid: AnalysisId, links: str = Form(''), back: str = Form('')):
    """Reads the text of public web pages the person typed in, one link per line."""
    if not store.get_analysis(aid):
        return not_found()
    where = check_destination(aid, back)
    if store.active_job(aid):
        return redirect(where, 'Wait for the current task to finish before editing evidence.')
    urls = [x.strip() for x in links.splitlines() if x.strip()][:linkfetch.MAX_LINKS]
    if not urls:
        return redirect(where, 'Paste a web address first.')
    added, problems = 0, []
    for url in urls:
        shown = url if len(url) <= 60 else url[:57] + '…'
        try:
            host, lines = linkfetch.fetch_lines(url)
        except linkfetch.LinkError as e:
            problems.append(f'{shown}: {e}')
            continue
        units = ingest.units_from_text('\n'.join(lines), host, mode='lines', clean=False)
        stored = store.add_upload(aid, host, units, hashlib.sha256(('link:' + url).encode()).hexdigest()) if units else 0
        if stored is None:
            problems.append(f'{shown}: {REPEATED_FILE.replace("file", "link")}')
        else:
            added += stored
    return redirect(where, upload_message(added, problems))


@app.post('/analysis/{aid}/add-voice')
def add_voice(aid: AnalysisId, files: List[UploadFile] = File(default=[]), back: str = Form('')):
    """Turns recordings into text on this computer, then stores the text like any other message."""
    if not store.get_analysis(aid):
        return not_found()
    where = check_destination(aid, back)
    if store.active_job(aid):
        return redirect(where, 'Wait for the current task to finish before editing evidence.')
    added, problems = 0, []
    for f in files:
        if not (f.filename or '').strip():
            continue
        name = file_label(f.filename)
        ext = Path(name).suffix.lower()
        if ext not in voice.AUDIO_EXTENSIONS:
            problems.append(f'{name}: unsupported type (use {", ".join(voice.AUDIO_EXTENSIONS)})')
            continue
        data = f.file.read(config.MAX_UPLOAD_BYTES + 1)
        if not data or len(data) > config.MAX_UPLOAD_BYTES:
            problems.append(f'{name}: ' + ('the file is empty' if not data else f'larger than {config.MAX_UPLOAD_BYTES // 1_000_000} MB'))
            continue
        digest = hashlib.sha256(data).hexdigest()
        if store.has_upload(aid, digest):
            problems.append(f'{name}: {REPEATED_FILE}')
            continue
        with tempfile.NamedTemporaryFile(dir=config.upload_dir(), prefix=config.UPLOAD_PREFIX, suffix=ext, delete=False) as tmp:
            tmp.write(data)
        try:
            sentences = voice.transcribe(tmp.name)
        except voice.VoiceUnavailable as e:
            Path(tmp.name).unlink(missing_ok=True)
            return redirect(where, str(e))
        except Exception as e:
            log.warning('could not transcribe %s: %s: %s', name, type(e).__name__, e)
            problems.append(f'{name}: could not be turned into text; it may be damaged or not really audio')
            continue
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        units = ingest.units_from_text('\n'.join(sentences), name, mode='lines', clean=False)
        if not units:
            problems.append(f'{name}: no speech was found')
            continue
        stored = store.add_upload(aid, name, units, digest)
        added += stored or 0
    return redirect(where, upload_message(added, problems))


async def read_paste(request: Request):
    """Reads the paste form: (text, role), or None when the post is larger than a file may be (MAX_UPLOAD_BYTES). Starlette refuses one
    form field over 1 MB with a bare JSON 400 (a browser's ordinary post included, from version 1.4), which cost the user the page and
    the paste; so the parser is given the file limit, and a post that declares more is refused here for the route to answer with a note.
    A post with no declared length (chunked, so not a browser's) is left to the parser, which refuses a field over the same limit.
    The form is read by hand because FastAPI reads Form(...) parameters with the default limit before any dependency runs, and in a
    dependency so that add_text stays a plain `def` and its database work runs in a worker thread."""
    limit = config.MAX_UPLOAD_BYTES
    declared = request.headers.get('content-length', '')
    if declared.isdecimal() and int(declared) > limit:                              # a post within the limit holds no field the parser would refuse
        return None
    async with request.form(max_part_size=limit) as form:
        text, role, back = form.get('raw_text'), form.get('actor_role'), form.get('back')
    return ((text if isinstance(text, str) else ''), (role if isinstance(role, str) and role else 'SUBJECT'),   # an empty role is no role, as with Form(...)
            back if isinstance(back, str) else '')


@app.post('/analysis/{aid}/add-text')
def add_text(aid: AnalysisId, paste=Depends(read_paste)):
    if not store.get_analysis(aid):
        return not_found()
    if paste is None:
        return redirect(f'/analysis/{aid}/workspace', 'That paste is too large. Save it as a .txt file and add it with File.')
    raw_text, actor_role, back = paste
    where = check_destination(aid, back)
    if actor_role not in ('SUBJECT', 'AUDIENCE'):
        return HTMLResponse('Choose whose words these are.', status_code=400)
    if store.active_job(aid):
        return redirect(where, 'Wait for the current task to finish before editing evidence.')
    units = ingest.units_from_text(raw_text, 'Pasted text', role=actor_role, mode='lines', clean=False)
    if not units:
        return redirect(where, 'Paste at least one communication unit.')
    added = store.add_units(aid, units, 'PASTE')
    limit = config.max_units()
    if not added:
        message = f'Nothing was added: this analysis already holds its limit of {limit:,} communication units.'
    elif added < len(units):
        message = f'Added {added:,} of {unit_total(units)} communication units (limit {limit:,} per analysis).'
    else:
        message = f'Added {added:,} communication unit{"" if added == 1 else "s"}.'
    cut = f' Only the first {ingest.MAX_TEXT_CHARS:,} characters were read.' if len(raw_text) > ingest.MAX_TEXT_CHARS else ''
    return redirect(where, message + cut)


@app.post('/analysis/{aid}/upload')
def upload(aid: AnalysisId, files: List[UploadFile] = File(default=[]), back: str = Form('')):
    if not store.get_analysis(aid):
        return not_found()
    where = check_destination(aid, back)
    if store.active_job(aid):
        return redirect(where, 'Wait for the current task to finish before editing evidence.')
    added, problems = ingest_files(aid, files)
    return redirect(where, upload_message(added, problems))


@app.post('/analysis/{aid}/raw-csv')
def raw_csv(aid: AnalysisId, raw_csv: UploadFile = File(...)):
    """The original single-CSV import, kept so existing links and scripts continue to work."""
    return upload(aid, [raw_csv], '')


def cleared_message(units, had_results):
    """Says what a clear removed, so the page never leaves the user guessing whether the report went too."""
    if not units:
        return 'There was no evidence to delete.'
    gone = f'{units:,} communication unit{"" if units == 1 else "s"}'
    if had_results:
        gone += ' and every result built from them (ratings, guidance, Vibe Check, report, PDF and CSV)'
    return f'Deleted {gone}. The analysis details and the numbers of earlier checks (scores and counts, never messages, names or file names) are kept, so trends survive a re-upload.'


@app.post('/analysis/{aid}/clear-units')
def clear_units(aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    if store.active_job(aid):
        return redirect(f'/analysis/{aid}/workspace', 'Wait for the current task to finish before clearing evidence.')
    units, had_results = store.count_units(aid), report.has_results(analysis.metrics_for(aid), store.latest_vibe(aid))
    store.clear_evidence(aid)
    return redirect(f'/analysis/{aid}/workspace', cleared_message(units, had_results))


from progress import describe as progress_of      # noqa: E402  steps and percentage of a running task, for the progress window


# ---- background tasks ------------------------------------------------------------------
WORKER = ThreadPoolExecutor(max_workers=1)    # one model call at a time on a small instance
JOB_LOCK = threading.Lock()


def start_job(aid, kind):
    a = store.get_analysis(aid)
    if not a:
        return not_found()
    destination = f'/analysis/{aid}/workspace'
    if not store.count_units(aid):
        return redirect(destination, 'Add some communication to analyze first.')
    period = store.period_coverage(a)
    if period and not period['kept']:
        return redirect(destination, f'All {period["total"]:,} communication units are dated outside the review period '
                                     f'({report.period_span(period)}). Change the time frame in the New check, or choose all dates.')
    if rules.is_rules(a['engine']):
        pass                                                                                   # word lists need nothing to be ready
    elif cloud.is_cloud(a['engine']):
        if not cloud.available(a['engine']):
            return redirect(destination, 'That engine is not available here, so it cannot run. Choose another engine.')
    elif not local_model_ready(a['engine']):
        return redirect(destination, 'The model is still preparing. Please retry shortly.')
    with JOB_LOCK:
        if store.active_job(aid):
            return redirect(destination, 'A task is already in progress.')
        if store.count_active_jobs() >= MAX_QUEUED_JOBS:
            return redirect(destination, 'The queue is full. Please try again shortly.')
        jid = store.create_job(aid, kind)
    WORKER.submit(pipeline.run_job, jid, aid, kind)
    return redirect(destination)


def remember_sensitivity(aid, value):
    if value in pipeline.SENSITIVITIES:
        store.update_analysis(aid, vibe_sensitivity=value)


@app.post('/analysis/{aid}/run-ai')
def run_ai(aid: AnalysisId, vibe_sensitivity: Optional[str] = Form(None)):
    if not store.get_analysis(aid):
        return not_found()
    remember_sensitivity(aid, vibe_sensitivity)
    return start_job(aid, 'full')


@app.post('/analysis/{aid}/run-vibe')
def run_vibe(aid: AnalysisId, vibe_sensitivity: Optional[str] = Form(None)):
    if not store.get_analysis(aid):
        return not_found()
    remember_sensitivity(aid, vibe_sensitivity)
    return start_job(aid, 'vibe')


@app.post('/analysis/{aid}/cancel')
def cancel(aid: AnalysisId):
    if not store.get_analysis(aid):
        return not_found()
    if store.request_cancel(aid):
        return redirect(f'/analysis/{aid}/workspace', 'Cancelling. The task stops once the model call in progress has finished.')
    return redirect(f'/analysis/{aid}/workspace', 'There is no task to cancel.')


@app.get('/analysis/{aid}/job')
def job_status(aid: AnalysisId):
    job = store.latest_job(aid)
    if job:
        job.pop('result', None)
        a = store.get_analysis(aid)
        job['progress'] = progress_of(job, bool(a and a['incognito']))
    return JSONResponse(job or {})


# ---- reports ---------------------------------------------------------------------------
@app.get('/analysis/{aid}/report', response_class=HTMLResponse)
def report_page(request: Request, aid: AnalysisId):
    ctx = report_context(aid, 'report')
    return render(request, 'report.html', **ctx)


def track_views(ms, only=None):
    """What the Track tab and its pages draw: for each of Escalation, Conflict and Empathy the headline numbers, the flags and a figure; and the three together."""
    if not track.available(ms):
        return None
    m = ms['m']
    out = {'cards': [], 'compare': vibe_check.figure(lambda w: bei_charts.track_compare(m['stretches'], width=w)), 'link': track.link(m), 'k': len(m['stretches']), 'early': not m['verdicts']}
    for key in track.KEYS:
        if only and key != only:
            continue
        flags = track.flags_for(m, key)
        c = track.card(m, key)
        c['figure'] = vibe_check.figure(lambda w, k=key, f=flags: bei_charts.track_chart(k, track.NAMES[k], m['stretches'], f, width=w))
        c['events'] = track.events(m, key)
        c['summary'] = track.summary(m, key)
        out['cards'].append(c)
    return out


RESULT_SCREENS = {'vibe': ('Vibe Check', 'users'), 'health': ('Health Score', 'pulse'), 'sentiment': ('Sentiment', 'chart'), 'passive': ('Passive-aggressive', 'chat'),
                  'alerts': ('Badges & Alerts', 'shield'), 'questions': ('Insights', 'check')}


def results_nav(aid, ctx, current):
    """The row of screens along the top of every results screen: only the ones this check has something for. (key, label, icon, href, current?)"""
    vibe_ok = bool(ctx.get('vibe') and ctx['vibe'].get('status') == 'ok')
    ms = ctx.get('measures')
    has = {'vibe': vibe_ok, 'health': bool(ms), 'sentiment': vibe_ok or bool(ctx.get('metrics')), 'passive': bool(ms), 'alerts': bool(ms or ctx.get('guidance')), 'questions': bool(ctx.get('answers'))}
    out = []
    for key in ('vibe', 'health', 'sentiment'):
        if has[key]:
            out.append((key, RESULT_SCREENS[key][0], RESULT_SCREENS[key][1], f'/analysis/{aid}/results/{key}', current == key))
    if ms:
        for key, icon_name in (('escalation', 'trend'), ('conflict', 'users'), ('empathy', 'heart')):
            out.append((key, track.NAMES[key], icon_name, f'/analysis/{aid}/track/{key}', current == key))
        out.append(('passive', 'Passive-aggressive', 'chat', f'/analysis/{aid}/results/passive', current == 'passive'))
    for key in ('alerts',):
        if has[key]:
            out.append((key, RESULT_SCREENS[key][0], RESULT_SCREENS[key][1], f'/analysis/{aid}/results/{key}', current == key))
    return out


def results_context(aid, what='page of results'):
    ctx = report_context(aid, what)
    ctx['track_view'] = track_views(ctx['measures']) if ctx.get('measures') else None
    return ctx


@app.get('/analysis/{aid}/results', response_class=HTMLResponse)
def results_page(request: Request, aid: AnalysisId):
    """The results of a workplace: the headline of each kind of result as a card. Each card opens its own screen."""
    a = store.get_analysis(aid)
    if not a or a['status'] == 'draft':
        return not_found()
    home = home_view(aid)
    if home and home.get('empty'):                 # nothing analysed yet: the same cards, empty, and the way to the first analysis
        return render(request, 'results.html', home=home, rid=aid, aid=aid, a=a, rnav=[], assets='', flagged_n=0, answers_n=0, units_n=store.count_units(aid))
    ctx = results_context(aid)
    flagged_n = len((ctx['vibe'] or {}).get('flagged') or []) if ctx.get('vibe') and ctx['vibe'].get('status') == 'ok' else 0
    return render(request, 'results.html', home=home, rid=aid, flagged_n=flagged_n, answers_n=len(ctx.get('answers') or []), rnav=results_nav(aid, ctx, 'overview'), **ctx)


@app.get('/analysis/{aid}/results/{screen}', response_class=HTMLResponse)
def results_screen(request: Request, aid: AnalysisId, screen: str, compare: str = 'previous'):
    """One kind of result on a screen of its own."""
    if screen not in RESULT_SCREENS:
        not_found()
    ctx = results_context(aid, f'{RESULT_SCREENS[screen][0]} screen')
    nav = results_nav(aid, ctx, screen)
    if not any(n[0] == screen for n in nav):
        raise PageError(404, 'Nothing here yet', f'This check has no {RESULT_SCREENS[screen][0]} result.', back=(f'/analysis/{aid}/results', 'Back to the results'))
    act = None
    if screen == 'alerts' and ctx.get('measures'):
        act = actview.build(ctx, compare)
    return render(request, 'screen.html', screen=screen, title=RESULT_SCREENS[screen][0], act=act, rnav=nav, **ctx)


@app.get('/analysis/{aid}/track/{key}', response_class=HTMLResponse)
def track_page(request: Request, aid: AnalysisId, key: str):
    """One measure of the Track tab on a page of its own: how it was built up over the chat, its flags, and the three measures together."""
    if key not in track.KEYS:
        not_found()
    ctx = report_context(aid, 'track')
    view = track_views(ctx['measures'], only=key) if ctx.get('measures') else None
    if not view:
        raise PageError(404, 'Too few messages for a timeline', 'Track needs at least 24 rated messages to follow a chat over time.', back=(f'/analysis/{aid}/results', 'Back to the results'))
    return render(request, 'track.html', track_view=view, card=view['cards'][0], key=key, names=track.NAMES, rnav=results_nav(aid, ctx, key), **ctx)


def detail_page(request, aid, page, *, attrs, title, kind, blurb, parent=None):
    a = store.get_analysis(aid)
    if not a:
        not_found()
    metrics = [m for m in analysis.metrics_for(aid) if m['attribute'] in attrs]
    if not metrics:
        raise PageError(404, 'No results yet', 'Run a check on the workplace page to rate the messages first.', back=(f'/analysis/{aid}/workspace', 'Open the workspace'))
    rows, total, pages, page = detail.comments(aid, attrs, page)
    figure_html, figure_note = detail.chart(aid, attrs, title)
    rated = metrics[0]['n']
    top = max(metrics, key=lambda m: m['fas'])
    hits = sum(round(m['detection_rate'] * m['n'] / 100) for m in metrics) if len(metrics) == 1 else total
    kpis = [{'label': 'Average FAS' if len(metrics) > 1 else 'FAS', 'value': f'{sum(m["fas"] for m in metrics) / len(metrics):.1f}', 'sub': 'strength of the tone, 0 to 100'},
            {'label': 'Messages showing it', 'value': f'{total:,}', 'sub': f'of {rated:,} rated'},
            {'label': 'Detection rate', 'value': f'{sum(m["detection_rate"] for m in metrics) / len(metrics):.0f}%', 'sub': 'of rated messages contain it'},
            {'label': 'Strongest' if len(metrics) > 1 else 'Strength when present', 'value': top['attribute'] if len(metrics) > 1 else f'{top["expression_strength"]:.0f}',
             'sub': f'FAS {top["fas"]:.1f}' if len(metrics) > 1 else 'out of 100'}]
    legend = [(m, detail.ROLES[i % len(detail.ROLES)]) for i, m in enumerate(attrs)] if len(attrs) > 1 else []
    return render(request, 'detail.html', a=a, aid=aid, title=title, kind=kind, blurb=blurb, parent=parent, kpis=kpis, members=sorted(metrics, key=lambda m: -m['fas']) if len(attrs) > 1 else [],
                  rows=rows, total=total, pages=pages, page=page, figure_html=figure_html, figure_note=figure_note, legend=legend, assets=vibe_check.assets_html(),
                  tone_class={'Positive': 'good', 'Tense': 'warn', 'Toxic': 'bad'}.get(title if title in ('Positive', 'Tense', 'Toxic') else analysis.family_of(title), ''))


@app.get('/analysis/{aid}/category/{name}', response_class=HTMLResponse)
def category_page(request: Request, aid: AnalysisId, name: str, page: int = 1):
    attrs = detail.family_attributes(name)
    if attrs is None:
        not_found()
    blurb = next((b for n, b in bei_charts.FAMILY_BLURB.items() if n == name), '')
    return detail_page(request, aid, page, attrs=attrs, title=name, kind='Category', blurb=blurb)


@app.get('/analysis/{aid}/tone/{name}', response_class=HTMLResponse)
def tone_page(request: Request, aid: AnalysisId, name: str, page: int = 1):
    if name not in analysis.ATTRIBUTES:
        not_found()
    return detail_page(request, aid, page, attrs=[name], title=name, kind=f'{analysis.family_of(name)} tone', parent=analysis.family_of(name),
                       blurb=f'Every message rated for {name.lower()}, and how it moves over time.')


@app.get('/analysis/{aid}/report.pdf')
def report_pdf(aid: AnalysisId):
    ctx = report_context(aid, 'report')
    data = report_export.pdf(ctx, BASE_DIR)
    return StreamingResponse(io.BytesIO(data), media_type='application/pdf',
                             headers={'Content-Disposition': f'attachment; filename="FelixaCommunityHealth_Report_{aid}.pdf"'})


@app.get('/analysis/{aid}/flagged', response_class=HTMLResponse)
def flagged_page(request: Request, aid: AnalysisId, kind: str = '', level: str = '', source: str = '', q: str = '', sort: str = 'severity', page: int = 1):
    """Every message Vibe Check flagged, none cut off, with filters and a CSV of what the filters show."""
    ctx = report_context(aid, 'list of flagged messages')
    view = flagged.build(ctx['vibe'], kind=kind, level=level, source=source, q=q, sort=sort, page=page)
    query = {k: v for k, v in (('kind', kind), ('level', level), ('source', source), ('q', q), ('sort', sort if sort != 'severity' else '')) if v}
    return render(request, 'flagged.html', view=view, aid=aid, a=ctx['a'], f={'kind': kind, 'level': level, 'source': source, 'q': q, 'sort': sort}, query=query,
                  levels=flagged.LEVELS, sorts=flagged.SORTS)


@app.get('/analysis/{aid}/flagged.csv')
def flagged_csv(aid: AnalysisId, kind: str = '', level: str = '', source: str = '', q: str = '', sort: str = 'severity'):
    ctx = report_context(aid, 'list of flagged messages')
    rows = flagged.csv_rows(flagged.build(ctx['vibe'], kind=kind, level=level, source=source, q=q, sort=sort)['all'])
    safe = [[report.csv_safe(c) for c in row] for row in rows]
    out = io.StringIO()
    csv.writer(out).writerows(safe)
    return StreamingResponse(io.BytesIO(out.getvalue().encode('utf-8-sig')), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename="felixa_flagged_messages_{aid}.csv"'})


@app.get('/analysis/{aid}/export')
def export(aid: AnalysisId):
    ctx = report_context(aid, 'export')
    out = io.StringIO()
    csv.writer(out).writerows(report.csv_rows(ctx))
    return StreamingResponse(io.BytesIO(out.getvalue().encode('utf-8-sig')), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename="felixa_community_health_{aid}.csv"'})


@app.get('/health')
def health():
    return JSONResponse({'ok': True, 'product': config.PRODUCT, 'version': config.VERSION, 'local_model_ready': local_model_ready() if config.local_model_enabled() else False, 'local_model_enabled': config.local_model_enabled(), 'claude_ready': cloud.available()})


def warn_if_sign_in_needs_https(host):
    """Logs a warning when a password is set and `host`, where the app listens, is more than this computer. A browser keeps the Secure
    session cookie only over https or at a localhost address, so someone who opens http://<this computer's address>:<port> and signs in
    with the right password lands on the form again (see login_page). Through a proxy that ends https, or an SSH tunnel to 127.0.0.1,
    sign-in works, so this only warns."""
    if os.getenv('FELIXA_PASSWORD') and host.strip('[]').lower() not in {name.strip('[]') for name in LOCAL_HOSTS}:
        log.warning('FELIXA_PASSWORD is set and the app listens on "%s". Its sign-in cookie is Secure, so a browser keeps it only over https or at '
                    'http://127.0.0.1 (or localhost): over plain http at any other address, sign-in returns to the form. Put an https proxy in '
                    'front of the app, or keep FELIXA_HOST=127.0.0.1 and reach it through an SSH tunnel (see the README).', host)


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    host = os.getenv('FELIXA_HOST', '127.0.0.1')
    warn_if_sign_in_needs_https(host)
    updater.start_background_check()
    uvicorn.run('app:app', host=host, port=int(os.getenv('PORT', '5050')), reload=False)


if __name__ == '__main__':
    main()
