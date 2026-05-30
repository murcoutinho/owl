"""Coder session-id persistence.

Owl stashes a UUID in ``plan_work_dir/coder_session_id`` so the fix phase can
``claude --resume <uuid>`` into the same conversation the coder used, keeping
file-read context warm across iterations. Ported from
``get_or_create_coder_session_id`` / ``load_coder_session_id``
.
"""

from __future__ import annotations

import uuid
from pathlib import Path


def get_or_create(session_file: Path) -> str:
    """Return the existing session id, or create, persist, and return a new one."""
    if session_file.exists():
        first_line = session_file.read_text().splitlines()
        if first_line and first_line[0].strip():
            return first_line[0].strip()
    sid = str(uuid.uuid4())
    session_file.write_text(sid + "\n")
    return sid


def load(session_file: Path) -> str | None:
    """Return the persisted session id, or None if absent/empty."""
    if not session_file.exists():
        return None
    lines = session_file.read_text().splitlines()
    if lines and lines[0].strip():
        return lines[0].strip()
    return None
