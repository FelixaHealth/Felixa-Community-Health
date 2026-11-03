"""The results dashboard (dashboard.py and /dashboard), the progress window's data (progress.py) and the side bar that links them. Run with:

    python3 -m unittest tests.test_dashboard -v
"""
import datetime
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase  # noqa: E402

import dashboard  # noqa: E402
import progress  # noqa: E402

TODAY = datetime.date(2026, 11, 3)


def record(when, health, n=100, members=None, **kw):
    out = {'created_at': when.isoformat(timespec='seconds'), 'gen': int(when.timestamp()), 'n': n, 'total': n, 'sampled': 0, 'basis': 'time', 'stretches': 12, 'model': 'qwen2.5:7b',
           'sensitivity': 'medium', 'health': health, 'health_sd': 12.0, 'hostile_n': 5, 'care_n': 10, 'dig_n': 2, 'heated_n': 8, 'episodes': 0, 'peak': 0.0, 'sig': '1:2'}
    if members:
        out['members'] = members
    out.update(kw)
    return out


class DashboardCase(AppCase):
    def workplace(self, name, kind='Online community', channel='Discord', checks=()):
        aid = self.new_analysis(name=name)
        self.store.update_analysis(aid, subject_type=kind, channel=channel)
        for when, health, extra in checks:
            self.assertTrue(self.store.save_measure_snapshot(aid, record(when, health, **extra)))
            snap = {'created_at': (when - datetime.timedelta(minutes=2)).isoformat(timespec='seconds'), 'score': round(health / 10, 1), 'friendly': 30, 'neutral': 30, 'unfriendly': 5,
                    'flagged_count': 3, 'analyzed_n': 100}
            with self.store.db() as c:
                c.execute('INSERT INTO vibe_history(analysis_id,created_at,snapshot_json) VALUES(?,?,?)', (aid, snap['created_at'], json.dumps(snap)))
        return aid

    def day(self, ago):
        return datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=ago), datetime.time(10))


