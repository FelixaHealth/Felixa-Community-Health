"""Prints the first free port on this computer from 5050 up. The start scripts use it because macOS keeps port 5000 for AirPlay."""
import socket

for port in range(5050, 5100):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            continue
    print(port)
    break
else:
    raise SystemExit('No free port between 5050 and 5099.')
