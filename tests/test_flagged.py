"""The Flagged messages page: every flagged message, filters, CSV."""
import re
import unittest

import flagged
from tests.test_app import AppCase


VIBE = {'flagged': [
    {'message': 'You are useless', 'type': 'insult', 'severity': 6, 'source': 'a.txt', 'timestamp': '2026-03-05T10:00:00', 'index': 1},
    {'message': '=cmd|x', 'type': 'threat', 'severity': 9, 'source': 'b.txt', 'timestamp': '2026-03-06T10:00:00', 'index': 2},
    {'message': 'meh', 'type': 'profanity', 'severity': 2, 'source': 'a.txt', 'timestamp': None, 'index': 3}]}


class Build(unittest.TestCase):
    def test_nothing_is_cut_off_and_the_most_severe_comes_first(self):
        view = flagged.build(VIBE)
        self.assertEqual([r['severity'] for r in view['rows']], [9, 6, 2])
        self.assertEqual((view['total'], view['matching'], view['serious']), (3, 3, 1))
        many = {'flagged': [{'message': f'm{i}', 'type': 'insult', 'severity': 5, 'index': i} for i in range(60)]}
        self.assertEqual(flagged.build(many)['matching'], 60)
        self.assertEqual(len(flagged.build(many, page=3)['rows']), 10)
        self.assertEqual(len(flagged.build(many)['all']), 60)

    def test_each_filter_narrows_the_list(self):
        self.assertEqual([r['message'] for r in flagged.build(VIBE, kind='insult')['rows']], ['You are useless'])
        self.assertEqual(flagged.build(VIBE, level='7')['matching'], 1)
        self.assertEqual(flagged.build(VIBE, level='4')['matching'], 2)
        self.assertEqual(flagged.build(VIBE, source='b.txt')['matching'], 1)
        self.assertEqual(flagged.build(VIBE, q='MEH')['matching'], 1)
        self.assertTrue(flagged.build(VIBE, q='zzz')['filtered'])


class Pages(AppCase):
    def test_the_page_and_the_csv_follow_the_filters_and_guard_formulas(self):
        aid = self.with_chat()
        self.assertEqual(self.client.get(f'/analysis/{aid}/flagged').status_code, 404)           # no results yet
        self.run_job(aid, path='check/6', engine='')
        page = self.client.get(f'/analysis/{aid}/flagged')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Flagged messages', page.text)
        self.assertEqual(self.client.get(f'/analysis/{aid}/flagged?level=7&sort=time&page=9').status_code, 200)
        csv = self.client.get(f'/analysis/{aid}/flagged.csv')
        self.assertEqual(csv.status_code, 200)
        self.assertTrue(csv.text.lstrip('﻿').startswith('Severity,Type,Source,Date,Flagged message'))
        self.assertIsNone(re.search(r'(?m)^[^,]*,[^,]*,[^,]*,[^,]*,[=+@]', csv.text))


if __name__ == '__main__':
    unittest.main()
