"""Shared SQLite admission controls. No process-local counters or new dependencies."""
import hashlib
import math
import secrets
import sqlite3
import time
from contextlib import closing

SCHEMA="""
CREATE TABLE IF NOT EXISTS security_events (
    token TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS security_events_lookup ON security_events(scope,key,expires);
CREATE INDEX IF NOT EXISTS security_events_expiry ON security_events(expires);
CREATE TABLE IF NOT EXISTS security_sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started REAL NOT NULL, seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS security_sessions_user ON security_sessions(user_id);
"""
MAX_EVENTS=20000

def opaque(value):
    return hashlib.sha256(str(value).encode()).hexdigest()

def reserve(db_path,limits,stamp=None):
    """Atomically check and reserve [(scope,key,limit,window)]. Return (token,retry_seconds).

    Reservations count concurrent requests too. Failed authentication keeps its reservation;
    successful authentication removes only that attempt. Ordinary request reservations expire.
    """
    stamp=time.time() if stamp is None else stamp
    token=secrets.token_hex(16)
    with closing(sqlite3.connect(db_path,timeout=2)) as con:
        with con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM security_events WHERE expires<=?",(stamp,))
            retry=0
            for scope,key,limit,window in limits:
                row=con.execute("SELECT COUNT(*),MIN(expires) FROM security_events WHERE scope=? AND key=?",(scope,opaque(key))).fetchone()
                if row[0]>=limit: retry=max(retry,max(1,math.ceil(row[1]-stamp)))
            if retry: return None,retry
            if con.execute("SELECT COUNT(*) FROM security_events").fetchone()[0]+len(limits)>MAX_EVENTS:
                return None,60
            con.executemany("INSERT INTO security_events VALUES(?,?,?,?)",
                            [(token,scope,opaque(key),stamp+window) for scope,key,limit,window in limits])
    return token,0

def release(db_path,token,username=None):
    with closing(sqlite3.connect(db_path,timeout=2)) as con:
        with con:
            con.execute("DELETE FROM security_events WHERE token=?",(token,))
            if username is not None: clear_auth(con,username)

def clear_auth(con,username):
    """A verified password or admin reset clears account/pair history, never IP-wide failures."""
    con.execute("DELETE FROM security_events WHERE scope=? AND key=?",("auth-account",opaque(username.lower())))
    # Pair keys are opaque too, so identify the account-specific scope instead of enumerating IPs.
    con.execute("DELETE FROM security_events WHERE scope=?",("auth-pair-"+opaque(username.lower()),))
