"""Run the real app with the fake AI, for the browser-level tests in tests/frontend.
python tests/fake_server.py 5056"""
import sys

import helpers

import os

_, fake = helpers.fresh()
fake.latency = float(os.environ.get("FAKE_AI_LATENCY", "0"))   # make the fake AI as slow as the real one, for load tests
helpers.appmod.app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 5056, use_reloader=False)
