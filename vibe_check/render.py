"""The Vibe Check section of the report: HTML for the web page, flowables for the PDF.

    html_str = render_html(result)            # drop into the report template
    story.extend(pdf_flowables(result))       # drop into the reportlab story

Both take the dict returned by `run_vibe_check`. Everything the section prints is escaped here;
message text from an upload never reaches the page unescaped.
"""
from __future__ import annotations

import html
import itertools

from . import charts
from .common import (fmt_int, fmt_score, long_date, md_bold_to_html, run_labels, score_label, score_tier, short_date, truncate,
                     type_label)

E = html.escape
_UIDS = itertools.count(1)

# ---- assets ------------------------------------------------------------------------
CSS = """
.vc{--vc-surface:var(--panel,#fff);--vc-ink:var(--ink,#0b0b0b);--vc-ink2:#52514e;--vc-muted:#898781;--vc-grid:#e1e0d9;--vc-axis:#c3c2b7;
--vc-series1:#1f66bf;--vc-pos:#1f66bf;--vc-neu:#898781;--vc-neg:#c93838;--vc-good:#0ca30c;--vc-warn:#fab219;--vc-serious:#ec835a;--vc-crit:#d03b3b;
--vc-on-fill:#fff;--vc-on-neu:#0b0b0b;--vc-on-warn:#0b0b0b;--vc-tile:#f4f4f1;--vc-soft:#fff0e0;--vc-rule:var(--line,#dfe3e8)}
[data-theme="dark"] .vc{--vc-surface:#1a1a19;--vc-ink:#f0efec;--vc-ink2:#c3c2b7;--vc-grid:#2c2c2a;--vc-axis:#383835;--vc-series1:#3987e5;--vc-pos:#3987e5;
--vc-neu:#c3c2b7;--vc-neg:#e66767;--vc-on-neu:#0b0b0b;--vc-tile:#242423;--vc-soft:#33200f;--vc-rule:#383835}
.vc h3{margin:40px 0 4px;font-size:22px}.vc h4{margin:24px 0 4px;font-size:18px}
.vc .vc-hero{display:grid;grid-template-columns:minmax(230px,300px) 1fr;gap:36px;align-items:center;margin:28px 0 8px}
.vc .vc-score{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;line-height:1}
.vc .vc-score-num{font-size:76px;font-weight:650;letter-spacing:-.035em;color:var(--vc-ink)}
.vc .vc-score-den{font-size:26px;color:var(--vc-ink2)}
.vc .vc-badge{display:inline-flex;align-items:center;gap:8px;margin-top:14px;font-size:19px;font-weight:650;color:var(--vc-ink)}
.vc .vc-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:24px 0 8px}
.vc .vc-kpi{background:var(--vc-tile);border-radius:12px;padding:16px 20px}
.vc .vc-kpi strong{display:block;font-size:32px;line-height:1.15;color:var(--vc-ink)}.vc .vc-kpi span{font-size:16px;color:var(--vc-ink2)}
.vc .vc-callout{background:var(--vc-soft);border-left:3px solid #F27435;border-radius:0 8px 8px 0;padding:18px 22px;margin:20px 0;color:var(--vc-ink)}
.vc .vc-callout strong{display:block;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--vc-ink2);margin-bottom:6px}
.vc .vc-cols{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px}
.vc .vc-hero>*,.vc .vc-cols>*,.vc .vc-kpis>*{min-width:0}
.vc .vc-card{border:1px solid var(--vc-rule);border-radius:12px;padding:4px 22px 14px}.vc .vc-card h3{margin-top:18px}
.vc .vc-fig{margin:14px 0 0}.vc .vc-svg{display:block;width:100%;height:auto;overflow:visible}
.vc .vc-narrow{display:none;max-width:520px;margin:0 auto}
.vc .vc-svg text{font-family:inherit}
.vc .vc-mark:hover{opacity:.86}.vc .vc-mark:focus,.vc .vc-hit:focus{outline:none}
.vc .vc-mark:focus-visible,.vc .vc-hit:focus-visible{stroke:var(--vc-ink);stroke-width:2}
.vc .vc-lines{list-style:none;padding:0;margin:10px 0}.vc .vc-lines li{display:flex;gap:12px;align-items:flex-start;margin:10px 0;line-height:1.6;color:var(--vc-ink)}
.vc .vc-icon{flex:none;margin-top:5px}
.vc .vc-flags{list-style:none;padding:0;margin:14px 0}
.vc .vc-flags li{display:grid;grid-template-columns:190px 1fr;gap:6px 18px;padding:14px 0;border-top:1px solid var(--vc-rule);overflow-wrap:anywhere}
.vc .vc-sev{font-size:15px;font-weight:650;color:var(--vc-ink);display:flex;gap:8px;align-items:flex-start}
.vc .vc-dot{flex:none;width:10px;height:10px;border-radius:50%;margin-top:7px}
.vc .vc-msg{color:var(--vc-ink);line-height:1.55}.vc .vc-src{grid-column:2;font-size:15px;color:var(--vc-ink2)}
.vc .vc-note,.vc .vc-caveat{font-size:16px;color:var(--vc-ink2);max-width:72ch}
.vc .vc-trendline{font-size:17px;color:var(--vc-ink);margin:10px 0}
.vc .vc-table{margin-top:6px}.vc .vc-table summary{font-size:16px;font-weight:600;color:var(--vc-ink2)}
.vc .vc-table table{font-size:16px}.vc .vc-table th{font-size:13px}
.vc details{margin-top:14px}
.vc-tip{position:fixed;z-index:50;max-width:280px;pointer-events:none;background:var(--vc-surface,#fff);color:var(--vc-ink,#0b0b0b);border:1px solid var(--vc-axis,#c3c2b7);border-radius:8px;
padding:9px 12px;box-shadow:0 6px 20px rgba(0,0,0,.14);font-size:15px;line-height:1.4}
.vc-tip[hidden]{display:none}.vc-tip .vc-tip-t{font-weight:650;margin-bottom:4px}
.vc-tip .vc-tip-r{display:flex;gap:8px;align-items:center}.vc-tip .vc-tip-r strong{font-weight:650}
.vc-tip .vc-key{flex:none;width:12px;height:3px;border-radius:2px}
@media(max-width:640px){.vc .vc-wide{display:none}.vc .vc-narrow{display:block}}
@media(max-width:1000px){.vc .vc-hero{grid-template-columns:1fr;gap:20px}}
@media(max-width:760px){.vc .vc-cols{grid-template-columns:1fr;gap:20px}.vc .vc-score-num{font-size:64px}.vc .vc-flags li{grid-template-columns:1fr}.vc .vc-src{grid-column:1}}
@media print{.vc-tip{display:none}.vc .vc-wide{display:block!important}.vc .vc-narrow{display:none!important}}
@media print{.vc details::details-content{content-visibility:visible!important;display:block!important}}
/* A heading stays with the note and chart it introduces (`.vc-keep` holds the three in one block), and a tile, card or message is not cut in two. */
@media print{.vc-keep,.vc>p,.vc-fig,.vc-kpi,.vc-card,.vc-callout,.vc-flags li,.vc-lines li{break-inside:avoid}.vc h3,.vc h4,.vc>p,.vc summary{break-after:avoid}}
"""

