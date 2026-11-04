"""Updating from inside the app."""
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import config
import updater


def zip_of(files) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, text in files.items():
            z.writestr('Felixa-Community-Health-main/' + name, text)
    return out.getvalue()


class Updater(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app, self.state = root / 'app', root / 'state'
        (self.app).mkdir()
        (self.state / 'data').mkdir(parents=True)
        (self.app / 'app.py').write_text('old')
        (self.app / 'felixa.db').write_text('my data')
        (self.state / 'version').write_text('a' * 40)
        for patch in (mock.patch.object(config, 'BASE_DIR', self.app), mock.patch.object(config, 'data_dir', return_value=self.state / 'data')):
            patch.start()
            self.addCleanup(patch.stop)
        updater._cache.update(at=0.0, remote='', error='')

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_newer_version_is_found_and_installed_without_touching_the_data(self):
        self.assertTrue(updater.available())
        with mock.patch.object(updater, '_get', return_value=b'b' * 40):
            self.assertTrue(updater.check()['newer'])
            self.assertTrue(updater.cached_newer())
            version = updater.install(download=lambda: zip_of({'app.py': 'new', 'felixa.db': 'EMPTY', 'static/x.css': 'x'}))
        self.assertEqual(version, 'bbbbbbb')
        self.assertEqual((self.app / 'app.py').read_text(), 'new')
        self.assertEqual((self.app / 'felixa.db').read_text(), 'my data')
        self.assertEqual((self.app / 'static' / 'x.css').read_text(), 'x')
        self.assertEqual((self.state / 'version').read_text(), 'b' * 40)

    def test_no_internet_changes_nothing_and_says_so(self):
        with mock.patch.object(updater, '_get', side_effect=OSError('down')):
            result = updater.check(force=True)
            self.assertFalse(result['newer'])
            self.assertIn('Could not reach GitHub', result['error'])
            with self.assertRaises(RuntimeError):
                updater.install()
        self.assertEqual((self.app / 'app.py').read_text(), 'old')

    def test_a_download_that_is_not_felixa_is_refused(self):
        with mock.patch.object(updater, '_get', return_value=b'b' * 40):
            with self.assertRaises(RuntimeError):
                updater.install(download=lambda: zip_of({'readme.txt': 'hi'}))
        self.assertEqual((self.app / 'app.py').read_text(), 'old')

    def test_a_copy_not_run_by_the_launcher_does_not_offer_updates(self):
        (self.state / 'version').unlink()
        self.assertFalse(updater.available())


if __name__ == '__main__':
    unittest.main()
