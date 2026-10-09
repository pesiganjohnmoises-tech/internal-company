"""MFA storage and enrollment helpers. Import never initializes the app.

Generate a private key file: python -m utils.mfa keygen --output <private-path>
Migration inspection: python -m utils.mfa migrate --database <existing-db>
Apply requires --apply --backup <new-backup-path> and explicit key configuration.
"""
import argparse
import base64
import binascii
import json
import hashlib
import hmac
import os
from pathlib import Path
import re
import sqlite3
import secrets
import time
from contextlib import closing

from cryptography.fernet import Fernet, InvalidToken
import pyotp
import qrcode
import qrcode.image.svg

SCHEMA_VERSION=1
TABLES={
    "mfa_schema_versions": "version INTEGER PRIMARY KEY CHECK(version=1)",
    "user_mfa": """user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
        encrypted_secret TEXT, enabled_at TEXT,
        last_used_step INTEGER NOT NULL DEFAULT -1 CHECK(last_used_step>=-1),
        security_version INTEGER NOT NULL DEFAULT 0 CHECK(security_version>=0),
        recovery_required INTEGER NOT NULL DEFAULT 0 CHECK(recovery_required IN (0,1)),
        CHECK((encrypted_secret IS NULL AND enabled_at IS NULL) OR
              (encrypted_secret IS NOT NULL AND enabled_at IS NOT NULL))""",
    "mfa_challenges": """token_hash TEXT PRIMARY KEY CHECK(length(token_hash)=64),
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL CHECK(purpose IN ('login','enroll','reauth','reset')),
        password_version TEXT NOT NULL, security_version INTEGER NOT NULL,
        created_at REAL NOT NULL, expires_at REAL NOT NULL,
        encrypted_pending_secret TEXT,
        CHECK(expires_at>created_at),
        CHECK(encrypted_pending_secret IS NULL OR purpose='enroll')""",
    "mfa_recovery_codes": """user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL CHECK(length(code_hash)=64), created_at TEXT NOT NULL,
        used_at TEXT, PRIMARY KEY(user_id,code_hash)""",
    "mfa_session_proofs": """token_hash TEXT PRIMARY KEY REFERENCES security_sessions(token_hash) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        security_version INTEGER NOT NULL CHECK(security_version>=0),
        verified INTEGER NOT NULL CHECK(verified IN (0,1))""",
}
INDEXES=(
    "CREATE INDEX IF NOT EXISTS mfa_challenges_expiry ON mfa_challenges(expires_at)",
    "CREATE INDEX IF NOT EXISTS mfa_challenges_user ON mfa_challenges(user_id)",
)
ENROLLMENT_SECONDS=10*60
LOGIN_SECONDS=5*60
ISSUER="Internal Company Directory"


class MFAEnrollmentError(ValueError):
    """Expired, invalid, or unavailable enrollment; never includes secrets."""


class MFAChallengeError(ValueError):
    """Invalid or expired password-verified login; contains no factor values."""


def _user(con,user_id):
    columns=("id","username","first_name","last_name","role","status","password_hash")
    row=con.execute("SELECT "+",".join(columns)+" FROM users WHERE id=?",(user_id,)).fetchone()
    return dict(zip(columns,row)) if row else None


def credential_version(user):
    return hashlib.sha256((user["password_hash"]+"|"+user["role"]+"|"+user["status"]).encode()).hexdigest()


def account_state(con,user):
    row=con.execute("SELECT required,encrypted_secret,security_version,last_used_step,recovery_required FROM user_mfa WHERE user_id=?",(user["id"],)).fetchone()
    return {"required":user["role"]=="ADMIN" or bool(row and row[0]),
            "enabled":bool(row and row[1]),"encrypted_secret":row[1] if row else None,
            "version":row[2] if row else 0,"last_used_step":row[3] if row else -1,
            "recovery_required":bool(row and row[4])}


def requires_factor(state):
    return state["required"] or state["enabled"] or state["recovery_required"]