# Hover and keyboard-focus readouts. Progressive: the table beneath every chart carries every value.
# The second function is for the printer: a closed <details> prints as its summary line alone, so every table twin, the flagged messages past the
# tenth, the forecast factors and the note on limits would be missing from paper. It opens the ones that are closed for the print and shuts exactly
# those again afterwards (a fold the reader had opened stays open). Both signals are used because browsers differ in which one they send; the
# list only grows while printing, so a second signal for the same print cannot lose the folds the first one opened.
JS = """
(function(){if(window.__vcTip)return;window.__vcTip=1;
var tip=document.createElement('div');tip.className='vc-tip';tip.setAttribute('role','status');tip.hidden=true;document.body.appendChild(tip);
function show(el,ev){var d;try{d=JSON.parse(el.getAttribute('data-vc-tip'))}catch(e){return}
var host=el.closest('.vc')||document.body;if(tip.parentNode!==host)host.appendChild(tip);
while(tip.firstChild)tip.removeChild(tip.firstChild);
if(d.t){var h=document.createElement('div');h.className='vc-tip-t';h.textContent=d.t;tip.appendChild(h)}
(d.r||[]).forEach(function(r){var row=document.createElement('div');row.className='vc-tip-r';
var k=document.createElement('span');k.className='vc-key';if(/^[a-z0-9-]+$/.test(r[2]||''))k.style.background='var(--vc-'+r[2]+')';
var v=document.createElement('strong');v.textContent=r[1];var l=document.createElement('span');l.textContent=r[0];
row.appendChild(k);row.appendChild(v);row.appendChild(l);tip.appendChild(row)});
tip.hidden=false;var x,y;if(ev&&ev.clientX!=null){x=ev.clientX;y=ev.clientY}else{var b=el.getBoundingClientRect();x=b.left+b.width/2;y=b.top}
tip.style.left=Math.max(8,Math.min(innerWidth-tip.offsetWidth-8,x+14))+'px';tip.style.top=Math.max(8,y-tip.offsetHeight-12)+'px';
var cx=el.getAttribute('data-vc-x'),svg=el.ownerSVGElement,c=svg&&svg.querySelector('.vc-cross');
if(c&&cx!==null){c.setAttribute('x1',cx);c.setAttribute('x2',cx);c.style.visibility='visible'}}
function hide(el){tip.hidden=true;var svg=el&&el.ownerSVGElement,c=svg&&svg.querySelector('.vc-cross');if(c)c.style.visibility='hidden'}
function target(e){return e.target.closest&&e.target.closest('[data-vc-tip]')}
document.addEventListener('pointermove',function(e){var t=target(e);if(t)show(t,e);else hide()});
document.addEventListener('pointerleave',function(){hide()},true);
document.addEventListener('focusin',function(e){var t=target(e);if(t)show(t)});
document.addEventListener('focusout',function(e){var t=target(e);if(t)hide(t)});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){hide();return}
var t=target(e);if(!t||!t.ownerSVGElement)return;var k=e.key,m=[].slice.call(t.ownerSVGElement.querySelectorAll('[data-vc-tip]')),i=m.indexOf(t),j;
if(k==='ArrowRight'||k==='ArrowDown')j=Math.min(i+1,m.length-1);else if(k==='ArrowLeft'||k==='ArrowUp')j=Math.max(i-1,0);else if(k==='Home')j=0;else if(k==='End')j=m.length-1;else return;
e.preventDefault();m.forEach(function(x){x.tabIndex=-1});m[j].tabIndex=0;m[j].focus()});})();
(function(){if(window.__vcPrint)return;window.__vcPrint=1;var shut=[];
function openAll(){[].forEach.call(document.querySelectorAll('.vc details:not([open])'),function(d){d.open=true;shut.push(d)})}
function shutAgain(){shut.forEach(function(d){d.open=false});shut=[]}
window.addEventListener('beforeprint',openAll);window.addEventListener('afterprint',shutAgain);
if(window.matchMedia){var q=matchMedia('print');if(q.addEventListener)q.addEventListener('change',function(e){if(e.matches)openAll();else shutAgain()})}})();
"""


