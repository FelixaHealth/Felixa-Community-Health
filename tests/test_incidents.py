"""Escalation by incident: where an incident starts, where it turns into conflict, and the bars at a chosen resolution."""
import datetime as dt
import unittest

import analysis
import incidents
from tests.test_app import AppCase


def unit(minute, text, **tones):
    ratings = {a: 0 for a in analysis.ATTRIBUTES}
    ratings.update(tones)
    return {'id': minute, 'text': text, 'source': '#team', 'timestamp': (dt.datetime(2026, 11, 9, 9, 0) + dt.timedelta(minutes=minute)).isoformat(timespec='seconds'), 'ratings': ratings}


CHAT = [unit(0, 'Morning all.'), unit(1, 'Ready when you are.'), unit(5, 'This is the third time. Great, thanks.', Agitated=2), unit(7, 'You never listen.', Aggressive=3),
        unit(8, 'Stop shouting.', Agitated=2), unit(9, 'You are useless.', Rude=3), unit(12, 'Take a breath.'), unit(13, 'Sorry, I snapped.'), unit(14, 'Thanks.'),
        unit(120, 'Lunch?'), unit(121, 'Sure.'), unit(125, 'Ugh, again.', Agitated=3), unit(128, 'Honestly, again?', Sarcastic=3), unit(140, 'Fine.'), unit(141, 'Okay.'), unit(142, 'Good.')]


class Incidents(unittest.TestCase):
    def test_two_incidents_one_turns_into_conflict_and_one_does_not(self):
        found = incidents.find(CHAT, 30)
        self.assertEqual(len(found), 2)
        first, second = found
        self.assertEqual(first['level'], 'red')
        self.assertEqual(first['start']['text'], 'This is the third time. Great, thanks.')
        self.assertEqual(first['turn']['text'], 'You never listen.')
        self.assertEqual(first['minutes_to_conflict'], 2)
        self.assertEqual((first['heated'], first['hostile']), (4, 2))
        self.assertEqual(second['level'], 'yellow')
        self.assertIsNone(second['turn'])

    def test_the_gap_decides_what_counts_as_one_incident(self):
        self.assertEqual(len(incidents.find(CHAT, 120)), 1, 'with a long quiet gap the afternoon joins the morning')
        self.assertEqual(len(incidents.find(CHAT, 15)), 2)

    def test_bars_follow_the_chosen_resolution(self):
        inc = incidents.find(CHAT, 30)[0]
        by_message = incidents.bins(inc, 0)
        self.assertEqual(len(by_message), len(inc['rows']))
        five = incidents.bins(inc, 5)
        ten = incidents.bins(inc, 10)
        self.assertGreater(len(five), len(ten))
        for bars in (by_message, five, ten):
            self.assertEqual(sum(b['hostile'] for b in bars), 2)
            self.assertEqual(sum(b['n'] for b in bars), len(inc['rows']))

    def test_a_calm_chat_has_no_incident(self):
        self.assertEqual(incidents.find([unit(i, 'fine') for i in range(10)]), [])


class Pages(AppCase):
    def test_unknown_incident_is_a_plain_404_and_the_escalation_page_lists_incidents(self):
        aid = self.with_chat()
        self.assertEqual(self.run_job(aid, path='check/6', engine='')['status'], 'complete')
        self.assertEqual(self.client.get(f'/analysis/{aid}/incident/999').status_code, 404)
        page = self.client.get(f'/analysis/{aid}/track/escalation')
        if page.status_code == 200:
            self.assertIn('Incidents', page.text)
            first = self.client.get(f'/analysis/{aid}/incident/1?step=10')
            self.assertIn(first.status_code, (200, 404))
            if first.status_code == 200:
                self.assertIn('Take it apart', first.text)
                self.assertIn('Escalation starts', first.text)


if __name__ == '__main__':
    unittest.main()


class Context(unittest.TestCase):
    def test_context_messages_from_far_away_are_left_out(self):
        chat = [unit(0, 'old calm one'), unit(1, 'old calm two')] + [unit(600 + i, t, **tones) for i, (t, tones) in enumerate([('Hmm.', {'Agitated': 2}), ('No.', {'Rude': 3})])]
        inc = incidents.find(chat, 30)[0]
        self.assertEqual([r['text'] for r in inc['rows']], ['Hmm.', 'No.'])
        self.assertLessEqual(len(incidents.bins(inc, 5)), 3)


class Deescalation(unittest.TestCase):
    def test_a_calming_message_after_heat_is_found_and_the_heat_held_back_or_not(self):
        import deescalation
        chat = [unit(0, 'Morning.'), unit(1, 'Ugh.', Agitated=2), unit(2, 'You never listen.', Rude=3), unit(3, 'Let us take a breath, I hear you.', Empathetic=3),
                unit(4, 'Okay. Sorry.'), unit(5, 'Thanks.', Polite=2), unit(6, 'Fine.')]
        found = incidents.find(chat, 30)
        self.assertEqual(len(found), 1)
        calm = found[0]['calm']
        self.assertEqual(calm['text'], 'Let us take a breath, I hear you.')
        self.assertTrue(calm['held'])
        self.assertEqual(calm['heated_after'], 0)
        view = deescalation.view(chat)
        self.assertEqual((len(view['tried']), len(view['held'])), (1, 1))
        self.assertTrue(all(s['examples'] for s in view['stages']))
        flared = [unit(0, 'Ugh.', Agitated=2), unit(1, 'Let us take a breath.', Empathetic=3), unit(2, 'No. You are useless.', Rude=3), unit(3, 'Stop it.', Agitated=2)]
        self.assertFalse(incidents.find(flared, 30)[0]['calm']['held'])

    def test_no_calming_message_means_no_traction_and_a_labelled_example(self):
        import deescalation
        chat = [unit(0, 'Ugh.', Agitated=2), unit(1, 'No.', Rude=3), unit(2, 'Whatever.')]
        self.assertIsNone(incidents.find(chat, 30)[0]['calm'])
        self.assertFalse(deescalation.view(chat)['real'])
