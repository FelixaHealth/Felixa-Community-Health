"""The results on a page of their own with tabs, the category and tone pages, and the price of a check. Run with:

    python3 -m unittest tests.test_results_pages -v
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_app import AppCase  # noqa: E402

import analysis  # noqa: E402
import prices  # noqa: E402
import workplace  # noqa: E402


class Pages(AppCase):
    def ready(self):
        aid = self.with_chat(name='Pages Guild')
        job = self.run_job(aid)
        self.assertEqual(job['status'], 'complete', job)
        return aid

    def test_the_workplace_page_shows_the_headline_and_the_results_have_a_page_with_tabs(self):
        aid = self.ready()
        work = self.client.get(f'/analysis/{aid}/workspace').text
        self.assertIn(f'href="/analysis/{aid}/results"', work)
        self.assertIn('Open the full results', work)
        self.assertNotIn('id="over-time"', work, 'the parts of the results are on the results page, not here')
        page = self.client.get(f'/analysis/{aid}/results').text
        bar = page.split('data-tabs>', 1)[1].split('</nav>', 1)[0]
        for tab in ('Overview', 'Alerts', 'Measures over time', 'Badges', 'Signal profile'):
            self.assertIn(f'<span>{tab}</span>', bar, tab)
        for panel in ('tab-overview', 'tab-alerts', 'tab-measures', 'tab-badges', 'tab-signals'):
            self.assertIn(f'id="{panel}"', page, panel)
            self.assertIn(f'href="#{panel}"', bar, panel)
        self.assertIn(f'href="/analysis/{aid}/category/Positive"', page)
        self.assertEqual(self.client.get('/analysis/999999/results').status_code, 404)

    def test_a_category_page_lists_its_messages_and_a_tone_page_its_own(self):
        aid = self.ready()
        rated = {m['attribute']: m for m in analysis.metrics_for(aid)}
        for family, members in analysis.FAMILIES:
            r = self.client.get(f'/analysis/{aid}/category/{family}')
            self.assertEqual(r.status_code, 200, family)
            self.assertIn(f'<h1>{family}</h1>', r.text)
            self.assertIn('Over time', r.text)
        tone = next(a for a in analysis.ATTRIBUTES if rated[a]['detection_rate'] > 0)
        page = self.client.get(f'/analysis/{aid}/tone/{tone}').text
        self.assertIn(f'<h1>{tone}</h1>', page)
        self.assertIn('class="comment ', page, 'a message that shows the tone is listed')
        self.assertIn(f'/category/{analysis.family_of(tone)}', page, 'a path back to its category')
        self.assertIn(f'{tone} ', page)
        for bad in ('/category/Nope', '/tone/Nope'):
            self.assertEqual(self.client.get(f'/analysis/{aid}{bad}').status_code, 404, bad)
        self.assertEqual(self.client.get(f'/analysis/{aid}/tone/{tone}?page=999').status_code, 200, 'a page past the end shows the last one')

    def test_every_message_of_a_tone_is_listed_once_over_the_pages(self):
        import detail
        aid = self.ready()
        tone = max(analysis.metrics_for(aid), key=lambda m: m['detection_rate'])['attribute']
        rows, total, pages, _page = detail.comments(aid, [tone], 1)
        seen = list(rows)
        for n in range(2, pages + 1):
            seen += detail.comments(aid, [tone], n)[0]
        self.assertEqual(len(seen), total)
        self.assertTrue(all(any(a == tone for a, _r in r['tones']) for r in seen))
        self.assertEqual([r['top'] for r in seen], sorted((r['top'] for r in seen), reverse=True), 'strongest first')

    def test_the_timeline_says_so_when_there_are_no_dates(self):
        import detail
        aid = self.new_analysis(name='Undated')
        self.client.post(f'/analysis/{aid}/add-text', data={'raw_text': 'thanks a lot\nyou idiot'})
        html, note = detail.chart(aid, ['Polite'], 'Polite')
        self.assertEqual(html, '')
        self.assertIn('no dates', note)


class Prices(unittest.TestCase):
    def parts(self, n=1000):
        return workplace.calls_for(n, 0, 1)

    def test_free_engines_have_no_price_and_paid_ones_do(self):
        for engine in ('qwen2.5:7b', 'rules:words'):
            self.assertIsNone(prices.per_million(engine))
            self.assertEqual(prices.text(engine, self.parts()), '')
        haiku = prices.text('claude:claude-haiku-5-5', self.parts())
        opus = prices.text('claude:claude-opus-5-5', self.parts())
        self.assertRegex(haiku, r'^About \$|^Under \$')
        self.assertTrue(float(re.findall(r'\$([\d,.]+)', opus)[0].replace(',', '')) > float(re.findall(r'\$([\d,.]+)', haiku)[0].replace(',', '')), 'the bigger model costs more')

    def test_the_price_grows_with_the_number_of_messages_and_has_a_range(self):
        small, big = prices.cost('claude:claude-sonnet-5-5', self.parts(100)), prices.cost('claude:claude-sonnet-5-5', self.parts(2000))
        self.assertLess(small[1], big[0])
        self.assertLess(big[0], big[1])
        self.assertIsNone(prices.cost('claude:claude-sonnet-5-5', self.parts(0)))

    def test_the_login_is_priced_like_the_model_of_its_kind(self):
        self.assertEqual(prices.per_million('claudecode:sonnet'), prices.per_million('claude:claude-sonnet-5-5'))
        self.assertIn('2026', prices.as_of_note())

    def test_the_engine_options_carry_the_price_text(self):
        opts = workplace.engine_options([], 'qwen2.5:7b', 1000, 0, 1, [('claude-haiku-5-5', 'Claude Haiku 5.5', 'x', 3.0)], True, [('sonnet', 'Claude Sonnet', 'x', 9.0)], True,
                                         [('gpt-4o-mini', 'GPT-4o mini', 'x', 3.0)], True, rules=True)
        by = {o['name']: o for o in opts}
        self.assertEqual(by['qwen2.5:7b']['cost'], 'Free')
        self.assertEqual(by['rules:words']['cost'], 'Free')
        self.assertTrue(by['claude:claude-haiku-5-5']['cost'].startswith(('About', 'Under')))
        self.assertEqual(by['claudecode:sonnet']['cost'], 'Included in your plan')
        self.assertIn('at API prices', by['claudecode:sonnet']['cost_note'])
        self.assertTrue(by['openai:gpt-4o-mini']['cost'])


class PriceAccuracy(unittest.TestCase):
    def test_a_tiny_check_reads_in_plain_words(self):
        for engine in ('claude:claude-haiku-5-5', 'claudecode:haiku', 'openai:gpt-4o-mini'):
            text = prices.text(engine, workplace.calls_for(1, 0, 1))
            self.assertNotIn('under', text.lower().replace('under $', ''), text)
            self.assertRegex(text, r'^(About|Up to|Under) \$', text)

    def test_long_messages_cost_more_than_short_ones(self):
        parts = workplace.calls_for(500, 0, 1)
        short, long_ = prices.cost('claude:claude-sonnet-5-5', parts, 80), prices.cost('claude:claude-sonnet-5-5', parts, 1500)
        self.assertGreater(long_[0], short[1] * 0.9)
        self.assertGreater(long_[0], short[0] * 2)
        self.assertEqual(prices.cost('claude:claude-sonnet-5-5', parts), prices.cost('claude:claude-sonnet-5-5', parts, prices.DEFAULT_CHARS))
        self.assertEqual(prices.cost('claude:claude-sonnet-5-5', parts, 10 ** 6), prices.cost('claude:claude-sonnet-5-5', parts, prices.MAX_CHARS), 'capped at what is sent')

    def test_the_options_use_the_average_length(self):
        args = ([], 'qwen2.5:7b', 500, 0, 1, [('claude-sonnet-5-5', 'Claude Sonnet 5.5', 'x', 6.0)], True)
        a = {o['name']: o for o in workplace.engine_options(*args, avg_chars=60)}['claude:claude-sonnet-5-5']['cost']
        b = {o['name']: o for o in workplace.engine_options(*args, avg_chars=2000)}['claude:claude-sonnet-5-5']['cost']
        self.assertNotEqual(a, b)


class AverageAndDefaults(AppCase):
    def test_the_average_length_counts_what_is_stored(self):
        aid = self.new_analysis(name='Avg')
        self.assertIsNone(self.store.average_chars(aid))
        self.client.post(f'/analysis/{aid}/add-text', data={'raw_text': 'a' * 100 + '\n' + 'b' * 300})
        self.assertAlmostEqual(self.store.average_chars(aid), 200.0, delta=1.0)

    def test_the_price_in_the_summary_matches_the_card_for_the_same_engine(self):
        aid = self.with_chat(name='Match')
        self.store.update_analysis(aid, engine='claude:claude-haiku-5-5', cloud_ok=1)
        page = self.client.get(f'/analysis/{aid}/check/6').text
        card = re.search(r'value="claude:claude-haiku-5-5".*?<b class="cost">([^<]+)</b>', page, re.S).group(1)
        row = re.search(r'<dt>Price</dt><dd[^>]*>([^<]+)</dd>', page).group(1)
        self.assertEqual(card, row)

    def test_a_check_that_ran_on_the_local_model_does_not_come_back_as_the_login(self):
        aid = self.with_chat(name='Local')
        self.store.update_analysis(aid, engine=None, model_used='qwen2.5:7b')
        page = self.client.get(f'/analysis/{aid}/check/6').text
        self.assertRegex(page, r'value="qwen2\.5:7b"\s+checked')


class EngineStepPrices(AppCase):
    def test_the_engine_step_shows_a_price_on_every_engine_and_the_date_of_the_prices(self):
        aid = self.with_chat()
        page = self.client.get(f'/analysis/{aid}/check/6').text
        self.assertGreaterEqual(page.count('class="cost'), 4)
        self.assertIn('Claude prices as of Nov 3, 2026', page)
        self.assertIn('Free</b>', page)
        self.assertIn('>Price<', page.replace('<dt>', '>').replace('</dt>', '<'))


if __name__ == '__main__':
    unittest.main()
