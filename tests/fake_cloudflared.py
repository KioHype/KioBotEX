"""Imita o cloudflared nos testes. FAKE_MODE: ok | crash | hang."""
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
print("INF Thank you for trying Cloudflare Tunnel...", flush=True)
print("INF Requesting new quick Tunnel on trycloudflare.com...", flush=True)
if mode == "crash":
    print('failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": no such host', flush=True)
    sys.exit(1)
if mode == "hang":
    time.sleep(60)
print("INF |  Your quick Tunnel has been created! Visit it at:                |", flush=True)
print("INF |  https://test-fake-words-here.trycloudflare.com                  |", flush=True)
time.sleep(600)
