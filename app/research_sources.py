"""Compatibility import: implementation lives in Personal Radar."""
import sys
from personal_radar_connectors import public_http as _client
sys.modules[__name__] = _client