def issue_login_challenge(con,cipher,user_id,password_hash,stamp=None):
    """Caller holds a write transaction after verifying this password hash."""
    stamp=time.time() if stamp is None else stamp
    user=_user(con,user_id)
    if not user or user["status"]!="ACTIVE" or user["password_hash"]!=password_hash:
        raise MFAChallengeError("Sign in again.")
    con.execute("INSERT OR IGNORE INTO user_mfa(user_id) VALUES(?)",(user_id,))
    state=account_state(con,user)
    if not (state["required"] or state["enabled"] or state["recovery_required"]): raise MFAChallengeError("Sign in again.")
    purpose="login" if state["enabled"] else "enroll"
    encrypted=None if state["enabled"] else encrypt_secret(cipher,user_id,pyotp.random_base32())
    token=secrets.token_urlsafe(32)
    con.execute("DELETE FROM mfa_challenges WHERE expires_at<=?",(stamp,))
    # Keep at most three pending browser logins/setups for an account.
    con.execute("""DELETE FROM mfa_challenges WHERE user_id=? AND purpose IN ('login','enroll')
        AND token_hash NOT IN (SELECT token_hash FROM mfa_challenges WHERE user_id=?
        AND purpose IN ('login','enroll') ORDER BY created_at DESC,token_hash DESC LIMIT 2)""",(user_id,user_id))
    expires=stamp+(LOGIN_SECONDS if purpose=="login" else ENROLLMENT_SECONDS)
    con.execute("INSERT INTO mfa_challenges VALUES(?,?,?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(),user_id,purpose,credential_version(user),
                 state["version"],stamp,expires,encrypted))
    return token,purpose


def pending_login(con,token,stamp=None):
    stamp=time.time() if stamp is None else stamp
    if not isinstance(token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}",token):
        raise MFAChallengeError("Sign-in expired. Enter your password again.")
    row=con.execute("SELECT user_id,purpose,password_version,security_version,encrypted_pending_secret FROM mfa_challenges WHERE token_hash=? AND expires_at>?",
                    (hashlib.sha256(token.encode()).hexdigest(),stamp)).fetchone()
    if not row or row[1] not in ("login","enroll"): raise MFAChallengeError("Sign-in expired. Enter your password again.")
    user=_user(con,row[0])
    if not user or user["status"]!="ACTIVE" or not hmac.compare_digest(row[2],credential_version(user)):
        raise MFAChallengeError("Sign-in expired. Enter your password again.")
    state=account_state(con,user)
    if row[3]!=state["version"] or (row[1]=="login" and not state["enabled"]) or (row[1]=="enroll" and (state["enabled"] or not (state["required"] or state["recovery_required"]))):
        raise MFAChallengeError("Sign-in expired. Enter your password again.")
    return user,state,row[1],row[4]