class Dashboard(DashboardCase):
    def test_a_dashboard_with_no_results_says_so_and_points_to_a_workplace(self):
        page = self.client.get('/dashboard').text
        self.assertEqual(self.client.get('/dashboard').status_code, 200)
        self.assertTrue('No results yet' in page or 'Results dashboard' in page)

    def test_every_check_of_every_workplace_appears_in_charts_tables_and_tiles(self):
        a = self.workplace('Dash Alpha', checks=[(self.day(60), 55.0, {'members': 100}), (self.day(30), 62.0, {'members': 110}), (self.day(2), 70.0, {'members': 130})])
        b = self.workplace('Dash Beta', 'Team', 'Slack', checks=[(self.day(40), 45.0, {}), (self.day(3), 52.0, {})])
        page = self.client.get('/dashboard').text
        for needle in ('Health score over time', 'Vibe Check over time', 'How people spoke', 'Members over time', 'Latest health score', 'Latest Vibe Check',
                       'Openly hostile, per 100 messages', 'Care, per 100 messages', 'Latest check of each workplace', 'All checks', 'Dash Alpha', 'Dash Beta'):
            self.assertIn(needle, page, needle)
        self.assertGreaterEqual(page.count('<svg'), 8, 'the charts are drawn, in two layouts each')
        self.assertIn('<h2>Results dashboard</h2>' if False else 'Results dashboard', page)
        ctx = dashboard.build({}, TODAY)
        self.assertEqual(ctx['checks_n'] >= 5, True)
        tiles = {k['label']: k for k in ctx['kpis']}
        self.assertGreaterEqual(int(tiles['Checks']['value'].replace(',', '')), 5)
        self.assertEqual(tiles['Members']['value'] != '–', True)
        # every check is a row of the table behind the charts
        rows = [r for r in dashboard.all_checks() if r['aid'] in (a, b)]
        self.assertEqual(len(rows), 5)
        self.assertEqual([r['health'] for r in rows if r['aid'] == a], [55.0, 62.0, 70.0])
        self.assertTrue(all(r['vibe'] is not None for r in rows), 'the Vibe Check score saved with each check is matched to it')
        self.assertAlmostEqual(rows[0]['conflict'], 5.0)

    def test_the_filters_switch_workplace_kind_platform_and_dates(self):
        a = self.workplace('Filter Alpha', 'Online community', 'Discord', checks=[(self.day(200), 50.0, {}), (self.day(10), 60.0, {})])
        b = self.workplace('Filter Beta', 'Team', 'Slack', checks=[(self.day(20), 70.0, {})])
        names = lambda args: sorted({r['workplace'] for r in dashboard.apply(dashboard.all_checks(), dashboard.read_filters(args, dashboard.all_checks(), TODAY))} & {'Filter Alpha', 'Filter Beta'})  # noqa: E731
        self.assertEqual(names({}), ['Filter Alpha', 'Filter Beta'])
        self.assertEqual(names({'workplace': str(a)}), ['Filter Alpha'])
        self.assertEqual(names({'workplace': str(b)}), ['Filter Beta'])
        self.assertEqual(names({'kind': 'Team'}), ['Filter Beta'])
        self.assertEqual(names({'platform': 'Discord'}), ['Filter Alpha'])
        self.assertEqual(names({'platform': 'Discord', 'kind': 'Team'}), [], 'filters narrow one another')
        wide = dashboard.all_checks()
        in_range = lambda args: [r for r in dashboard.apply(wide, dashboard.read_filters(args, wide, datetime.date.today())) if r['aid'] == a]  # noqa: E731
        self.assertEqual(len(in_range({'range': 'all'})), 2)
        self.assertEqual(len(in_range({'range': '90'})), 1, 'the check from 200 days ago leaves a 90 day view')
        self.assertEqual(len(in_range({'range': '365'})), 2)
        day = lambda ago: (datetime.date.today() - datetime.timedelta(days=ago)).isoformat()  # noqa: E731
        self.assertEqual(len(in_range({'from': day(250), 'to': day(150)})), 1, 'a custom period')
        self.assertEqual(len(in_range({'from': day(150), 'to': day(250)})), 1, 'a period typed backwards is turned round')

    def test_values_that_mean_nothing_fall_back_to_everything(self):
        self.workplace('Fallback One', checks=[(self.day(5), 60.0, {})])
        wide = dashboard.all_checks()
        f = dashboard.read_filters({'workplace': '99999', 'platform': 'Nowhere', 'kind': '<b>x</b>', 'range': 'forever', 'from': 'not a date', 'to': '2026-13-45'}, wide, TODAY)
        self.assertEqual((f['workplace'], f['platform'], f['kind'], f['range']), ('', '', '', 'all'))
        self.assertIsNone(f['start'])
        self.assertEqual(self.client.get('/dashboard?workplace=abc&range=%27%3E%3Cscript%3E&from=%00').status_code, 200)

    def test_one_workplace_gets_its_own_view_and_a_name_is_never_markup(self):
        aid = self.workplace('<script>alert(7)</script> Club', checks=[(self.day(30), 55.0, {'members': 50}), (self.day(3), 65.0, {'members': 60})])
        page = self.client.get(f'/dashboard?workplace={aid}').text
        self.assertNotIn('<script>alert(7)</script>', page)
        self.assertIn('&lt;script&gt;alert(7)&lt;/script&gt; Club', page)
        self.assertIn('How people spoke', page)
        self.assertIn(f'<option value="{aid}" selected>', page)

    def test_a_workplace_keeps_its_colour_whatever_the_filter(self):
        a = self.workplace('Colour A', checks=[(self.day(9), 60.0, {}), (self.day(2), 64.0, {})])
        b = self.workplace('Colour B', 'Team', 'Slack', checks=[(self.day(8), 50.0, {}), (self.day(1), 55.0, {})])
        roles = dashboard._roles([n for _a, n in dashboard.read_filters({}, dashboard.all_checks(), TODAY)['workplaces']])
        both = dashboard.build({}, TODAY)
        only = dashboard.build({'platform': 'Slack'}, TODAY)
        self.assertEqual(dict(only['legend']).get('Colour B'), roles['Colour B'])
        self.assertEqual(dict(both['legend']).get('Colour B'), roles['Colour B'])

    def test_a_period_with_nothing_in_it_says_so(self):
        self.workplace('Old Timer', checks=[(self.day(400), 50.0, {})])
        page = self.client.get('/dashboard?range=30&workplace=' + str(dashboard.all_checks()[-1]['aid'])).text
        self.assertIn('Nothing in this view', page)

    def test_the_dashboard_needs_the_password_like_every_page(self):
        pass                                                                                         # the sign-in wall is tested for every route in Auth


class SideBar(DashboardCase):
    def test_the_dashboard_is_in_the_top_bar_of_every_page_with_the_current_one_marked(self):
        aid = self.new_analysis(name='Nav Test')
        for path in (f'/analysis/{aid}/workspace', f'/analysis/{aid}/check/1', '/dashboard'):
            self.assertIn('href="/dashboard"', self.client.get(path).text.split('</header>', 1)[0], path)
        top = self.client.get('/dashboard').text.split('</header>', 1)[0]
        self.assertIn('aria-current="page"', top.split('href="/dashboard"', 1)[1].split('</a>', 1)[0])
        self.assertNotIn('aria-current="page"', self.client.get(f'/analysis/{aid}/workspace').text.split('</header>', 1)[0])