def assets_html() -> str:
    """The <style> and <script> blocks. Include once per page, or fold CSS/JS into your own bundle."""
    return f'<style>{CSS}</style><script>{JS}</script>'


# ---- small builders --------------------------------------------------------------
def _uid() -> str:
    return f'vc{next(_UIDS)}'


def _md(text) -> str:
    return md_bold_to_html(E(str(text or '')))


def _fig(build) -> str:
    """`build(width)` returns a Scene or None. Both layouts are emitted; CSS shows one."""
    wide = build(charts.W)
    if wide is None:
        return ''
    return (f'<figure class="vc-fig"><div class="vc-chart vc-wide">{charts.to_svg(wide, _uid())}</div>'
            f'<div class="vc-chart vc-narrow">{charts.to_svg(build(charts.NARROW_W), _uid())}</div></figure>')


def _keep(*parts) -> str:
    """A heading with the note and the chart it introduces, as one block that a printed page does not split. A chain of `break-after: avoid`
    is not enough: Chromium sometimes breaks it between the note and the chart, or moves everything before it, depending on where the page ends."""
    return '<div class="vc-keep">' + ''.join(parts) + '</div>'


def _table(headers, rows, summary='View the data as a table') -> str:
    head = ''.join(f'<th scope="col">{E(str(h))}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{E(str(c))}</td>' for c in row) + '</tr>' for row in rows)
    return (f'<details class="vc-table"><summary>{E(summary)}</summary><div class="table-wrap"><table>'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></details>')


figure = _fig            # public: wide + phone layouts of one chart, from build(width) -> Scene
data_table = _table      # public: the table twin that sits under every chart


def _lines(items) -> str:
    if not items:
        return ''
    return '<ul class="vc-lines">' + ''.join(
        f'<li>{charts.icon_svg(i["tone"])}<span>{_md(i["text"])}</span></li>' for i in items) + '</ul>'


def _tile(value, label) -> str:
    return f'<div class="vc-kpi"><strong>{E(str(value))}</strong><span>{E(label)}</span></div>'


def _period(vibe) -> str:
    p = vibe.get('period')
    if not p:
        return ''
    a, b = long_date(p['start']), long_date(p['end'])
    return a if a == b else f'{a} – {b}'


def _coverage(vibe) -> str:
    """'500 of 600 messages analysed' when sampling, the overall cap or a failed batch left some messages out, else just the count."""
    n = vibe['analyzed_n']
    read = vibe.get('input_n') or n
    return f'{fmt_int(n)} of {fmt_int(read)} messages analysed' if read > n else f'{fmt_int(n)} messages analysed'


def _sev_role(severity: int) -> str:
    return 'crit' if severity >= 7 else 'serious' if severity >= 4 else 'warn'


# ---- sections --------------------------------------------------------------------
def _flagged_html(vibe) -> str:
    flagged = vibe.get('flagged') or []
    total = vibe.get('flagged_total', len(flagged))
    if not total:
        return '<h3>Flagged messages</h3><p class="vc-note">No messages were flagged.</p>'

    def item(f):
        who = {'SUBJECT': 'Person', 'AUDIENCE': 'Audience'}.get(str(f.get('role') or '').upper(), '')
        meta = ' · '.join(x for x in (f.get('source'), who, short_date(f['timestamp']) if f.get('timestamp') else '') if x)
        return (f'<li><span class="vc-sev"><span class="vc-dot" style="background:var(--vc-{_sev_role(f["severity"])})"></span>'
                f'<span>{E(type_label(f["type"]))} · {f["severity"]}/10</span></span>'
                f'<span class="vc-msg">“{E(truncate(f["message"], 260))}”</span><span class="vc-src">{E(meta)}</span></li>')

    head = ''.join(item(f) for f in flagged[:10])
    rest = flagged[10:]
    more = (f'<details><summary>Show the other {"flagged message" if len(rest) == 1 else fmt_int(len(rest)) + " flagged messages"}</summary>'
            f'<ul class="vc-flags">{"".join(item(f) for f in rest)}</ul></details>') if rest else ''
    return (f'<h3>Flagged messages ({fmt_int(total)})</h3>'
            '<p class="vc-note">Ordered by severity. Each message is copied from your upload, never paraphrased.</p>'
            f'<ul class="vc-flags">{head}</ul>{more}')


FORECAST_HEAD = '<h3>Behaviour forecast and recommendations</h3>'


def _forecast_html(fc) -> str:
    bar = _fig(lambda w: charts.forecast_bar(fc['improve'], fc['worsen'], width=w))
    tab = _table(['Measure', 'Value'], [['Chance the community improves', f'{fc["improve"]}%'],
                                       ['Chance toxicity increases', f'{fc["worsen"]}%'],
                                       ['Confidence', f'{fc["confidence"]} ({fc["n_reports"]} reports)']])
    factors = ''.join(f'<li>{E(f)}</li>' for f in fc['factors'])
    return (_keep(FORECAST_HEAD, '<h4>Probability forecast</h4>',
                  f'<div class="vc-kpis">{_tile(str(fc["improve"]) + "%", "chance the community improves")}'
                  f'{_tile(str(fc["worsen"]) + "%", "chance toxicity increases")}'
                  f'{_tile(fc["confidence"], "confidence, from " + str(fc["n_reports"]) + " reports")}</div>',
                  f'<p class="vc-caveat">{E(fc["caveat"])}</p>', bar)
            + f'{tab}<details><summary>Why these numbers</summary><ul>{factors}</ul></details>')


def _progress_html(prog) -> str:
    n = prog['n_reports']
    if n < 2:
        return ('<h3>Progress over time</h3><p class="vc-note">This is the first Vibe Check for this analysis. '
                'Run it again after the next upload to start tracking trends and unlock the behaviour forecast.</p>')
    reps = prog['reports']
    arrow = {'up': 'up', 'down': 'down', 'flat': 'steady'}[prog['direction']]
    diff = prog['diff']
    head = (f'{n} reports · oldest {fmt_score(prog["oldest"])}/10 → latest {fmt_score(prog["latest"])}/10 '
            f'({arrow}{"" if not diff else " " + ("+" if diff > 0 else "−") + fmt_score(abs(diff))}) · average {fmt_score(prog["average"])}/10')
    s = prog['sentiment']
    labels = run_labels(reps)

    score_tab = _table(['Run', 'Score', 'Messages', 'Flagged', 'Sensitivity'],
                       [[labels[i]['long'], f'{fmt_score(r["score"])}/10', fmt_int(r.get('analyzed_n') or 0),
                         fmt_int(r.get('flagged_count') or 0), str(r.get('sensitivity') or '').strip().capitalize() or 'Not recorded'] for i, r in enumerate(reps)])
    mix_tab = _table(['Run', 'Friendly', 'Neutral', 'Unfriendly'],
                     [[labels[i]['long'], fmt_int(r.get('friendly') or 0), fmt_int(r.get('neutral') or 0),
                       fmt_int(r.get('unfriendly') or 0)] for i, r in enumerate(reps)])
    flag_tab = _table(['Run', 'Flagged messages'], [[labels[i]['long'], fmt_int(r.get('flagged_count') or 0)] for i, r in enumerate(reps)])
    tox_tab = _table(['Toxicity type', 'Instances'], [[type_label(k), fmt_int(v)] for k, v in prog['toxicity']]) if prog['toxicity'] else ''
    tox_desc = 'Toxicity types added up over all saved Vibe Checks: ' + ', '.join(f'{type_label(k)} {v}' for k, v in prog['toxicity'][:7]) + '.'

    parts = [
        '<h3>Progress over time</h3>',
        f'<p class="vc-note">{E(head)}</p>',
        f'<p class="vc-trendline">{_md(prog["trend"]["text"])}</p>',
        _lines(prog['health']),
        _keep(f'<h4>{"Impact" if prog["multi_source"] else "Friendliness"} score over time</h4>',
              _fig(lambda w: charts.score_trend(reps, prog['multi_source'], width=w))), score_tab,
        _keep('<h4>Sentiment evolution</h4>',
              f'<p class="vc-note">Friendly {s["friendly_first"]}% → {s["friendly_last"]}% ({s["friendly_delta"]:+d}%). '
              f'Unfriendly {s["unfriendly_first"]}% → {s["unfriendly_last"]}% ({s["unfriendly_delta"]:+d}%).</p>',
              _fig(lambda w: charts.sentiment_stack(reps, width=w))), mix_tab,
        _keep('<h4>Flagged messages trend</h4>',
              f'<p class="vc-note">{_md(prog["flagged"]["text"])}</p>',
              _fig(lambda w: charts.flagged_trend(reps, width=w))), flag_tab,
        _keep('<h4>Cumulative toxicity</h4>',
              _fig(lambda w: charts.ranked_bars(prog['toxicity'], title='Cumulative toxicity breakdown', desc=tox_desc, unit='instances', width=w))
              if prog['toxicity'] else '<p class="vc-note">No toxicity detected across all reports.</p>'), tox_tab,
        _forecast_html(prog['forecast']) if prog.get('forecast') else FORECAST_HEAD,
        '<h4>Predictions</h4>' + _lines(prog['predictions']) if prog['predictions'] else '',
        '<h4>Positive psychology recommendations</h4>' + _lines(prog['recommendations']),
    ]
    return ''.join(parts)


METHOD = ('Vibe Check reads every message and sorts it into friendly, neutral or unfriendly. The sensitivity you chose sets the bar: low lets banter '
          'pass and high flags any rude tone. It flags unfriendly messages by number and counts the kinds of toxicity it finds, such as insults, '
          'harassment or threats. The friendliness score out of 10 is the model\'s own rating of the whole mix. When you add several sources, each '
          'one counts in proportion to how many of its messages were read, so the bigger chats weigh more, and the smallest ones are grouped as '
          '"Other small sources" to keep the page short. A message over 500 characters is read in full, as passages that each count as one message. '
          'A very large upload is read in batches and sampled evenly, and a sampled source counts as its sample. Trend lines and the forecast need at '
          'least two checks. They compare your saved checks as they are, so if you changed the sensitivity between two of them, part of the change may '
          'come from that setting. The forecast is a rule of thumb that shows a direction, not a promise. A language model makes these judgement calls '
          'and trained human raters have not checked them yet, so treat every figure as a starting point for a conversation.')

# Shown above the per-source meters, in the page and the PDF. The weight is the sample (`analyzed_n`, beside each meter), see engine.aggregate.
IMPACT_NOTE = ('The Impact Score is the average of the source scores, weighted by the number of messages analysed from each source (shown '
               'beside each meter), so a source with more analysed messages influences it more. A source that was sampled down counts as '
               'its sample, not its full size.')


def render_html(vibe: dict | None, *, assets: bool = True, notice: str = '') -> str:
    """Returns the Vibe Check section. `assets=False` leaves out <style>/<script> when the host bundles `CSS` and `JS`.
    `notice` is a sentence from the host, printed in a callout under the heading of a finished Vibe Check (for example, that
    the newest run failed and this is an earlier one)."""
    if not vibe:
        return ''
    pre = assets_html() if assets else ''
    if vibe.get('status') != 'ok':
        return (f'{pre}<section class="panel vc" id="vibe-check"><div class="eyebrow">Behaviour intelligence</div><h2>Vibe Check</h2>'
                f'<div class="vc-callout"><strong>Not run</strong>{E(vibe.get("reason") or "Vibe Check did not run.")}</div></section>')

    score, n = vibe['score'], vibe['analyzed_n']
    mix = vibe['sentiment']
    tier = score_tier(score)
    tone = {'excellent': 'good', 'good': 'good', 'attention': 'warn', 'poor': 'bad'}[tier]
    sens = vibe.get('sensitivity', 'medium')
    from .engine import SENSITIVITY_LABELS
    meta = ' · '.join(x for x in (
        _coverage(vibe), f'{len(vibe["sources"])} source{"s" if len(vibe["sources"]) != 1 else ""}',
        f'Sensitivity: {SENSITIVITY_LABELS.get(sens, sens)}', _period(vibe)) if x)

    src_names = ', '.join(s['name'] for s in vibe['sources'])
    sent_tab = _table(['Sentiment', 'Messages', 'Share'], [
        ['Friendly', fmt_int(mix['friendly']), f'{mix["pct"]["friendly"]}%'],
        ['Neutral', fmt_int(mix['neutral']), f'{mix["pct"]["neutral"]}%'],
        ['Unfriendly', fmt_int(mix['unfriendly']), f'{mix["pct"]["unfriendly"]}%']])
    has_tox = any(v > 0 for v in vibe['toxicity_types'].values())
    tox_tab = _table(['Toxicity type', 'Instances'], [[type_label(k), fmt_int(v)] for k, v in
                                                     sorted(vibe['toxicity_types'].items(), key=lambda kv: (-kv[1], kv[0])) if v]) if has_tox else ''

    out = [pre, '<section class="panel vc" id="vibe-check" aria-labelledby="vc-h">',
           '<div class="vc-keep"><div class="eyebrow">Behaviour intelligence</div><h2 id="vc-h">Vibe Check</h2>',
           f'<div class="vc-callout"><strong>Latest attempt failed</strong>{E(notice)}</div>' if notice else '',
           '<p>How friendly the communication feels across everything you supplied, what is working, and where a small change would help.</p>',
           f'<p class="vc-note">{E(meta)}</p>',
           '<div class="vc-hero"><div>',
           f'<div class="vc-score"><span class="vc-score-num">{E(fmt_score(score))}</span><span class="vc-score-den">/ 10</span></div>',
           f'<div class="vc-badge">{charts.icon_svg(tone, 20)}<span>{E(score_label(score))}</span></div></div>',
           f'<div><div class="eyebrow" style="margin-bottom:6px">{"Impact" if vibe.get("multi_source") else "Friendliness"} score</div>',
           _fig(lambda w: charts.score_meter(score, width=w)), '</div></div></div>',      # the hero, then the block of title and hero
           f'<div class="vc-kpis">{_tile(fmt_int(n), "messages analysed")}{_tile(len(vibe["sources"]), "sources")}'
           f'{_tile(fmt_int(vibe["flagged_total"]), "flagged messages")}{_tile(str(mix["pct"]["friendly"]) + "%", "friendly")}</div>']
    if vibe.get('summary'):
        out.append(f'<div class="vc-callout"><strong>Vibe verdict</strong>{E(vibe["summary"])}</div>')
    out += [_keep('<h3>Sentiment mix</h3>', _fig(lambda w: charts.sentiment_bar(mix['friendly'], mix['neutral'], mix['unfriendly'], width=w))), sent_tab,
            '<div class="vc-cols">']
    if vibe.get('strengths'):
        out.append(f'<div class="vc-card"><h3>Community strengths</h3><p>{E(vibe["strengths"])}</p></div>')
    if vibe.get('recommendation'):
        out.append(f'<div class="vc-card"><h3>Recommendations</h3><p>{E(vibe["recommendation"])}</p></div>')
    out.append('</div>')
    out += [_keep('<h3>Toxicity breakdown</h3>', _fig(lambda w: charts.toxicity_bars(vibe['toxicity_types'], width=w)) if has_tox
                  else '<p class="vc-note">No toxicity detected.</p>'), tox_tab, _flagged_html(vibe)]
    if vibe.get('multi_source'):
        out += [_keep('<h3>Impact score by source</h3>', f'<p class="vc-note">{E(IMPACT_NOTE)}</p>',
                      _fig(lambda w: charts.source_meters(vibe['sources'], width=w))),
                _table(['Source', 'Score', 'Messages', 'Flagged'], [[s['name'], f'{fmt_score(s["score"])}/10', fmt_int(s['analyzed_n']),
                                                                  fmt_int(s.get('flagged_n', 0))] for s in vibe['sources']])]
    out.append(_progress_html(vibe['progress']) if vibe.get('progress') else
               '<h3>Progress over time</h3><p class="vc-note">This is the first Vibe Check for this analysis. Run it again after the next '
               'upload to start tracking trends and unlock the behaviour forecast.</p>')
    notes = ''.join(f'<li>{E(x)}</li>' for x in vibe.get('notes', []))
    out.append(f'<details><summary>How Vibe Check works</summary><p>{E(METHOD)}</p>'
               f'<p class="vc-note">Sources: {E(src_names)}. Model: {E(", ".join(vibe.get("models") or []) or "not recorded")}. '
               f'Method {E(vibe["version"])}.</p>{"<ul>" + notes + "</ul>" if notes else ""}</details>')
    out.append('</section>')
    return ''.join(out)


# ---- PDF -------------------------------------------------------------------------
def pdf_flowables(vibe: dict | None, *, fonts=('Helvetica', 'Helvetica-Bold'), width: float = 504, notice: str = '') -> list:
    """reportlab flowables for the Vibe Check section, starting with its heading.
    `fonts` is (regular, bold); pass the report's own registered fonts. `notice` is a sentence from the host, printed under the
    heading of a finished Vibe Check (see `render_html`)."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    if not vibe:
        return []
    reg, bold = fonts
    ink, soft = colors.HexColor('#212121'), colors.HexColor('#555555')
    st = {
        'head': ParagraphStyle('vcHead', fontName=bold, fontSize=23, leading=29, spaceAfter=14, textColor=ink, keepWithNext=True),
        'sub': ParagraphStyle('vcSub', fontName=bold, fontSize=13, leading=18, spaceBefore=12, spaceAfter=6, textColor=ink, keepWithNext=True),
        'body': ParagraphStyle('vcBody', fontName=reg, fontSize=10.5, leading=15, spaceAfter=8, textColor=ink),
        'small': ParagraphStyle('vcSmall', fontName=reg, fontSize=8.5, leading=12, spaceAfter=6, textColor=soft, wordWrap='CJK'),
        'hero': ParagraphStyle('vcHero', fontName=bold, fontSize=46, leading=50, textColor=ink),
    }

    def P(text, style='body'):
        t = charts.pdf_safe(E(str(text or ''))).replace('\n', '<br/>')
        import re
        t = re.sub(r'\*\*(.+?)\*\*', rf'<font name="{bold}">\1</font>', t)
        return Paragraph(t, st[style])

    scale = width / charts.W
    drawing = lambda scene: charts.to_drawing(scene, fonts=fonts, scale=scale)   # noqa: E731
    story: list = [P('Vibe Check', 'head')]
    if vibe.get('status') != 'ok':
        story.append(P(vibe.get('reason') or 'Vibe Check did not run.'))
        return story
    if notice:
        story.append(P(notice))

    score, mix = vibe['score'], vibe['sentiment']
    from .engine import SENSITIVITY_LABELS
    sens = vibe.get('sensitivity', 'medium')
    story.append(P(' | '.join(x for x in (_coverage(vibe), f'{len(vibe["sources"])} source(s)',
                                          f'Sensitivity: {SENSITIVITY_LABELS.get(sens, sens)}', _period(vibe)) if x), 'small'))
    hero = Table([[Paragraph(f'{E(fmt_score(score))}<font size="16" name="{reg}"> / 10</font>', st['hero']),
                   [P(('Impact' if vibe.get('multi_source') else 'Friendliness') + ' score: ' + score_label(score), 'sub'),
                    drawing(charts.score_meter(score, width=300 / scale))]]], colWidths=[170, width - 170])
    hero.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story += [hero, Spacer(1, 8)]
    if vibe.get('summary'):
        story += [P('Vibe verdict', 'sub'), P(vibe['summary'])]
    sent = charts.sentiment_bar(mix['friendly'], mix['neutral'], mix['unfriendly'])
    story.append(KeepTogether([P('Sentiment mix', 'sub'), drawing(sent)]) if sent else P('Sentiment mix', 'sub'))
    story.append(P(f'Friendly {fmt_int(mix["friendly"])} ({mix["pct"]["friendly"]}%) | Neutral {fmt_int(mix["neutral"])} '
                   f'({mix["pct"]["neutral"]}%) | Unfriendly {fmt_int(mix["unfriendly"])} ({mix["pct"]["unfriendly"]}%)', 'small'))
    if vibe.get('strengths'):
        story += [P('Community strengths', 'sub'), P(vibe['strengths'])]
    if vibe.get('recommendation'):
        story += [P('Recommendations', 'sub'), P(vibe['recommendation'])]

    tox = charts.toxicity_bars(vibe['toxicity_types'])
    story.append(KeepTogether([P('Toxicity breakdown', 'sub'), drawing(tox)]) if tox else
                 KeepTogether([P('Toxicity breakdown', 'sub'), P('No toxicity detected.')]))
    flagged = vibe.get('flagged') or []
    story.append(P(f'Flagged messages ({fmt_int(vibe.get("flagged_total", 0))})', 'sub'))
    if flagged:
        rows = [[P('Type', 'small'), P('Message', 'small'), P('Source', 'small')]] + [
            [P(f'{type_label(f["type"])} {f["severity"]}/10', 'small'), P(f'"{truncate(f["message"], 220)}"', 'small'),
             P(f.get('source') or '', 'small')] for f in flagged[:10]]
        t = Table(rows, colWidths=[86, width - 86 - 110, 110], repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F2F2F2')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#F27435')),
                               ('LINEBELOW', (0, 1), (-1, -1), .4, colors.HexColor('#D8D8D8')),
                               ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6)]))
        story.append(t)
        if len(flagged) > 10:
            story.append(P(f'...and {fmt_int(len(flagged) - 10)} more flagged messages in the web report.', 'small'))
    else:
        story.append(P('No messages were flagged.'))
    if vibe.get('multi_source'):
        story += [P('Impact score by source', 'sub'),
                  P(IMPACT_NOTE, 'small'),
                  drawing(charts.source_meters(vibe['sources']))]

    prog = vibe.get('progress')
    if prog and prog['n_reports'] >= 2:
        reps = prog['reports']
        story += [P('Progress over time', 'sub'), P(prog['trend']['text'])]
        story += [P('- ' + h['text'], 'small') for h in prog['health']]
        cumulative = charts.ranked_bars(prog['toxicity'], title='Cumulative toxicity', unit='instances', desc='Toxicity added up over all saved Vibe Checks.')
        for scene in (charts.score_trend(reps, prog['multi_source']), charts.sentiment_stack(reps), charts.flagged_trend(reps), cumulative):
            if scene:
                story += [Spacer(1, 4), KeepTogether([P(scene.title, 'sub'), drawing(scene)])]
        fc = prog.get('forecast')
        if fc:
            story += [P('Behaviour forecast', 'sub'),
                      KeepTogether([drawing(charts.forecast_bar(fc['improve'], fc['worsen']))]),
                      P(f'{fc["improve"]}% chance the community improves | {fc["worsen"]}% chance toxicity increases | '
                        f'confidence {fc["confidence"]} ({fc["n_reports"]} reports)'),
                      P(fc['caveat'], 'small')]
            story += [P('- ' + f, 'small') for f in fc['factors']]
        for title, key in (('Predictions', 'predictions'), ('Positive psychology recommendations', 'recommendations')):
            if prog.get(key):
                story += [P(title, 'sub')] + [P('- ' + x['text'], 'small') for x in prog[key]]
    story += [P('How Vibe Check works', 'sub'), P(METHOD, 'small')]
    story += [P(x, 'small') for x in vibe.get('notes', [])]
    return story
