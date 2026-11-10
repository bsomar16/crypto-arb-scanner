"""Configuration loader for crypto-arb-scanner."""
from botutil import load_json

def load_cfg():
    return load_json("config.json", {}) or {}