def consume_factor(con,cipher,user,state,code,method,stamp):
    if not state["enabled"]: raise MFAChallengeError("An enrolled authenticator is required.")
    if method=="totp":
        secret=decrypt_secret(cipher,user["id"],state["encrypted_secret"])
        step=matching_step(secret,code,stamp,state["last_used_step"])
        if step is None: raise MFAChallengeError("Invalid or already used authentication code.")
        changed=con.execute("UPDATE user_mfa SET last_used_step=? WHERE user_id=? AND security_version=? AND last_used_step<?",
                            (step,user["id"],state["version"],step)).rowcount
    elif method=="recovery":
        try: digest=recovery_hash(code)
        except ValueError: raise MFAChallengeError("Invalid or already used authentication code.") from None
        from datetime import datetime,timezone
        used_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
        changed=con.execute("UPDATE mfa_recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                            (used_at,user["id"],digest)).rowcount
    else: raise MFAChallengeError("Invalid authentication method.")
    if changed!=1: raise MFAChallengeError("Invalid or already used authentication code.")


def verify_login(con,cipher,token,code,method="totp",stamp=None):
    """Atomically consume the factor and challenge in the caller's write transaction."""
    stamp=time.time() if stamp is None else stamp
    user,state,purpose,_=pending_login(con,token,stamp)
    if purpose!="login": raise MFAChallengeError("Complete authenticator setup first.")
    consume_factor(con,cipher,user,state,code,method,stamp)
    con.execute("DELETE FROM mfa_challenges WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))
    return user


def verify_management(con,cipher,user_id,sid,password_hash,code,method,expected_role,stamp=None):
    """Fresh password was checked by caller; recheck account/session under the write lock."""
    stamp=time.time() if stamp is None else stamp
    user=_user(con,user_id)
    if not user or user["status"]!="ACTIVE" or user["role"]!=expected_role or user["password_hash"]!=password_hash:
        raise MFAChallengeError("Sign in again before changing MFA.")
    state=account_state(con,user)
    proof=con.execute("""SELECT p.security_version,p.verified FROM mfa_session_proofs p
        JOIN security_sessions s ON s.token_hash=p.token_hash
        WHERE p.token_hash=? AND p.user_id=? AND s.user_id=?""",
        (hashlib.sha256(sid.encode()).hexdigest(),user_id,user_id)).fetchone()
    if not proof or proof[0]!=state["version"] or proof[1]!=1:
        raise MFAChallengeError("Sign in again before changing MFA.")
    consume_factor(con,cipher,user,state,code,method,stamp)
    return user


def revoke_access(con,user_id):
    con.execute("DELETE FROM mfa_challenges WHERE user_id=?",(user_id,))
    con.execute("DELETE FROM security_sessions WHERE user_id=?",(user_id,))


def new_recovery_codes(con,user_id,stamp):
    from datetime import datetime,timezone
    created_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    codes=[]
    for _ in range(10):
        raw=secrets.token_hex(10).upper()
        codes.append("-".join(raw[i:i+4] for i in range(0,20,4)))
    con.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?",(user_id,))
    con.executemany("INSERT INTO mfa_recovery_codes VALUES(?,?,?,NULL)",
                    [(user_id,recovery_hash(code),created_at) for code in codes])
    return codes


def begin_replacement(con,cipher,user,sid,stamp=None):
    stamp=time.time() if stamp is None else stamp
    state=account_state(con,user)
    token=secrets.token_urlsafe(32)
    con.execute("DELETE FROM mfa_challenges WHERE user_id=? AND purpose='enroll'",(user["id"],))
    con.execute("INSERT INTO mfa_challenges VALUES(?,?,?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(),user["id"],"enroll",
                 enrollment_binding(user["password_hash"],sid),state["version"],stamp,stamp+ENROLLMENT_SECONDS,
                 encrypt_secret(cipher,user["id"],pyotp.random_base32())))
    return token


def regenerate_recovery(con,user_id,stamp=None):
    codes=new_recovery_codes(con,user_id,time.time() if stamp is None else stamp)
    con.execute("UPDATE user_mfa SET security_version=security_version+1 WHERE user_id=?",(user_id,))
    revoke_access(con,user_id)
    return codes


def disable_factor(con,user):
    state=account_state(con,user)
    if state["required"] or state["recovery_required"]: raise MFAChallengeError("MFA is required and cannot be disabled.")
    con.execute("UPDATE user_mfa SET encrypted_secret=NULL,enabled_at=NULL,last_used_step=-1,security_version=security_version+1 WHERE user_id=?",(user["id"],))
    con.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?",(user["id"],))
    revoke_access(con,user["id"])


def reset_factor(con,user_id):
    """Preserve role/policy; optional accounts also need verified re-enrollment after reset."""
    con.execute("INSERT OR IGNORE INTO user_mfa(user_id) VALUES(?)",(user_id,))
    con.execute("UPDATE user_mfa SET encrypted_secret=NULL,enabled_at=NULL,last_used_step=-1,recovery_required=1,security_version=security_version+1 WHERE user_id=?",(user_id,))
    con.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?",(user_id,))
    revoke_access(con,user_id)


def enrollment_binding(password_hash,sid):
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]+":"+hashlib.sha256(sid.encode()).hexdigest()


def begin_enrollment(con,cipher,user_id,password_hash,sid,stamp=None):
    """Caller owns the transaction; replace previous enrollment for this user."""
    stamp=time.time() if stamp is None else stamp
    user=con.execute("SELECT password_hash,status FROM users WHERE id=?",(user_id,)).fetchone()
    active_session=con.execute("SELECT 1 FROM security_sessions WHERE token_hash=? AND user_id=?",(hashlib.sha256(sid.encode()).hexdigest(),user_id)).fetchone()
    if not user or user[0]!=password_hash or user[1]!="ACTIVE" or not active_session: raise MFAEnrollmentError("Sign in again before setup.")
    con.execute("DELETE FROM mfa_challenges WHERE expires_at<=?",(stamp,))
    con.execute("INSERT OR IGNORE INTO user_mfa(user_id) VALUES(?)",(user_id,))
    state=con.execute("SELECT encrypted_secret,security_version FROM user_mfa WHERE user_id=?",(user_id,)).fetchone()
    if state[0]: raise MFAEnrollmentError("An authenticator is already enrolled.")
    # A bounded number of active enrollment rows: one per account.
    con.execute("DELETE FROM mfa_challenges WHERE user_id=? AND purpose='enroll'",(user_id,))
    token=secrets.token_urlsafe(32)
    encrypted=encrypt_secret(cipher,user_id,pyotp.random_base32())
    con.execute("INSERT INTO mfa_challenges VALUES(?,?,?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(),user_id,"enroll",
                 enrollment_binding(password_hash,sid),state[1],stamp,stamp+ENROLLMENT_SECONDS,encrypted))
    return token


