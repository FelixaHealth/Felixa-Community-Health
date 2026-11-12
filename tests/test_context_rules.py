"""The context rules: a hostile word that is mentioned, aimed at oneself, or used to start a fake apology. Every sentence here is a reading we want to keep. Run with:

    python3 -m unittest tests.test_context_rules -v
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import analysis  # noqa: E402
import context_rules  # noqa: E402

CASES = [
    # (sentence, labels it must get)
    ("sorry I called you an idiot", {'mention'}),
    ("I am so sorry for calling you stupid yesterday", {'mention'}),
    ("I called you a jerk and I am sorry", {'mention'}),
    ("Sorry for saying that. I was angry.", {'mention'}),
    ("He called me an idiot in front of everyone", {'mention'}),
    ("Someone told me I was useless", {'mention'}),
    ("Is idiot a bad word?", {'mention'}),
    ("Is it okay to say that to a teammate?", {'mention'}),
    ("She said the word \"moron\" and nobody reacted", {'mention'}),
    ("You called me \"stupid\" last week", {'mention'}),
    ("I'm such an idiot, I forgot the meeting", {'self'}),
    ("I am so stupid, I deleted the file", {'self'}),
    ("ugh I feel like a total loser today", {'self'}),
    ("Stupid me, wrong channel", {'self'}),
    ("Sorry, but you are pathetic", {'fake_apology'}),
    ("No offense but you are slow", {'fake_apology'}),
    ("With all due respect, you have no idea what you are doing", {'fake_apology'}),
    ("Sorry you feel that way", {'fake_apology'}),
    ("Sorry if you are offended", {'fake_apology'}),
    ("Sorry, you are so sensitive", {'fake_apology'}),
    # these must stay as they are: no label
    ("You are an idiot", set()),
    ("I called you an idiot because you are one", set()),
    ("Shut up, you idiot", set()),
    ("I'm an idiot and you are a joke", set()),
    ("Sorry I snapped. Let us sort it out together.", set()),
    ("I'm so sorry about what happened", set()),
    ("Thanks for the update", set()),
    ("That sounds really hard", set()),
    ("", set()),
]


class Cases(unittest.TestCase):
    def test_every_sentence_gets_the_reading_we_want(self):
        for text, want in CASES:
            with self.subTest(text=text):
                self.assertEqual(context_rules.classify(text), want)


class Apply(unittest.TestCase):
    def test_the_hostile_tones_are_the_ones_measures_uses(self):
        import measures
        self.assertEqual(context_rules.OVERT, tuple(measures.OVERT))

    def ratings(self, **r):
        return {**dict.fromkeys(analysis.ATTRIBUTES, 0), **r}

    def test_a_mention_or_self_directed_word_does_not_count_as_open_hostility(self):
        r = self.ratings(Rude=3, Aggressive=2, Polite=2)
        for label in ('mention', 'self'):
            out = context_rules.apply(r, {label})
            self.assertEqual((out['Rude'], out['Aggressive'], out['Polite']), (0, 0, 2))
        self.assertEqual(r['Rude'], 3, 'the saved rating is not changed')

    def test_a_fake_apology_and_an_ordinary_message_keep_their_ratings(self):
        r = self.ratings(Rude=3)
        self.assertEqual(context_rules.apply(r, {'fake_apology'})['Rude'], 3)
        self.assertEqual(context_rules.apply(r, set())['Rude'], 3)

    def test_labels_round_trip(self):
        self.assertEqual(context_rules.labels_from(context_rules.encode({'mention', 'self'})), {'mention', 'self'})
        self.assertEqual(context_rules.labels_from(context_rules.encode(set())), set())
        self.assertEqual(context_rules.labels_from(None), set())


from tests.test_app import AppCase  # noqa: E402


class InTheApp(AppCase):
    def test_the_ratings_the_measures_read_follow_the_rules_and_the_saved_rating_stays(self):
        aid = self.new_analysis(name='Rules Co')
        texts = ['sorry I called you an idiot', 'You are an idiot', "I'm such an idiot", 'Sorry, but you are pathetic', 'Thanks for the update']
        self.store.add_units(aid, [{'text': t, 'source': 'general', 'timestamp': f'2026-11-09T09:0{i}:00'} for i, t in enumerate(texts)], 'TEST')
        with self.store.db() as c:
            for u in self.store.units_for(aid):
                for a in analysis.ATTRIBUTES:
                    c.execute('INSERT INTO unit_ratings(unit_id,attribute,rating,rationale) VALUES(?,?,?,?)', (u['id'], a, 3 if a == 'Rude' else 0, ''))
        rated = self.store.rated_units(aid)
        self.assertEqual([u['ratings']['Rude'] for u in rated], [0, 3, 0, 3, 3])
        self.assertTrue(all(set(u) == {'id', 'timestamp', 'ratings'} for u in rated), 'a unit stays an id, a date and ratings')
        texts_units = self.store.rated_text_units(aid)
        self.assertEqual([u['ratings']['Rude'] for u in texts_units], [0, 3, 0, 3, 3])
        self.assertEqual([u['labels'] for u in texts_units], [['mention'], [], ['self'], ['fake_apology'], []])
        with self.store.db() as c:
            self.assertEqual({r['rating'] for r in c.execute("SELECT rating FROM unit_ratings WHERE attribute='Rude'")}, {3}, 'what the model rated is kept as it was')


if __name__ == '__main__':
    unittest.main()