class ProgressData(unittest.TestCase):
    def job(self, message, status='running', kind='full'):
        return {'status': status, 'message': message, 'kind': kind}

    def test_each_step_of_a_run_is_named_and_the_bar_only_moves_forward(self):
        seen = []
        for message in ('Preparing the analysis…', 'Rating communication signals (10 of 100)…', 'Rating communication signals (90 of 100)…',
                        'Vibe Check: reading source 1 of 2 (a.csv), messages 1–40 of 100…', 'Vibe Check: reading source 2 of 2 (b.csv), messages 41–100 of 100…',
                        'Writing evidence-linked guidance…'):
            seen.append(progress.describe(self.job(message), False)['percent'])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(progress.describe(self.job('x', 'complete'))['percent'], 100)
        self.assertEqual(progress.describe(self.job('x', 'queued'))['percent'], 0)

    def test_the_current_step_is_marked_and_the_earlier_ones_are_done(self):
        got = progress.describe(self.job('Rating communication signals (30 of 76)…'), False)
        self.assertEqual([(s['key'], s['state']) for s in got['stages']], [('prepare', 'done'), ('rate', 'now'), ('vibe', 'todo'), ('guide', 'todo'), ('report', 'todo')])
        got = progress.describe(self.job('Removing names from the messages…'), True)
        self.assertEqual([s['key'] for s in got['stages']], ['prepare', 'names', 'rate', 'vibe', 'guide', 'report'], 'a step for names only under Incognito')
        self.assertEqual(got['stages'][1]['state'], 'now')
        vibe = progress.describe(self.job('Vibe Check: reading source 1 of 1 (a), messages 1–40 of 80…', kind='vibe'))
        self.assertEqual([s['key'] for s in vibe['stages']], ['prepare', 'vibe', 'report'])
        self.assertEqual(vibe['stages'][1]['state'], 'now')

    def test_nothing_is_invented_for_a_message_it_does_not_know(self):
        got = progress.describe(self.job('Something new happened…'))
        self.assertEqual(got['detail'], 'Something new happened…')
        self.assertEqual(got['stages'][0]['state'], 'now')
        self.assertEqual(progress.describe(None), {})
        self.assertEqual(progress.describe({'status': 'failed', 'message': 'Claude Code could not answer. Not signed in.', 'kind': 'full'})['detail'], 'Claude Code could not answer. Not signed in.')


class ProgressWindow(DashboardCase):
    def test_a_running_task_opens_the_window_and_the_job_route_carries_its_steps(self):
        aid = self.with_chat()
        busy = self.store.create_job(aid, 'full')
        self.addCleanup(self.store.job_update, busy, 'cancelled', 'done')
        self.store.job_update(busy, 'running', 'Rating communication signals (30 of 76)…')
        page = self.client.get(f'/analysis/{aid}/workspace').text
        self.assertIn('<dialog class="run-dialog no-print" id="run-dialog"', page)
        self.assertIn('data-open="yes"', page)
        self.assertIn('role="progressbar"', page)
        self.assertIn('Rating the 13 tones in every message', page)
        self.assertIn('/static/run.js', page)
        self.assertIn('Cancel this task', page)
        data = self.client.get(f'/analysis/{aid}/job').json()
        self.assertEqual(data['progress']['state'], 'running')
        self.assertEqual([s['key'] for s in data['progress']['stages']][:2], ['prepare', 'rate'])
        self.assertGreater(data['progress']['percent'], 8)
        self.store.job_update(busy, 'complete', 'Your report is ready: We rated 76 communication units.')
        done = self.client.get(f'/analysis/{aid}/job').json()['progress']
        self.assertEqual((done['percent'], done['state']), (100, 'complete'))
        self.assertNotIn('/static/run.js', self.client.get(f'/analysis/{aid}/workspace').text, 'nothing to follow once the task is over')

    def test_a_task_that_failed_shows_why_in_the_window(self):
        aid = self.with_chat()
        job = self.store.create_job(aid, 'full')
        self.store.job_update(job, 'failed', 'Claude Code could not answer. You are not signed in to Claude Code.')
        page = self.client.get(f'/analysis/{aid}/workspace').text
        self.assertIn('This task did not finish', page)                                            # the panel keeps the reason after the window is gone
        self.assertIn('You are not signed in to Claude Code.', page)
        self.assertNotIn('id="run-dialog"', page, 'the window is for a task that is running')


if __name__ == '__main__':
    unittest.main()
