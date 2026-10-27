"""Status source "codex-sqlite": Codex's own thread index (~/.codex/state_5.sqlite):
thread id -> cwd, title, rollout path, updated_at, in ctx.codex_threads."""
import os
import sqlite3

from . import Source, register_source
from ._common import HOME

CODEX_DB = os.path.join(HOME, ".codex/state_5.sqlite")


@register_source
class CodexSqliteSource(Source):
    name = "codex-sqlite"
    interval = 5.0

    def poll(self, ctx):
        if not os.path.exists(CODEX_DB):
            return
        try:
            db = sqlite3.connect(f"file:{CODEX_DB}?mode=ro", uri=True, timeout=1)
            rows = db.execute("select id, cwd, title, rollout_path, updated_at from threads "
                              "where archived = 0 order by updated_at desc limit 300").fetchall()
            db.close()
        except sqlite3.Error:
            return
        ctx.codex_threads = {r[0]: {"cwd": r[1], "title": r[2], "rollout_path": r[3], "updated_at": r[4]} for r in rows}
