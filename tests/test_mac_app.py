"""The double-click Mac launchers: Felixa.app starts the app with no Terminal window, Stop Felixa.app stops it. Run with:

    python3 -m unittest tests.test_mac_app -v
"""
import os
import pathlib
import plistlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APPS = {'Felixa.app': 'Felixa', 'Stop Felixa.app': 'StopFelixa', 'Felixa on Phone.app': 'FelixaOnPhone', 'Felixa Always On.app': 'FelixaAlwaysOn'}


class MacApps(unittest.TestCase):
    def test_each_app_has_a_plist_an_icon_and_an_executable_that_parses(self):
        for app, exe in APPS.items():
            plist = plistlib.loads((ROOT / app / 'Contents' / 'Info.plist').read_bytes())
            self.assertEqual((plist['CFBundleExecutable'], plist['CFBundlePackageType']), (exe, 'APPL'), app)
            self.assertTrue((ROOT / app / 'Contents' / 'Resources' / f'{plist["CFBundleIconFile"]}.icns').is_file(), app)
            path = ROOT / app / 'Contents' / 'MacOS' / exe
            self.assertTrue(os.access(path, os.X_OK), f'{app} must be executable')
            self.assertEqual(subprocess.run(['bash', '-n', str(path)]).returncode, 0, app)

    def test_git_keeps_the_executable_bit_so_the_zip_download_still_opens(self):
        out = subprocess.run(['git', 'ls-files', '-s', '--', 'Felixa.app', 'Stop Felixa.app', 'Felixa on Phone.app', 'Felixa Always On.app'], cwd=ROOT, capture_output=True, text=True).stdout
        modes = {line.split('\t')[1]: line.split()[0] for line in out.splitlines()}
        for app, exe in APPS.items():
            self.assertEqual(modes.get(f'{app}/Contents/MacOS/{exe}'), '100755', app)

    def test_the_phone_launcher_shares_on_the_wifi_and_allows_only_that_address(self):
        text = (ROOT / 'Felixa.app' / 'Contents' / 'MacOS' / 'Felixa').read_text()
        for needle in ('FELIXA_SHARE', 'FELIXA_HOST=0.0.0.0', 'FELIXA_ALLOWED_HOSTS', 'ipconfig getifaddr', 'Copy address'):
            self.assertIn(needle, text, needle)
        self.assertIn('FELIXA_SHARE=1', (ROOT / 'Felixa on Phone.app' / 'Contents' / 'MacOS' / 'FelixaOnPhone').read_text())

    def test_the_launcher_updates_itself_from_github_and_keeps_your_data_elsewhere(self):
        text = (ROOT / 'Felixa.app' / 'Contents' / 'MacOS' / 'Felixa').read_text()
        for needle in ('commits/main', 'archive/refs/heads/main.zip', 'FELIXA_NO_UPDATE', 'FELIXA_UPDATED', 'exec "$ROOT/Felixa.app/Contents/MacOS/Felixa"', 'Application Support/Felixa'):
            self.assertIn(needle, text, needle)
        self.assertLess(text.index('commits/main'), text.index('Python 3.10 or newer'), 'it updates before it starts')

    def test_always_on_installs_a_login_item_that_starts_and_updates_the_app_quietly(self):
        text = (ROOT / 'Felixa Always On.app' / 'Contents' / 'MacOS' / 'FelixaAlwaysOn').read_text()
        for needle in ('LaunchAgents', 'RunAtLoad', 'StartInterval', 'FELIXA_NO_BROWSER', 'launchctl bootout', 'Turn off'):
            self.assertIn(needle, text, needle)
        self.assertIn('browse()', (ROOT / 'Felixa.app' / 'Contents' / 'MacOS' / 'Felixa').read_text())

    def test_the_launcher_runs_from_a_private_copy_so_a_locked_or_deleted_download_does_not_matter(self):
        text = (ROOT / 'Felixa.app' / 'Contents' / 'MacOS' / 'Felixa').read_text()
        for needle in ('APP_HOME="$STATE/app"', 'AppTranslocation', 'FELIXA_BOOTSTRAPPED', 'FELIXA_PREPARE_ONLY', 'com.apple.quarantine'):
            self.assertIn(needle, text, needle)
        self.assertLess(text.index('FELIXA_BOOTSTRAPPED'), text.index('commits/main'), 'it copies itself before it updates')
        always = (ROOT / 'Felixa Always On.app' / 'Contents' / 'MacOS' / 'FelixaAlwaysOn').read_text()
        self.assertIn('Application Support/Felixa/app/Felixa.app', always)

    def test_the_launcher_names_what_it_needs_and_does_not_open_a_terminal(self):
        text = (ROOT / 'Felixa.app' / 'Contents' / 'MacOS' / 'Felixa').read_text()
        for needle in ('Application Support/Felixa', 'FELIXA_DATA_DIR', '.local/bin', 'Python.framework', 'FELIXA_NO_LOCAL_MODEL=1', '/health', 'nohup', 'display dialog'):
            self.assertIn(needle, text, needle)
        self.assertNotIn('Terminal', text.replace('No Terminal window', ''))


if __name__ == '__main__':
    unittest.main()