def pending_enrollment(con,cipher,user_id,token,sid,stamp=None):
    stamp=time.time() if stamp is None else stamp
    if sid is None:
        try:
            user,_,purpose,encrypted=pending_login(con,token,stamp)
            if user["id"]!=user_id or purpose!="enroll": raise MFAChallengeError("Invalid setup.")
            return decrypt_secret(cipher,user_id,encrypted)
        except MFAChallengeError:
            raise MFAEnrollmentError("Setup expired. Enter your password again.") from None
    if not isinstance(token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}",token):
        raise MFAEnrollmentError("Setup expired. Verify your password to start again.")
    row=con.execute("""SELECT mc.encrypted_pending_secret,mc.password_version,mc.security_version,
        u.password_hash,u.status,um.security_version,um.encrypted_secret
        FROM mfa_challenges mc JOIN users u ON u.id=mc.user_id JOIN user_mfa um ON um.user_id=u.id
        WHERE mc.token_hash=? AND mc.user_id=? AND mc.purpose='enroll' AND mc.expires_at>?""",
        (hashlib.sha256(token.encode()).hexdigest(),user_id,stamp)).fetchone()
    active_session=con.execute("SELECT 1 FROM security_sessions WHERE token_hash=? AND user_id=?",(hashlib.sha256(sid.encode()).hexdigest(),user_id)).fetchone()
    proof=con.execute("SELECT user_id,security_version,verified FROM mfa_session_proofs WHERE token_hash=?",(hashlib.sha256(sid.encode()).hexdigest(),)).fetchone()
    if not row or row[4]!="ACTIVE" or row[2]!=row[5] or not active_session or not hmac.compare_digest(row[1],enrollment_binding(row[3],sid)) or (row[6] and (not proof or proof[0]!=user_id or proof[1]!=row[5] or proof[2]!=1)):
        raise MFAEnrollmentError("Setup expired. Verify your password to start again.")
    return decrypt_secret(cipher,user_id,row[0])


def provisioning_uri(secret,username):
    return pyotp.TOTP(secret).provisioning_uri(name=username,issuer_name=ISSUER)


def qr_svg(secret,username):
    image=qrcode.make(provisioning_uri(secret,username),image_factory=qrcode.image.svg.SvgPathFillImage)
    return image.to_string()


def matching_step(secret,code,stamp=None,last_used_step=-1):
    if not isinstance(code,str) or not re.fullmatch(r"[0-9]{6}",code): return None
    current=int(time.time() if stamp is None else stamp)//30
    totp=pyotp.TOTP(secret)
    # Store the actual matched time step so future login cannot replay enrollment.
    for step in (current,current-1,current+1):
        if step>=0 and step>last_used_step and hmac.compare_digest(totp.at(step*30),code): return step
    return None


def recovery_hash(code):
    if not isinstance(code,str) or not re.fullmatch(r"(?:[A-Fa-f0-9]{4}-){4}[A-Fa-f0-9]{4}",code):
        raise ValueError("Invalid recovery code format.")
    return hashlib.sha256(code.replace("-","").upper().encode("ascii")).hexdigest()


