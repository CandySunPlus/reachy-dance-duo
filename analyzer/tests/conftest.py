"""Keep tests off /data and out of the real credential file."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_RUNTIME = Path(tempfile.mkdtemp(prefix="dance-duo-tests-"))
os.environ["AUDIO_CACHE_DIR"] = str(_RUNTIME / "audio")
os.environ["QQ_CREDENTIAL_PATH"] = str(_RUNTIME / "qq" / "credential.json")
os.environ["SITE_DIR"] = str(_RUNTIME / "site")
