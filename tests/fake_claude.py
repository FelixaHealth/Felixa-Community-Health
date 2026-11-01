"""A stand-in for the `claude` program (Claude Code), for tests. It answers `claude -p --output-format json ...` the way the real one does: the
instructions come in --system-prompt, the text on stdin, the answer goes out as one JSON result. The answer itself comes from the fake model
server (tests/fake_ollama.py) over its Claude-shaped route, and every call is written to the file FAKE_CLAUDE_LOG so a test can see how it was run.
"""
import json
import os
import sys
import urllib.request


def main():
    args = sys.argv[1:]
    value = lambda flag: args[args.index(flag) + 1] if flag in args else None      # noqa: E731
    text = sys.stdin.read()
    with open(os.environ['FAKE_CLAUDE_LOG'], 'a') as log:
        log.write(json.dumps({'args': args, 'cwd': os.getcwd(), 'files_in_cwd': sorted(os.listdir('.')), 'stdin': text,
                              'has_api_key': any(k in os.environ for k in ('ANTHROPIC_API_KEY', 'FELIXA_ANTHROPIC_API_KEY'))}) + '\n')
    mode = os.environ.get('FAKE_CLAUDE_MODE', 'ok')
    if mode == 'old' and '--tools' in args:                                    # a Claude Code too old to know --tools
        print("error: unknown option '--tools'", file=sys.stderr)
        sys.exit(1)
    if mode == 'notsignedin':
        print(json.dumps({'type': 'result', 'is_error': True, 'result': 'Not logged in \u00b7 Please run /login'}))
        return
    if mode == 'limit':
        print(json.dumps({'type': 'result', 'is_error': True, 'result': 'Claude usage limit reached. Your limit will reset at 5pm.'}))
        return
    if mode == 'crash':
        print('not signed in', file=sys.stderr)
        sys.exit(1)
    if mode == 'error':
        print(json.dumps({'type': 'result', 'is_error': True, 'result': 'Invalid API key'}))
        return
    body = {'model': value('--model'), 'system': value('--system-prompt'), 'messages': [{'role': 'user', 'content': text}]}
    request = urllib.request.Request(os.environ['FAKE_CLAUDE_URL'] + '/v1/messages', data=json.dumps(body).encode(),
                                     headers={'content-type': 'application/json', 'x-api-key': os.environ['FAKE_CLAUDE_KEY']})
    with urllib.request.urlopen(request) as response:
        answer = json.load(response)
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': answer['content'][0]['text']}))


main()