def complete_enrollment(con,cipher,user_id,token,sid,code,stamp=None):
    """Caller holds a write transaction. Return recovery plaintext once, never store it."""
    stamp=time.time() if stamp is None else stamp
    secret=pending_enrollment(con,cipher,user_id,token,sid,stamp)
    step=matching_step(secret,code,stamp)
    if step is None: raise MFAEnrollmentError("Enter a valid six-digit code from your authenticator.")
    from datetime import datetime,timezone
    enabled_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    con.execute("UPDATE user_mfa SET encrypted_secret=?,enabled_at=?,last_used_step=?,recovery_required=0,security_version=security_version+1 WHERE user_id=?",
                (encrypt_secret(cipher,user_id,secret),enabled_at,step,user_id))
    codes=new_recovery_codes(con,user_id,stamp)
    revoke_access(con,user_id)
    return codes


class MFAConfigurationError(RuntimeError):
    """Safe configuration error that never includes the supplied key."""


class MFASecretError(ValueError):
    """Safe secret error that never includes plaintext or ciphertext."""


def configured_cipher(environ=None):
    """Require an explicit, stable key. Never generate a fallback at startup."""
    env=os.environ if environ is None else environ
    value=env.get("MFA_ENCRYPTION_KEY", "")
    filename=env.get("MFA_ENCRYPTION_KEY_FILE", "")
    if bool(value)==bool(filename):
        raise MFAConfigurationError("Configure exactly one MFA encryption key source.")
    if filename:
        try:
            value=Path(filename).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            raise MFAConfigurationError("The MFA encryption key file could not be read.") from None
    try:
        raw=value.encode("ascii")
        decoded=base64.b64decode(raw,altchars=b"-_",validate=True)
        if len(decoded)!=32 or base64.urlsafe_b64encode(decoded)!=raw:
            raise ValueError
        if value==env.get("SECRET_KEY"):
            raise MFAConfigurationError("MFA and session encryption keys must be separate.")
        return Fernet(raw)
    except (ValueError, UnicodeError, binascii.Error):
        raise MFAConfigurationError("The MFA encryption key must be a valid Fernet key.") from None


def _validate_secret(user_id,secret):
    if type(user_id) is not int or user_id<=0 or not isinstance(secret,str) or not re.fullmatch(r"[A-Z2-7]{32}",secret):
        raise MFASecretError("Invalid MFA secret record.")


def encrypt_secret(cipher,user_id,secret):
    _validate_secret(user_id,secret)
    # Binding the account inside authenticated ciphertext prevents row-swapping.
    payload=json.dumps({"user_id":user_id,"secret":secret},separators=(",",":"))
    return cipher.encrypt(payload.encode("ascii")).decode("ascii")


def decrypt_secret(cipher,user_id,encrypted):
    try:
        if not isinstance(encrypted,str) or len(encrypted)>2048: raise ValueError
        payload=json.loads(cipher.decrypt(encrypted.encode("ascii")))
        _validate_secret(user_id,payload["secret"])
        if type(payload["user_id"]) is not int or payload["user_id"]!=user_id: raise ValueError
        return payload["secret"]
    except (InvalidToken,ValueError,UnicodeError,KeyError,TypeError):
        raise MFASecretError("The MFA secret could not be decrypted for this account.") from None


def generate_key_file(path):
    """Exclusive creation: never overwrite a key; never print its contents."""
    target=Path(path)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(Fernet.generate_key()+b"\n")


def connect_existing(path,readonly=False):
    # mode=rw/ro refuses to create a new database for a misspelled target.
    uri=Path(path).resolve().as_uri()+"?mode="+("ro" if readonly else "rw")
    con=sqlite3.connect(uri,uri=True,timeout=5)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _validate_database(con):
    columns={row[1] for row in con.execute("PRAGMA table_info(users)")}
    if not {"id","username","password_hash","role","status"}<=columns:
        raise RuntimeError("The database does not contain the expected users schema.")
    session_columns={row[1] for row in con.execute("PRAGMA table_info(security_sessions)")}
    if not {"token_hash","user_id","started","seen"}<=session_columns:
        raise RuntimeError("The database requires the existing security session schema first.")
    tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    existing=set(TABLES)&tables
    # The Step 2/3 core schema can be upgraded additively with session proofs.
    core=set(TABLES)-{"mfa_session_proofs"}
    if existing and not core<=existing:
        raise RuntimeError("Incomplete MFA schema; manual review is required.")
    upgraded=True
    if existing:
        if con.execute("SELECT version FROM mfa_schema_versions").fetchall()!=[(SCHEMA_VERSION,)]:
            raise RuntimeError("Unsupported MFA schema version.")
        # Compare structure against a disposable reference, not user data.
        with closing(sqlite3.connect(":memory:")) as reference:
            for name,definition in TABLES.items():
                reference.execute(f"CREATE TABLE {name} ({definition})")
                if name in existing:
                    actual=con.execute(f"PRAGMA table_info({name})").fetchall()
                    expected=reference.execute(f"PRAGMA table_info({name})").fetchall()
                    if name=="user_mfa" and actual==expected[:-1]: upgraded=False
                    elif actual!=expected: raise RuntimeError("Unexpected MFA table structure; manual review is required.")
    return existing==set(TABLES) and upgraded


def migrate(con):
    """Caller supplies an existing connection; schema changes are one transaction."""
    if con.in_transaction: raise RuntimeError("Migration requires a fresh transaction.")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        existed=_validate_database(con)
        for name,definition in TABLES.items():
            con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({definition})")
        if "recovery_required" not in {row[1] for row in con.execute("PRAGMA table_info(user_mfa)")}:
            con.execute("ALTER TABLE user_mfa ADD COLUMN recovery_required INTEGER NOT NULL DEFAULT 0 CHECK(recovery_required IN (0,1))")
        for sql in INDEXES: con.execute(sql)
        con.execute("INSERT OR IGNORE INTO mfa_schema_versions VALUES(?)",(SCHEMA_VERSION,))
        con.commit()
        return not existed
    except Exception:
        con.rollback()
        raise


def backup_existing(con,path):
    # Exclusive creation ensures an existing backup is never overwritten.
    target=Path(path).resolve()
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(fd)
    with closing(sqlite3.connect(target)) as dest: con.backup(dest)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    keygen=commands.add_parser("keygen")
    keygen.add_argument("--output",required=True)
    migration=commands.add_parser("migrate")
    migration.add_argument("--database",required=True)
    migration.add_argument("--apply",action="store_true")
    migration.add_argument("--backup")
    recovery=commands.add_parser("recover-admin")
    recovery.add_argument("--database",required=True)
    recovery.add_argument("--user-id",type=int,required=True)
    recovery.add_argument("--apply",action="store_true")
    recovery.add_argument("--backup")
    recovery.add_argument("--confirm-user-id",type=int)
    recovery.add_argument("--identity-verified",action="store_true")
    args=parser.parse_args()
    try:
        if args.command=="keygen":
            generate_key_file(args.output)
            print("Private MFA key file created. Back it up separately; never commit it.")
            return
        if args.apply:
            if not args.backup: parser.error("--apply requires --backup with a new destination")
            configured_cipher()
        with closing(connect_existing(args.database,readonly=not args.apply)) as con:
            existed=_validate_database(con)
            if args.command=="recover-admin":
                if not existed: raise RuntimeError("Complete MFA migration first.")
                user=_user(con,args.user_id)
                if not user or user["role"]!="ADMIN" or user["status"]!="ACTIVE": raise RuntimeError("Active administrator required.")
                if not args.apply:
                    print(f"Plan: reset MFA for active admin ID {args.user_id}; revoke sessions and require enrollment. No changes applied.")
                    return
                if args.confirm_user_id!=args.user_id or not args.identity_verified: raise RuntimeError("Identity and target confirmation required.")
                backup_existing(con,args.backup)
                with con:
                    con.execute("BEGIN IMMEDIATE")
                    user=_user(con,args.user_id)
                    if not user or user["role"]!="ADMIN" or user["status"]!="ACTIVE": raise RuntimeError("Active administrator required.")
                    reset_factor(con,args.user_id)
                print(f"MFA recovery completed actor=hosting-console target_id={args.user_id} identity_confirmation=recorded; enrollment required. Record this event in the restricted recovery incident log.")
                return
            if not args.apply:
                print("MFA schema already present." if existed else "Plan: add missing MFA tables/indexes, including session proofs. No changes applied.")
                return
            backup_existing(con,args.backup)
            changed=migrate(con)
            print("Backup completed. MFA storage migration applied." if changed else "Backup completed. MFA storage already current.")
    except (OSError,sqlite3.Error,RuntimeError):
        # Keep driver errors and key-file paths out of routine console output.
        parser.exit(1,"Operation refused or failed. Check target, permissions, schema, and key configuration. No secret was printed.\n")


if __name__=="__main__": main()
