# app.py - Versione definitiva con database SQLAlchemy
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort, send_from_directory, Response, send_file
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import or_, inspect, text
from collections import Counter, defaultdict
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import os, re, csv, io, json, secrets, random, smtplib, sys, uuid
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Carica variabili d'ambiente
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_fallback_key_change_in_production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# =====================================================
# ============= CONFIGURAZIONE DATABASE ===============
# =====================================================

# Determina la cartella base dell'app (dove si trova app.py)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

print(f"[RegistroProf] BASE_DIR: {BASE_DIR}")

# Carica URL database
database_url = os.getenv("DATABASE_URL", "").strip()

# Fallback intelligente: SQLite con percorso ASSOLUTO per sviluppo locale
if not database_url or "host" in database_url or "@localhost" in database_url:
    database_url = f"sqlite:///{os.path.join(BASE_DIR, 'registro.db')}"
    print(f"[DB] Sviluppo SQLite: {database_url}")
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
    print(f"[DB] Produzione PostgreSQL: {database_url[:60]}...")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
if database_url.startswith("sqlite"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"] = {"check_same_thread": False}

db = SQLAlchemy(app)
print("[DB] SQLAlchemy OK")

try:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
except Exception:
    pass

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "materiali")
AVATAR_FOLDER = os.path.join(BASE_DIR, "uploads", "avatars")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)
MAX_UPLOAD_BYTES = min(int(os.getenv("MAX_UPLOAD_MB", "18")) * 1024 * 1024, 52428800)
ALLOWED_EXT_MAT = frozenset("pdf zip doc docx pptx txt jpg jpeg png gif".split())

def rl_key_lim():
    uid = session.get("username")
    return uid if uid else ("ip:"+get_remote_address())

limiter = Limiter(app=app, key_func=rl_key_lim, default_limits=[], storage_uri=os.getenv("RATELIMIT_STORAGE_URI","memory://"))

def migrate_sqlite():
    uri = str(app.config["SQLALCHEMY_DATABASE_URI"] or "")
    if not uri.startswith("sqlite"): return
    migr = {"users":[("avatar_preset","INTEGER DEFAULT 0"), ("avatar_file","VARCHAR(260)"), ("oauth_provider","VARCHAR(40)"), ("oauth_sub","VARCHAR(120)")],
            "reviews":[("is_anonymous","BOOLEAN DEFAULT 0"), ("professor_id","INTEGER")],
            "notices":[("expires_at","DATETIME")]}
    try:
        insp = inspect(db.engine)
        for table, defs in migr.items():
            if table not in insp.get_table_names(): continue
            have = {c["name"].lower() for c in insp.get_columns(table)}
            for colname, decl in defs:
                if colname.lower() in have: continue
                with db.engine.begin() as cx:
                    cx.execute(text(f"ALTER TABLE {table} ADD COLUMN {colname} {decl}"))
    except Exception as e:
        print(f"[migrate] {e}")

try:
    from authlib.integrations.flask_client import OAuth as _OAuth
    oauth_registry = _OAuth(app)
except Exception:
    oauth_registry = None

def oauth_setup():
    ggl = ms = fb = None
    if oauth_registry is None: return ggl, ms, fb
    if os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
        ggl = oauth_registry.register(
            name="google",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope":"openid email profile"},
        )
    if os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_CLIENT_SECRET"):
        tid = os.getenv("MICROSOFT_TENANT_ID", "common")
        ms = oauth_registry.register(
            name="microsoft",
            client_id=os.getenv("MICROSOFT_CLIENT_ID"),
            client_secret=os.getenv("MICROSOFT_CLIENT_SECRET"),
            server_metadata_url=f"https://login.microsoftonline.com/{tid}/v2.0/.well-known/openid-configuration",
            client_kwargs={"scope":"openid email profile"},
        )
    if os.getenv("FACEBOOK_CLIENT_ID") and os.getenv("FACEBOOK_CLIENT_SECRET"):
        fb = oauth_registry.register(
            name="facebook",
            client_id=os.getenv("FACEBOOK_CLIENT_ID"),
            client_secret=os.getenv("FACEBOOK_CLIENT_SECRET"),
            access_token_url="https://graph.facebook.com/oauth/access_token",
            authorize_url="https://www.facebook.com/dialog/oauth",
            api_base_url="https://graph.facebook.com/",
            client_kwargs={"scope":"email"},
        )
    return ggl, ms, fb

oauth_google, oauth_ms, oauth_fb = oauth_setup()

def oauth_find_or_create(provider, sub, email, nome):
    provider = (provider or "").strip()[:40]
    email = (email or "").strip() or None
    nome = (nome or "").strip() or None
    if not provider or not sub: return None, "oauth_param"
    existing = User.query.filter_by(oauth_provider=provider, oauth_sub=str(sub)).first()
    if existing: return existing, None
    if email:
        ex = User.query.filter_by(email=email).first()
        if ex:
            if getattr(ex, "oauth_sub", None) and ex.oauth_provider and (ex.oauth_sub != str(sub) or ex.oauth_provider != provider):
                return None, "email_conflict"
            ex.oauth_provider, ex.oauth_sub = provider, str(sub)
            if nome and not (ex.nome_cognome or "").strip(): ex.nome_cognome = nome[:150]
            db.session.commit(); return ex, None
    base_raw = (email.split("@")[0] if email else "") or "studente"
    base = re.sub(r"[^a-zA-Z0-9._-]", "", base_raw)[:24] or "studente"
    un, i = base, 0
    while User.query.filter_by(username=un).first():
        i += 1; un = f"{base}{i}"
    pwd = password_hash(secrets.token_urlsafe(24))
    em_used = email
    if not em_used:
        em_used = f"{un}.{provider}@oauth.local.placeholder"
        while User.query.filter_by(email=em_used).first():
            em_used = f"{un}.{uuid.uuid4().hex[:8]}@{provider}.oauth.placeholder"
    u = User(username=un, password=pwd, email=em_used, nome_cognome=(nome or un)[:150], role="user",
             stato="attivo", account_status="attivo", oauth_provider=provider, oauth_sub=str(sub))
    db.session.add(u); db.session.commit()
    return u, None

def oauth_finalize_redirect(user_row):
    if user_row.account_status == "sospeso":
        return redirect(url_for("login"))
    if user_row.account_status == "bannato":
        return redirect(url_for("login"))
    if getattr(user_row, "stato", "") == "in_attesa":
        return redirect(url_for("login"))
    session["username"], session["role"] = user_row.username, user_row.role
    session["session_id"] = registra_sessione(user_row.username)
    log_audit("login_oauth", target_username=user_row.username)
    return redirect(url_for("admin_dashboard" if user_row.role == "admin" else "user_dashboard"))

def materiale_consentiti(fname):
    return "." in fname and fname.rsplit(".", 1)[-1].lower() in ALLOWED_EXT_MAT

def chiav_prof(pref_id=None, nome=None, mat=None, scuola=None):
    if pref_id:
        return f"id:{pref_id}"
    return "|".join([(nome or "").lower().strip(),(mat or "").lower().strip(),(scuola or "").lower().strip()])[:255]

def find_flat_comment(comms, cid):
    """Ricerca ricorsiva in lista commenti (vecchio formato nidificato oppure lista piatta)."""
    if cid is None: return None
    for c in comms or []:
        if isinstance(c, dict) and int(c.get("id", -1)) == int(cid):
            return c
        sub = c.get("replies") if isinstance(c, dict) else None
        if sub:
            f = find_flat_comment(sub, cid)
            if f: return f
    return None

def max_review_comment_id(comms):
    m = 0
    for c in comms or []:
        if not isinstance(c, dict): continue
        try:
            cid = int(c.get("id") or 0)
        except (TypeError, ValueError):
            cid = 0
        m = max(m, cid)
        m = max(m, max_review_comment_id(c.get("replies") or []))
    return m

def flatten_comments_for_notify(comms, out=None):
    out = out if out is not None else []
    for c in comms or []:
        if isinstance(c, dict):
            out.append(c); flatten_comments_for_notify(c.get("replies") or [], out)
    return out

def notify_favorites_new_review(rec_row):
    try:
        fk = chiav_prof(None, getattr(rec_row, "nomeProfRec", None), "", getattr(rec_row, "scuola", None) or "")
        conds = [ProfessorFavorite.chiave_prof == fk]
        if getattr(rec_row, "professor_id", None):
            conds.append(ProfessorFavorite.professor_id == rec_row.professor_id)
        qry = ProfessorFavorite.query.filter(or_(*conds))
        for pf in qry.all():
            if pf.username == rec_row.username: continue
            u = User.query.filter_by(username=pf.username).first()
            lab = getattr(rec_row, "nomeProfRec", "") or "Professore"
            crea_notifica(pf.username, "preferiti", "Nuova recensione su un professore tra i tuoi seguiti",
                          f"Hai ricevuto un aggiornamento relativo a: {lab}.", "/user#recensioni")
    except Exception as e:
        print(f"[notify_favorites_new_review] {e}")

def notify_favorites_new_material(material_row):
    try:
        fk = chiav_prof(getattr(material_row, "professor_id", None), getattr(material_row, "professore_nome", None),
                        getattr(material_row, "materia", None), getattr(material_row, "scuola", None))
        q = ProfessorFavorite.query.filter(ProfessorFavorite.chiave_prof == fk)
        for pf in q.all():
            if pf.username == material_row.caricato_da:
                continue
            crea_notifica(pf.username, "preferiti", "Nuovo materiale su un professore seguito",
                          f"Disponibile nuovo materiale: {material_row.titolo or 'Materiale didattico'}.", "/user#materiali")
    except Exception as e:
        print(f"[notify_favorites_new_material] {e}")

def recensioni_mask_row(rec_row):
    out = rec_row.to_dict()
    anon = bool(getattr(rec_row, "is_anonymous", False))
    vu = session.get("username"); ad = session.get("role") == "admin"
    out["is_mine"] = bool(vu and vu == rec_row.username)
    if anon and not ad and vu != rec_row.username:
        out["user"], out["_anon"] = "Anonimo", True
    elif anon and ad:
        out["_real_author"] = rec_row.username
    return out

# =====================================================
# ============= MODELLI DATABASE ======================
# =====================================================

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True)
    nome_cognome = db.Column(db.String(150))
    scuola = db.Column(db.String(150))
    role = db.Column(db.String(20), default='user')
    stato = db.Column(db.String(20), default='attivo')
    account_status = db.Column(db.String(20), default='attivo')
    telefono = db.Column(db.String(30))
    data_nascita = db.Column(db.String(20))
    indirizzo = db.Column(db.String(200))
    citta = db.Column(db.String(100))
    cap = db.Column(db.String(10))
    admin_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    avatar_preset = db.Column(db.Integer, default=0)
    avatar_file = db.Column(db.String(260))
    oauth_provider = db.Column(db.String(40))
    oauth_sub = db.Column(db.String(120), index=True)
    
    def to_dict(self, include_sensitive=False):
        d = {'id':self.id,'username':self.username,'email':self.email,'nome_cognome':self.nome_cognome,
             'scuola':self.scuola,'role':self.role,'stato':self.stato,'account_status':self.account_status,
             'created_at':self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None}
        if include_sensitive:
            d.update({'telefono':self.telefono,'data_nascita':self.data_nascita,'indirizzo':self.indirizzo,
                      'citta':self.citta,'cap':self.cap,'admin_note':self.admin_note})
        d['avatar_preset'] = self.avatar_preset or 0
        d['avatar_file'] = bool(self.avatar_file)
        return d

class Vote(db.Model):
    __tablename__ = 'votes'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    voto = db.Column(db.String(10), nullable=False)
    nomeProf = db.Column(db.String(100))
    materia = db.Column(db.String(100))
    scuola = db.Column(db.String(150))
    timestamp = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {'id':self.id,'user':self.username,'voto':self.voto,'nomeProf':self.nomeProf,
                'materia':self.materia,'scuola':self.scuola,
                'timestamp':self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else None}

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    nomeProfRec = db.Column(db.String(100))
    scuola = db.Column(db.String(150))
    recensione = db.Column(db.Text)
    likes = db.Column(db.Integer, default=0)
    dislikes = db.Column(db.Integer, default=0)
    user_likes = db.Column(db.JSON, default=list)
    user_dislikes = db.Column(db.JSON, default=list)
    commenti = db.Column(db.JSON, default=list)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    is_anonymous = db.Column(db.Boolean, default=False)
    professor_id = db.Column(db.Integer, index=True)
    def to_dict(self):
        return {'id':self.id,'user':self.username,'nomeProfRec':self.nomeProfRec,'scuola':self.scuola,
                'recensione':self.recensione,'likes':self.likes,'dislikes':self.dislikes,
                'user_likes':self.user_likes,'user_dislikes':self.user_dislikes,'commenti':self.commenti,
                'timestamp':self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else None,
                'is_anonymous': bool(self.is_anonymous),'professor_id':self.professor_id}

class Professor(db.Model):
    __tablename__ = 'professors'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    materia = db.Column(db.String(100))
    scuola = db.Column(db.String(150))
    descrizione = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {'id':self.id,'nome':self.nome,'materia':self.materia,'scuola':self.scuola,'descrizione':self.descrizione}

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), unique=True, nullable=False, index=True)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {"id": self.id, "nome": self.nome, "created_by": self.created_by,
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None}

class School(db.Model):
    __tablename__ = 'schools'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(180), unique=True, nullable=False, index=True)
    created_by = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {"id": self.id, "nome": self.nome, "created_by": self.created_by,
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None}

class RoleDef(db.Model):
    __tablename__ = 'role_defs'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(40), unique=True, nullable=False, index=True)
    is_system = db.Column(db.Boolean, default=False)

class SessionLog(db.Model):
    __tablename__ = 'sessions'
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    ip = db.Column(db.String(50))
    login_time = db.Column(db.DateTime, default=datetime.now)
    last_activity = db.Column(db.DateTime, default=datetime.now)
    user_agent = db.Column(db.String(200))
    def to_dict(self):
        return {'session_id':self.session_id,'username':self.username,'ip':self.ip,
                'login_time':self.login_time.strftime("%Y-%m-%d %H:%M:%S") if self.login_time else None,
                'last_activity':self.last_activity.strftime("%Y-%m-%d %H:%M:%S") if self.last_activity else None,
                'user_agent':self.user_agent}

class RegistrationRequest(db.Model):
    __tablename__ = 'registration_requests'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120))
    nome_cognome = db.Column(db.String(150))
    scuola = db.Column(db.String(150))
    stato = db.Column(db.String(20), default='in_attesa')
    admin_note = db.Column(db.Text)
    data_registrazione = db.Column(db.DateTime, default=datetime.now)
    data_approvazione = db.Column(db.DateTime)
    def to_dict(self):
        return {'id':self.id,'username':self.username,'email':self.email,'nome_cognome':self.nome_cognome,
                'scuola':self.scuola,'stato':self.stato,'admin_note':self.admin_note,
                'data_registrazione':self.data_registrazione.strftime("%Y-%m-%d %H:%M:%S") if self.data_registrazione else None,
                'data_approvazione':self.data_approvazione.strftime("%Y-%m-%d %H:%M:%S") if self.data_approvazione else None}

class Ticket(db.Model):
    __tablename__ = 'tickets'
    id = db.Column(db.Integer, primary_key=True)
    utente = db.Column(db.String(80), nullable=False, index=True)
    oggetto = db.Column(db.String(200), nullable=False)
    messaggio = db.Column(db.Text, nullable=False)
    priorita = db.Column(db.String(20), default='media')
    stato = db.Column(db.String(20), default='aperto')
    data_apertura = db.Column(db.DateTime, default=datetime.now)
    data_chiusura = db.Column(db.DateTime)
    admin_assegnato = db.Column(db.String(80))
    risposte = db.Column(db.JSON, default=list)
    def to_dict(self):
        return {'id':self.id,'utente':self.utente,'oggetto':self.oggetto,'messaggio':self.messaggio,
                'priorita':self.priorita,'stato':self.stato,
                'data_apertura':self.data_apertura.strftime("%Y-%m-%d %H:%M:%S") if self.data_apertura else None,
                'data_chiusura':self.data_chiusura.strftime("%Y-%m-%d %H:%M:%S") if self.data_chiusura else None,
                'admin_assegnato':self.admin_assegnato,'risposte':self.risposte}

class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False)
    indice = db.Column(db.Integer)
    motivo = db.Column(db.Text)
    segnalatore = db.Column(db.String(80), nullable=False)
    stato = db.Column(db.String(20), default='pending')
    admin_note = db.Column(db.Text)
    data = db.Column(db.DateTime, default=datetime.now)
    data_chiusura = db.Column(db.DateTime)
    def to_dict(self):
        return {'id':self.id,'tipo':self.tipo,'indice':self.indice,'motivo':self.motivo,
                'segnalatore':self.segnalatore,'stato':self.stato,'admin_note':self.admin_note,
                'data':self.data.strftime("%Y-%m-%d %H:%M:%S") if self.data else None,
                'data_chiusura':self.data_chiusura.strftime("%Y-%m-%d %H:%M:%S") if self.data_chiusura else None}

class Notice(db.Model):
    __tablename__ = 'notices'
    id = db.Column(db.Integer, primary_key=True)
    titolo = db.Column(db.String(200), nullable=False)
    contenuto = db.Column(db.Text, nullable=False)
    attivo = db.Column(db.Boolean, default=True)
    priority = db.Column(db.String(20), default='normal')
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime)
    def to_dict(self):
        return {'id':self.id,'titolo':self.titolo,'contenuto':self.contenuto,'attivo':self.attivo,
                'priority':self.priority,
                'created_at':self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
                'expires_at':self.expires_at.strftime("%Y-%m-%d %H:%M:%S") if self.expires_at else None}

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    utente = db.Column(db.String(80), nullable=False, index=True)
    tipo = db.Column(db.String(50), nullable=False)
    titolo = db.Column(db.String(200), nullable=False)
    messaggio = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(200))
    letta = db.Column(db.Boolean, default=False)
    data = db.Column(db.DateTime, default=datetime.now)
    data_lettura = db.Column(db.DateTime)
    def to_dict(self):
        return {'id':self.id,'utente':self.utente,'tipo':self.tipo,'titolo':self.titolo,'messaggio':self.messaggio,
                'link':self.link,'letta':self.letta,
                'data':self.data.strftime("%Y-%m-%d %H:%M:%S") if self.data else None,
                'data_lettura':self.data_lettura.strftime("%Y-%m-%d %H:%M:%S") if self.data_lettura else None}

class PasswordRecovery(db.Model):
    __tablename__ = 'password_recovery'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    codice = db.Column(db.String(10), nullable=False)
    data_richiesta = db.Column(db.DateTime, default=datetime.now)
    usato = db.Column(db.Boolean, default=False)
    def to_dict(self):
        return {'id':self.id,'username':self.username,'codice':self.codice,
                'data_richiesta':self.data_richiesta.strftime("%Y-%m-%d %H:%M:%S") if self.data_richiesta else None,'usato':self.usato}

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    azione = db.Column(db.String(100), nullable=False)
    esito = db.Column(db.String(20), default='ok')
    attore = db.Column(db.String(80), nullable=False)
    target = db.Column(db.String(80))
    ip = db.Column(db.String(50))
    user_agent = db.Column(db.String(200))
    dettagli = db.Column(db.JSON)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {'id':self.id,'azione':self.azione,'esito':self.esito,'attore':self.attore,'target':self.target,
                'ip':self.ip,'user_agent':self.user_agent,'dettagli':self.dettagli,
                'timestamp':self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if self.timestamp else None}

class PrivacyRequest(db.Model):
    __tablename__ = 'privacy_requests'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    motivo = db.Column(db.Text)
    stato = db.Column(db.String(20), default='pending')
    data_richiesta = db.Column(db.DateTime, default=datetime.now)
    data_chiusura = db.Column(db.DateTime)
    admin_note = db.Column(db.Text)
    def to_dict(self):
        return {'id':self.id,'username':self.username,'motivo':self.motivo,'stato':self.stato,
                'admin_note':self.admin_note,
                'data_richiesta':self.data_richiesta.strftime("%Y-%m-%d %H:%M:%S") if self.data_richiesta else None,
                'data_chiusura':self.data_chiusura.strftime("%Y-%m-%d %H:%M:%S") if self.data_chiusura else None}

class NotificationPreference(db.Model):
    __tablename__ = 'notification_preferences'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    canale_in_app = db.Column(db.Boolean, default=True)
    canale_email = db.Column(db.Boolean, default=False)
    tipi = db.Column(db.JSON, default=lambda: {'ticket':True,'registrazione':True,'segnalazione':True,'sistema':True,'comment_reply':True,'preferiti':True})
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    def to_dict(self):
        return {'username':self.username,'canale_in_app':self.canale_in_app,'canale_email':self.canale_email,
                'tipi':self.tipi,'updated_at':self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else None}

class LoginHistory(db.Model):
    __tablename__ = 'login_histories'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    ip = db.Column(db.String(60))
    user_agent = db.Column(db.String(200))
    tipo = db.Column(db.String(20), default='login')
    quando = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {'id':self.id,'ip':self.ip,'user_agent':self.user_agent,'tipo':self.tipo,
                'quando':self.quando.strftime("%Y-%m-%d %H:%M:%S") if self.quando else None}

class BannedIP(db.Model):
    __tablename__ = 'banned_ips'
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(60), unique=True, nullable=False, index=True)
    motivo = db.Column(db.Text)
    creato_il = db.Column(db.DateTime, default=datetime.now)
    banned_by = db.Column(db.String(80))

class SiteBanner(db.Model):
    __tablename__ = 'site_banner'
    id = db.Column(db.Integer, primary_key=True)
    attivo = db.Column(db.Boolean, default=False)
    messaggio = db.Column(db.String(600), default='')
    aggiornato = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class TicketTemplate(db.Model):
    __tablename__ = 'ticket_templates'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    oggetto = db.Column(db.String(200))
    corpo = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.String(40), default='generale')

class StudyMaterial(db.Model):
    __tablename__ = 'study_materials'
    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, index=True)
    professore_nome = db.Column(db.String(120))
    materia = db.Column(db.String(100))
    scuola = db.Column(db.String(150), index=True)
    titolo = db.Column(db.String(200))
    nome_file_sicuro = db.Column(db.String(260))
    caricato_da = db.Column(db.String(80), nullable=False, index=True)
    mime = db.Column(db.String(80))
    dimensione = db.Column(db.Integer, default=0)
    quando = db.Column(db.DateTime, default=datetime.now)
    def to_dict(self):
        return {'id':self.id,'professor_id':self.professor_id,'professore_nome':self.professore_nome,'materia':self.materia,
                'scuola':self.scuola,'titolo':self.titolo,'caricato_da':self.caricato_da,
                'quando':self.quando.strftime("%Y-%m-%d %H:%M:%S") if self.quando else None}

class ExamEvent(db.Model):
    __tablename__ = 'exam_events'
    id = db.Column(db.Integer, primary_key=True)
    scuola = db.Column(db.String(150), nullable=False, index=True)
    group_id = db.Column(db.Integer, index=True)
    materia = db.Column(db.String(100))
    titolo = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text)
    quando = db.Column(db.DateTime, nullable=False)
    creato_da = db.Column(db.String(80), nullable=False)

class UserGroup(db.Model):
    __tablename__ = 'user_groups'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(130), unique=True, index=True)
    scuola = db.Column(db.String(150), nullable=False)
    creator = db.Column(db.String(80), nullable=False)
    descrizione = db.Column(db.Text)
    quando = db.Column(db.DateTime, default=datetime.now)

class GroupMember(db.Model):
    __tablename__ = 'group_members'
    group_id = db.Column(db.Integer, db.ForeignKey('user_groups.id'), primary_key=True)
    username = db.Column(db.String(80), primary_key=True)
    ruolo = db.Column(db.String(20), default='membro')

class UserFollow(db.Model):
    __tablename__ = 'user_follows'
    follower = db.Column(db.String(80), primary_key=True)
    followed = db.Column(db.String(80), primary_key=True)

class ProfessorFavorite(db.Model):
    __tablename__ = 'professor_favorites'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    professor_id = db.Column(db.Integer, index=True)
    chiave_prof = db.Column(db.String(260), nullable=False)
    professore_etichetta = db.Column(db.String(220))
    __table_args__ = (db.UniqueConstraint('username','chiave_prof', name='uq_user_prof_pref'),)

# =====================================================
# ============= FUNZIONI DI SUPPORTO ==================
# =====================================================

def password_hash(value):
    return generate_password_hash(value, method="pbkdf2:sha256", salt_length=16)

def password_verifica(password_input, password_salvata):
    if not password_salvata: return False
    if str(password_salvata).startswith("pbkdf2:sha256:"):
        return check_password_hash(password_salvata, password_input)
    return password_input == password_salvata

def is_password_hashed(value):
    return isinstance(value, str) and value.startswith("pbkdf2:sha256:")

def log_audit(azione, esito="ok", dettagli=None, target_username=None):
    log = AuditLog(azione=azione, esito=esito, attore=session.get("username","anonimo"),
                   target=target_username, ip=request.remote_addr or "0.0.0.0",
                   user_agent=request.headers.get("User-Agent","Unknown")[:120], dettagli=dettagli or {})
    db.session.add(log); db.session.commit()

def notices_attivi_query():
    now = datetime.now()
    return (Notice.query.filter(Notice.attivo == True).filter(or_(Notice.expires_at.is_(None), Notice.expires_at > now))
            .order_by(Notice.created_at.desc()))

def is_founder_user(username):
    return (username or "").strip().lower() == "admin"

def tax_upsert_subject(name, actor=None):
    n = (name or "").strip()
    if not n: return
    if not Subject.query.filter(Subject.nome.ilike(n)).first():
        db.session.add(Subject(nome=n[:120], created_by=(actor or session.get("username") or "system")))

def tax_upsert_school(name, actor=None):
    n = (name or "").strip()
    if not n: return
    if not School.query.filter(School.nome.ilike(n)).first():
        db.session.add(School(nome=n[:180], created_by=(actor or session.get("username") or "system")))

def elimina_utente_totale(username):
    """Elimina dati dell'account (privacy / admin)."""
    u = username.strip() if username else ""
    if not u: return
    if is_founder_user(u):
        return
    Vote.query.filter_by(username=u).delete()
    Review.query.filter_by(username=u).delete()
    Ticket.query.filter_by(utente=u).delete()
    Notification.query.filter_by(utente=u).delete()
    NotificationPreference.query.filter_by(username=u).delete()
    SessionLog.query.filter_by(username=u).delete()
    LoginHistory.query.filter_by(username=u).delete()
    PasswordRecovery.query.filter_by(username=u).delete()
    RegistrationRequest.query.filter_by(username=u).delete()
    PrivacyRequest.query.filter_by(username=u).delete()
    StudyMaterial.query.filter_by(caricato_da=u).delete()
    ExamEvent.query.filter_by(creato_da=u).delete()
    ProfessorFavorite.query.filter_by(username=u).delete()
    UserFollow.query.filter(or_(UserFollow.follower == u, UserFollow.followed == u)).delete(synchronize_session=False)
    gm = GroupMember.query.filter_by(username=u).all()
    for row in gm: db.session.delete(row)
    for g in list(UserGroup.query.filter_by(creator=u)):
        GroupMember.query.filter_by(group_id=g.id).delete()
        db.session.delete(g)
    User.query.filter_by(username=u).delete()
    db.session.commit()

# Rate limiting login
LOGIN_ATTEMPTS = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_BLOCK_MINUTES = 10

# --- Anti-abuso POST (azioni autenticate) ---
SPAM_BUCKET = defaultdict(list)

def spam_allow(actor, bucket, max_evt, window_sec=86400):
    actor = actor or ("ip:" + (request.remote_addr or "na"))
    key = (str(actor), str(bucket)); now_ts = datetime.now().timestamp()
    arr = [t for t in SPAM_BUCKET.get(key, []) if now_ts - t < window_sec]
    if len(arr) >= max_evt:
        return False
    arr.append(now_ts); SPAM_BUCKET[key] = arr
    return True

def login_rate_key(username):
    return f"{request.remote_addr or '0.0.0.0'}:{username.lower().strip()}"

def login_is_blocked(username):
    key = login_rate_key(username); record = LOGIN_ATTEMPTS.get(key)
    if not record: return False, 0
    blocked_until = record.get("blocked_until")
    if blocked_until and datetime.now() < blocked_until:
        return True, int((blocked_until - datetime.now()).total_seconds() // 60) + 1
    if blocked_until: LOGIN_ATTEMPTS.pop(key, None)
    return False, 0

def login_register_failure(username):
    key = login_rate_key(username)
    record = LOGIN_ATTEMPTS.get(key, {"count":0,"blocked_until":None})
    record["count"] += 1
    if record["count"] >= LOGIN_MAX_ATTEMPTS:
        record["blocked_until"] = datetime.now() + timedelta(minutes=LOGIN_BLOCK_MINUTES)
    LOGIN_ATTEMPTS[key] = record

def login_clear_attempts(username):
    LOGIN_ATTEMPTS.pop(login_rate_key(username), None)

def default_preferenze_notifiche(username):
    return {"username":username,"canale_in_app":True,"canale_email":False,
            "tipi":{"ticket":True,"registrazione":True,"segnalazione":True,"sistema":True,
                    "comment_reply":True,"preferiti":True},
            "updated_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

@app.before_request
def verifica_sessione_server():
    if request.endpoint is None:
        return
    rip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (request.remote_addr or "0.0.0.0")
    try:
        if BannedIP.query.filter_by(ip=rip).first():
            abort(403)
    except Exception:
        pass
    EXEMPT_SESSION = frozenset({
        "login","logout","home","index","registrazione","recupero_password","contatti_page","privacy_informativa","static",
        "oauth_google_start","oauth_google_callback","oauth_microsoft_start","oauth_microsoft_callback",
        "oauth_facebook_start","oauth_facebook_callback","debug_info","debug_tickets","uploads_avatars_public"})
    if request.endpoint in EXEMPT_SESSION:
        return
    if not session.get("username"):
        return
    if SessionLog.query.filter_by(username=session["username"]).first() is None:
        session.clear(); return redirect(url_for("login"))
    sid = session.get("session_id"); row = SessionLog.query.filter_by(username=session["username"]).first()
    if not row or not sid or row.session_id != sid:
        session.clear(); return redirect(url_for("login"))

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self' https: 'unsafe-inline' 'unsafe-eval' data: blob:; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

def genera_captcha():
    a, b = random.randint(1,10), random.randint(1,10)
    return {"domanda": f"{a} + {b} = ?", "risposta": a+b}

def genera_codice_recupero():
    return str(random.randint(100000, 999999))

def genera_session_id():
    return secrets.token_urlsafe(32)

def registra_sessione(username):
    session_id = genera_session_id()
    rip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (request.remote_addr or "0.0.0.0")
    ua = request.headers.get("User-Agent","Unknown")[:200]
    db.session.add(LoginHistory(username=username, ip=rip, user_agent=ua[:200], tipo="login"))
    SessionLog.query.filter_by(username=username).delete()
    nuova = SessionLog(session_id=session_id, username=username, ip=rip,
                       login_time=datetime.now(), last_activity=datetime.now(),
                       user_agent=ua[:100])
    db.session.add(nuova); db.session.commit()
    return session_id

def aggiorna_attivita_sessione():
    if "username" not in session: return
    s = SessionLog.query.filter_by(username=session["username"]).order_by(SessionLog.login_time.desc()).first()
    if s: s.last_activity = datetime.now(); db.session.commit()

def pulisci_sessioni_scadute():
    cutoff = datetime.now() - timedelta(days=1)
    SessionLog.query.filter(SessionLog.last_activity < cutoff).delete(); db.session.commit()

def crea_notifica(username, tipo, titolo, messaggio, link=None):
    pref = NotificationPreference.query.filter_by(username=username).first()
    if pref and not pref.canale_in_app: return None
    if pref and not pref.tipi.get(tipo, True): return None
    notif = Notification(utente=username, tipo=tipo, titolo=titolo, messaggio=messaggio, link=link)
    db.session.add(notif); db.session.commit()
    return {"id":notif.id,"tipo":tipo,"titolo":titolo,"messaggio":messaggio}

# Email config
SMTP_SERVER = "smtp-mail.outlook.com"
SMTP_PORT = 587
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "assistenzaregistroprof@outlook.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "assistenzaregistroprof@outlook.com")
SITE_URL = os.getenv("SITE_URL", "http://localhost:5000")

def invia_email_ticket(ticket):
    if not EMAIL_PASSWORD: return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎫 Nuovo Ticket #{ticket['id']}: {ticket['oggetto']}"
        msg["From"] = EMAIL_SENDER; msg["To"] = EMAIL_RECEIVER
        ticket_link = f"{SITE_URL}/admin#ticket-{ticket['id']}"
        html = f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
            <div style="background:linear-gradient(135deg,#667eea,#764ba2);padding:20px;border-radius:10px 10px 0 0;color:white;">
                <h2 style="margin:0;">🎫 Nuovo Ticket di Supporto</h2><p style="margin:5px 0 0 0;opacity:0.9;">Ticket #{ticket['id']}</p></div>
            <div style="background:#f8f9fa;padding:25px;border:1px solid #e0e0e0;border-top:none;">
                <p><strong>Utente:</strong> {ticket['utente']}</p><p><strong>Oggetto:</strong> {ticket['oggetto']}</p>
                <p><strong>Messaggio:</strong> {ticket['messaggio']}</p>
                <div style="text-align:center;margin:30px 0;">
                    <a href="{ticket_link}" style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:14px 35px;text-decoration:none;border-radius:25px;font-weight:bold;display:inline-block;">👁️ Visualizza Ticket</a></div></div></body></html>"""
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(); server.login(EMAIL_SENDER, EMAIL_PASSWORD); server.send_message(msg)
        return True
    except Exception as e:
        print(f"[email] Errore: {e}"); return False

def invia_email_newsletter(destinatario, subject, html_body):
    if not EMAIL_PASSWORD: return False
    if not destinatario or "@" not in destinatario: return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject[:200], EMAIL_SENDER, destinatario.strip()
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(); server.login(EMAIL_SENDER, EMAIL_PASSWORD); server.send_message(msg)
        return True
    except Exception as e:
        print(f"[email_newsletter] {e}"); return False

def ensure_banner_singleton():
    if SiteBanner.query.get(1) is None:
        db.session.add(SiteBanner(id=1, attivo=False, messaggio=""))
        db.session.commit()

# =====================================================
# ============= ROUTES PRINCIPALI =====================
# =====================================================

@app.route("/")
def index():
    return redirect(url_for("home"))

@app.route("/contatti")
def contatti_page():
    return render_template("contatti.html")

@app.route("/uploads/public/avatars/<fname>")
def uploads_avatars_public(fname):
    fn = secure_filename(fname)
    if fn != fname or not fn:
        abort(404)
    full = os.path.join(AVATAR_FOLDER, fn)
    if not os.path.isfile(full):
        abort(404)
    return send_from_directory(AVATAR_FOLDER, fn, max_age=3600)

@app.route("/informativa-privacy")
@app.route("/privacy-policy")
def privacy_informativa():
    return render_template("privacy_informativa.html")

@app.route("/oauth/google/start")
def oauth_google_start():
    if oauth_google is None:
        return redirect(url_for("login"))
    return oauth_google.authorize_redirect(url_for("oauth_google_callback", _external=True))

@app.route("/oauth/google/callback")
def oauth_google_callback():
    if oauth_google is None:
        return redirect(url_for("login"))
    try:
        token = oauth_google.authorize_access_token()
        ui = token.get("userinfo")
        if not ui:
            resp = oauth_google.get("userinfo"); ui = resp.json()
        email = ui.get("email"); sub = ui.get("sub"); nome = ui.get("name")
        user, err = oauth_find_or_create("google", sub, email, nome)
        if err or not user: return redirect(url_for("login"))
        return oauth_finalize_redirect(user)
    except Exception as e:
        print(f"[oauth google] {e}"); log_audit("oauth_google_fail", esito="ko"); return redirect(url_for("login"))

@app.route("/oauth/microsoft/start")
def oauth_microsoft_start():
    if oauth_ms is None:
        return redirect(url_for("login"))
    return oauth_ms.authorize_redirect(url_for("oauth_microsoft_callback", _external=True))

@app.route("/oauth/microsoft/callback")
def oauth_microsoft_callback():
    if oauth_ms is None:
        return redirect(url_for("login"))
    try:
        token = oauth_ms.authorize_access_token()
        ui = token.get("userinfo")
        if not ui:
            resp = oauth_ms.get("https://graph.microsoft.com/oidc/userinfo"); ui = resp.json()
        email = ui.get("email"); sub = ui.get("sub"); nome = ui.get("name")
        user, err = oauth_find_or_create("microsoft", sub, email, nome)
        if err or not user: return redirect(url_for("login"))
        return oauth_finalize_redirect(user)
    except Exception as e:
        print(f"[oauth ms] {e}"); log_audit("oauth_microsoft_fail", esito="ko"); return redirect(url_for("login"))

@app.route("/oauth/facebook/start")
def oauth_facebook_start():
    if oauth_fb is None:
        return redirect(url_for("login"))
    return oauth_fb.authorize_redirect(url_for("oauth_facebook_callback", _external=True))

@app.route("/oauth/facebook/callback")
def oauth_facebook_callback():
    if oauth_fb is None:
        return redirect(url_for("login"))
    try:
        token = oauth_fb.authorize_access_token()
        resp = oauth_fb.get("me", params={"fields": "id,name,email"})
        ui = resp.json()
        email = ui.get("email"); sub = ui.get("id"); nome = ui.get("name")
        user, err = oauth_find_or_create("facebook", str(sub), email, nome)
        if err or not user: return redirect(url_for("login"))
        return oauth_finalize_redirect(user)
    except Exception as e:
        print(f"[oauth fb] {e}"); log_audit("oauth_facebook_fail", esito="ko"); return redirect(url_for("login"))

@app.route("/home")
def home():
    avvisi = notices_attivi_query().all()
    return render_template("index.html", avvisi=[a.to_dict() for a in avvisi])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Step 2FA per admin (opzionale via env ENABLE_ADMIN_2FA=true)
        otp_mode = (request.form.get("otp_mode") or "").strip() == "1"
        if otp_mode:
            otp_user = (session.get("pending_2fa_user") or "").strip()
            otp_code = (request.form.get("otp_code") or "").strip()
            exp = int(session.get("pending_2fa_exp") or 0)
            if not otp_user or not otp_code:
                return render_template("login.html", error="Codice OTP mancante", need_otp=True, otp_username=otp_user)
            if int(datetime.now().timestamp()) > exp:
                session.pop("pending_2fa_user", None); session.pop("pending_2fa_code", None); session.pop("pending_2fa_exp", None)
                return render_template("login.html", error="Codice OTP scaduto. Effettua di nuovo il login.")
            if otp_code != (session.get("pending_2fa_code") or ""):
                log_audit("login_2fa_failed", esito="ko", target_username=otp_user)
                return render_template("login.html", error="Codice OTP non valido", need_otp=True, otp_username=otp_user)
            user = User.query.filter_by(username=otp_user).first()
            if not user:
                return render_template("login.html", error="Utente non trovato")
            session.pop("pending_2fa_user", None); session.pop("pending_2fa_code", None); session.pop("pending_2fa_exp", None)
            session["username"] = user.username; session["role"] = user.role
            session["session_id"] = registra_sessione(user.username)
            login_clear_attempts(user.username); log_audit("login_2fa_success", target_username=user.username)
            return redirect(url_for("admin_dashboard" if user.role == "admin" else "user_dashboard"))

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        bloccato, minuti = login_is_blocked(username)
        if bloccato:
            return render_template("login.html", error=f"Troppi tentativi. Riprova tra circa {minuti} minuti.")
        user = User.query.filter_by(username=username).first()
        if user and password_verifica(password, user.password):
            if user.stato == "in_attesa":
                log_audit("login_bloccato_in_attesa", esito="ko", target_username=username)
                return render_template("login.html", error="⏳ Il tuo account è in attesa di approvazione.")
            if user.account_status == "sospeso":
                log_audit("login_bloccato_sospeso", esito="ko", target_username=username)
                return render_template("login.html", error="⚠️ Il tuo account è SOSPESO.")
            if user.account_status == "bannato":
                log_audit("login_bloccato_bannato", esito="ko", target_username=username)
                return render_template("login.html", error="❌ Il tuo account è BANNATO.")
            if not is_password_hashed(user.password):
                user.password = password_hash(password); db.session.commit()
            if user.role == "admin" and os.getenv("ENABLE_ADMIN_2FA", "").lower() in ("1", "true", "yes"):
                otp = f"{random.randint(0, 999999):06d}"
                session["pending_2fa_user"] = username
                session["pending_2fa_code"] = otp
                session["pending_2fa_exp"] = int((datetime.now() + timedelta(minutes=7)).timestamp())
                destinatario = (user.email or "").strip()
                html = f"<p>Codice OTP RegistroProf: <strong>{otp}</strong></p><p>Scade tra 7 minuti.</p>"
                invia_email_newsletter(destinatario, "Codice OTP amministratore - RegistroProf", html)
                return render_template("login.html", need_otp=True, otp_username=username, info="Codice OTP inviato via email.")
            session["username"] = username; session["role"] = user.role
            session["session_id"] = registra_sessione(username)
            login_clear_attempts(username); log_audit("login_success", target_username=username)
            return redirect(url_for("admin_dashboard" if user.role == "admin" else "user_dashboard"))
        login_register_failure(username); log_audit("login_failed", esito="ko", target_username=username)
        return render_template("login.html", error="Credenziali errate")
    return render_template("login.html", need_otp=False)

@app.route("/recupero-password", methods=["GET", "POST"])
def recupero_password():
    if request.method == "POST":
        step = request.form.get("step", "1")
        if step == "1":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            utente = User.query.filter_by(username=username, email=email).first()
            if not utente:
                return render_template("recupero_password.html", errore="Username o email non trovati", step=1)
            codice = genera_codice_recupero()
            db.session.add(PasswordRecovery(username=username, codice=codice)); db.session.commit()
            return render_template("recupero_password.html", step=2, username=username, codice_mostrato=codice)
        elif step == "2":
            username = request.form.get("username", "").strip()
            codice_inserito = request.form.get("codice", "").strip()
            recupero = PasswordRecovery.query.filter_by(username=username, codice=codice_inserito, usato=False).first()
            if not recupero:
                return render_template("recupero_password.html", errore="Codice non valido", step=2, username=username)
            return render_template("recupero_password.html", step=3, username=username, codice=codice_inserito)
        elif step == "3":
            username = request.form.get("username", "").strip()
            nuova_password = request.form.get("nuova_password", "").strip()
            conferma_password = request.form.get("conferma_password", "").strip()
            if not nuova_password or len(nuova_password) < 4:
                return render_template("recupero_password.html", errore="Password minima 4 caratteri", step=3, username=username)
            if nuova_password != conferma_password:
                return render_template("recupero_password.html", errore="Le password non coincidono", step=3, username=username)
            utente = User.query.filter_by(username=username).first()
            if utente: utente.password = password_hash(nuova_password); db.session.commit()
            PasswordRecovery.query.filter_by(username=username, usato=False).update({"usato": True}); db.session.commit()
            return render_template("recupero_password.html", successo="✅ Password aggiornata!")
    return render_template("recupero_password.html", step=1)

@app.route("/registrazione", methods=["GET", "POST"])
def registrazione():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        conferma_password = request.form.get("conferma_password", "").strip()
        email = request.form.get("email", "").strip()
        nome_cognome = request.form.get("nome_cognome", "").strip()
        scuola = request.form.get("scuola", "").strip()
        captcha_risposta = request.form.get("captcha_risposta", "").strip()
        captcha_sessione = session.get("captcha", {}); errori = []
        if not username or len(username) < 3: errori.append("Username minimo 3 caratteri")
        if not password or len(password) < 4: errori.append("Password minima 4 caratteri")
        if password != conferma_password: errori.append("Le password non coincidono")
        if not email or "@" not in email: errori.append("Email non valida")
        if not nome_cognome: errori.append("Nome e cognome obbligatori")
        if not scuola: errori.append("Scuola obbligatoria")
        try:
            if int(captcha_risposta) != captcha_sessione.get("risposta"): errori.append("CAPTCHA errato")
        except: errori.append("CAPTCHA errato")
        if User.query.filter_by(username=username).first() or RegistrationRequest.query.filter_by(username=username, stato="in_attesa").first():
            errori.append("Username già esistente o in attesa")
        if User.query.filter_by(email=email).first(): errori.append("Email già registrata")
        if errori:
            session["captcha"] = genera_captcha()
            return render_template("registrazione.html", errori=errori, dati_inviati=request.form, captcha_domanda=session["captcha"]["domanda"])
        db.session.add(RegistrationRequest(username=username, password=password_hash(password), email=email,
                                           nome_cognome=nome_cognome, scuola=scuola, stato="in_attesa"))
        db.session.commit(); session["captcha"] = genera_captcha()
        return render_template("registrazione.html", successo="✅ Registrazione inviata! Attendi approvazione.", captcha_domanda=session["captcha"]["domanda"])
    session["captcha"] = genera_captcha()
    return render_template("registrazione.html", captcha_domanda=session["captcha"]["domanda"])

@app.route("/logout")
def logout():
    if "username" in session: SessionLog.query.filter_by(username=session["username"]).delete(); db.session.commit()
    session.clear(); return redirect(url_for("home"))

@app.route("/user")
def user_dashboard():
    if "username" not in session or session.get("role") != "user": return redirect(url_for("login"))
    aggiorna_attivita_sessione()
    avvisi = notices_attivi_query().all()
    return render_template("server.html", username=session["username"], avvisi=[a.to_dict() for a in avvisi])

@app.route("/admin")
def admin_dashboard():
    if "username" not in session or session.get("role") != "admin": return redirect(url_for("login"))
    aggiorna_attivita_sessione(); pulisci_sessioni_scadute()
    return render_template("admin.html", username=session["username"])

# =====================================================
# ============= API - FUNZIONI DI CONTROLLO ===========
# =====================================================

def login_required(): return "username" in session
def admin_required(): return login_required() and session.get("role") == "admin"

@app.context_processor
def ctx_site_banner():
    try:
        banner = SiteBanner.query.get(1)
    except Exception:
        banner = None
    return {"site_banner": banner, "oauth_google_ok": oauth_google is not None, "oauth_ms_ok": oauth_ms is not None,
            "oauth_fb_ok": oauth_fb is not None}

# --- Debug (solo con ENABLE_DEBUG_ROUTES=true nell'ambiente) ---
@app.route("/debug/info")
def debug_info():
    if os.getenv("ENABLE_DEBUG_ROUTES", "").lower() not in ("1", "true", "yes"):
        return redirect(url_for("home"))
    return jsonify({"BASE_DIR":BASE_DIR,"DATABASE_URL":app.config["SQLALCHEMY_DATABASE_URI"],
                    "DB_FILE_EXISTS":os.path.exists(os.path.join(BASE_DIR,"registro.db")) if "sqlite" in app.config["SQLALCHEMY_DATABASE_URI"] else None,
                    "SESSION":{"username":session.get("username"),"role":session.get("role")},
                    "TIMESTAMP":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

@app.route("/debug/tickets")
def debug_tickets():
    if os.getenv("ENABLE_DEBUG_ROUTES", "").lower() not in ("1", "true", "yes"):
        return redirect(url_for("home"))
    if not login_required(): return jsonify({"error":"Login richiesto"}), 403
    all_t = Ticket.query.all(); user_t = Ticket.query.filter_by(utente=session["username"]).all()
    return jsonify({"total":len(all_t),"user_visible":len(user_t),"all":[t.to_dict() for t in all_t],
                    "current_user":session["username"],"role":session.get("role")})

# =====================================================
# ============= API - NOTIFICHE =======================
# =====================================================

@app.route("/api/notifiche", methods=["GET"])
def api_notifiche():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    notifiche = Notification.query.filter_by(utente=session["username"]).order_by(Notification.data.desc()).all()
    return jsonify([n.to_dict() for n in notifiche])

@app.route("/api/notifiche/<int:id>", methods=["PUT"])
def api_notifica_letta(id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    n = Notification.query.get(id)
    if not n or n.utente != session["username"]: return jsonify({"error":"Non trovata"}), 404
    n.letta, n.data_lettura = True, datetime.now(); db.session.commit()
    return jsonify({"success":True})

@app.route("/api/notifiche/segna-tutte-lette", methods=["POST"])
def api_notifiche_tutte_lette():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    Notification.query.filter_by(utente=session["username"], letta=False).update({"letta":True,"data_lettura":datetime.now()})
    db.session.commit(); return jsonify({"success":True})

@app.route("/api/notifiche/preferenze", methods=["GET", "PUT"])
def api_notifiche_preferenze():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    pref = NotificationPreference.query.filter_by(username=session["username"]).first()
    if not pref: pref = NotificationPreference(username=session["username"]); db.session.add(pref); db.session.commit()
    if request.method == "GET": return jsonify(pref.to_dict())
    data = request.get_json() or {}
    if "canale_in_app" in data: pref.canale_in_app = bool(data["canale_in_app"])
    if "canale_email" in data: pref.canale_email = bool(data["canale_email"])
    if "tipi" in data and isinstance(data["tipi"], dict):
        for k,v in data["tipi"].items(): pref.tipi[k] = bool(v)
    db.session.commit(); return jsonify({"success":True,"preferenze":pref.to_dict()})

# =====================================================
# ============= API - TICKET ==========================
# =====================================================

@app.route("/api/ticket", methods=["GET", "POST"])
def api_ticket():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if request.method == "GET":
        stato = request.args.get("stato", "").strip()
        query = Ticket.query if session["role"] == "admin" else Ticket.query.filter_by(utente=session["username"])
        if stato: query = query.filter_by(stato=stato)
        return jsonify([t.to_dict() for t in query.order_by(Ticket.data_apertura.desc()).all()])
    elif request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        target_u = session["username"]
        if admin_required():
            target_u = (data.get("utente") or "").strip()
            if not target_u or not User.query.filter_by(username=target_u).first():
                return jsonify({"error":"Specifica un utente esistente (campo utente)"}), 400
        nuovo = Ticket(utente=target_u, oggetto=data.get("oggetto",""), messaggio=data.get("messaggio",""),
                       priorita=data.get("priorita","media"), stato="aperto")
        db.session.add(nuovo); db.session.commit()
        for admin in User.query.filter_by(role="admin").all():
            crea_notifica(admin.username, "ticket", f"🎫 Nuovo Ticket #{nuovo.id}", f"{session['username']} per {target_u}: {nuovo.oggetto}", "/admin#ticket")
        if admin_required() and target_u != session["username"]:
            crea_notifica(target_u, "ticket", "📩 Ticket aperto dal supporto", f"È stato registrato un ticket (#{nuovo.id}) a tuo nome.", "/user#ticket")
        invia_email_ticket(nuovo.to_dict())
        return jsonify({"success":True,"ticket_id":nuovo.id})

@app.route("/api/ticket/<int:id>", methods=["GET", "PUT", "DELETE"])
def api_ticket_dettaglio(id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    ticket = Ticket.query.get(id)
    if not ticket: return jsonify({"error":"Non trovato"}), 404
    is_admin, is_creatore = session["role"]=="admin", ticket.utente==session["username"]
    if not is_admin and not is_creatore: return jsonify({"error":"Non autorizzato"}), 403
    if request.method == "GET": return jsonify(ticket.to_dict())
    elif request.method == "PUT":
        data = request.get_json()
        if is_admin:
            if "stato" in data and data["stato"] in ["aperto","in_lavorazione","risolto","chiuso"]:
                ticket.stato = data["stato"]
                if data["stato"] in ["risolto","chiuso"]: ticket.data_chiusura = datetime.now()
            if "risposta" in data and data["risposta"]:
                risposte = list(ticket.risposte or [])
                risposte.append({"da":"admin","admin":session["username"],"messaggio":data["risposta"],"data":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                ticket.risposte = risposte
                crea_notifica(ticket.utente, "ticket", "💬 Risposta al tuo ticket", f"Admin {session['username']} ha risposto al ticket #{id}", "/user#ticket")
        elif is_creatore and ticket.stato == "aperto" and "messaggio" in data and data["messaggio"]:
            risposte = list(ticket.risposte or [])
            risposte.append({"da":"utente","utente":session["username"],"messaggio":data["messaggio"],"data":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            ticket.risposte = risposte
        db.session.commit(); return jsonify({"success":True})
    elif request.method == "DELETE":
        if is_admin or (is_creatore and len(ticket.risposte)==0):
            db.session.delete(ticket); db.session.commit(); return jsonify({"success":True})
        return jsonify({"error":"Non puoi eliminare ticket con risposte"}), 403

# =====================================================
# ============= API - REGISTRAZIONI ===================
# =====================================================

@app.route("/api/registrazioni", methods=["GET", "POST"])
def api_registrazioni_lista():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    if request.method == "GET":
        return jsonify([r.to_dict() for r in RegistrationRequest.query.order_by(RegistrationRequest.data_registrazione.desc()).all()])
    data = request.get_json(force=True, silent=True) or {}
    nu = RegistrationRequest(username=(data.get("username") or "").strip(),
                             password=password_hash(str(data.get("password") or "changeme")), email=data.get("email"),
                             nome_cognome=data.get("nome_cognome"), scuola=data.get("scuola"), stato="in_attesa")
    if len(nu.username) < 3: return jsonify({"error":"Username troppo corto"}), 400
    if User.query.filter_by(username=nu.username).first(): return jsonify({"error":"Username già in uso"}), 400
    db.session.add(nu); db.session.commit(); log_audit("reg_admin_create")
    return jsonify({"success":True,"richiesta":nu.to_dict()})

@app.route("/api/registrazioni/<int:id>", methods=["PUT", "DELETE"])
def api_registrazioni_gestisci(id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    reg = RegistrationRequest.query.get(id)
    if not reg: return jsonify({"error":"Non trovata"}), 404
    if request.method == "DELETE": db.session.delete(reg); db.session.commit(); return jsonify({"success":True})
    if request.method == "PUT":
        data = request.get_json()
        if data.get("stato") in ["approvato","rifiutato"]:
            reg.stato, reg.admin_note = data["stato"], data.get("admin_note","")
            if data["stato"] == "approvato":
                reg.data_approvazione = datetime.now()
                nuovo_user = User(username=reg.username, password=reg.password, email=reg.email,
                                  nome_cognome=reg.nome_cognome, scuola=reg.scuola, role="user",
                                  stato="attivo", account_status="attivo", created_at=datetime.now())
                db.session.add(nuovo_user)
                crea_notifica(reg.username, "registrazione", "✅ Registrazione approvata", "Il tuo account è attivo!", "/login")
            db.session.commit(); return jsonify({"success":True})
        return jsonify({"error":"Stato non valido"}), 400

@app.route("/api/registrazioni/<int:id>/modifica", methods=["PUT"])
def api_registrazioni_modifica(id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    reg = RegistrationRequest.query.get(id)
    if not reg: return jsonify({"error":"Non trovata"}), 404
    data = request.get_json()
    for campo in ["username","email","nome_cognome","scuola"]:
        if campo in data: setattr(reg, campo, data[campo])
    if "password" in data and data["password"]: reg.password = password_hash(data["password"])
    db.session.commit(); return jsonify({"success":True})

# =====================================================
# ============= API - UTENTI ==========================
# =====================================================

@app.route("/api/utenti", methods=["GET"])
def api_utenti_lista():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    utenti = User.query.all()
    return jsonify([{"id":u.id,"username":u.username,"role":u.role,
                     "account_status": getattr(u,"account_status","attivo"),
                     "created_at":u.created_at.strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "N/A",
                     "voti_count":Vote.query.filter_by(username=u.username).count(),
                     "recensioni_count":Review.query.filter_by(username=u.username).count()} for u in utenti])

@app.route("/api/ruoli", methods=["GET", "POST"])
def api_ruoli():
    if request.method == "GET":
        rows = RoleDef.query.order_by(RoleDef.nome.asc()).all()
        return jsonify([{"id": r.id, "nome": r.nome, "is_system": bool(r.is_system)} for r in rows])
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip().lower()
    if not nome or len(nome) < 3: return jsonify({"error":"Nome ruolo non valido"}), 400
    if nome in ("admin","user"): return jsonify({"error":"Ruolo di sistema"}), 400
    if RoleDef.query.filter_by(nome=nome).first(): return jsonify({"error":"Ruolo già esistente"}), 400
    r = RoleDef(nome=nome, is_system=False); db.session.add(r); db.session.commit()
    return jsonify({"success":True,"ruolo":{"id":r.id,"nome":r.nome}})

@app.route("/api/ruoli/<int:rid>", methods=["PUT", "DELETE"])
def api_ruoli_mod(rid):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    r = RoleDef.query.get(rid)
    if not r: return jsonify({"error":"Ruolo non trovato"}), 404
    if r.nome in ("admin", "user") and not is_founder_user(session.get("username")):
        return jsonify({"error":"Solo il founder può modificare i ruoli di sistema"}), 403
    if request.method == "DELETE":
        if User.query.filter_by(role=r.nome).count() > 0:
            return jsonify({"error":"Ruolo assegnato ad almeno un utente"}), 400
        db.session.delete(r); db.session.commit(); return jsonify({"success":True})
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip().lower()
    if not nome or len(nome) < 3: return jsonify({"error":"Nome ruolo non valido"}), 400
    if nome in ("admin","user"): return jsonify({"error":"Nome ruolo non consentito"}), 400
    if RoleDef.query.filter(RoleDef.nome == nome, RoleDef.id != r.id).first(): return jsonify({"error":"Ruolo già esistente"}), 400
    old = r.nome; r.nome = nome
    User.query.filter_by(role=old).update({"role": nome})
    db.session.commit()
    return jsonify({"success":True})

@app.route("/api/materie", methods=["GET", "POST"])
def api_materie():
    if request.method == "GET":
        return jsonify([m.to_dict() for m in Subject.query.order_by(Subject.nome.asc()).all()])
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip()
    if not nome: return jsonify({"error":"Nome materia obbligatorio"}), 400
    tax_upsert_subject(nome, session.get("username")); db.session.commit()
    return jsonify({"success":True})

@app.route("/api/materie/<int:mid>", methods=["PUT", "DELETE"])
def api_materie_mod(mid):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    m = Subject.query.get(mid)
    if not m: return jsonify({"error":"Materia non trovata"}), 404
    if request.method == "DELETE":
        db.session.delete(m); db.session.commit(); return jsonify({"success":True})
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip()
    if not nome: return jsonify({"error":"Nome materia obbligatorio"}), 400
    m.nome = nome[:120]; db.session.commit(); return jsonify({"success":True})

@app.route("/api/scuole", methods=["GET", "POST"])
def api_scuole():
    if request.method == "GET":
        return jsonify([s.to_dict() for s in School.query.order_by(School.nome.asc()).all()])
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip()
    if not nome: return jsonify({"error":"Nome scuola obbligatorio"}), 400
    tax_upsert_school(nome, session.get("username")); db.session.commit()
    return jsonify({"success":True})

@app.route("/api/scuole/<int:sid>", methods=["PUT", "DELETE"])
def api_scuole_mod(sid):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    s = School.query.get(sid)
    if not s: return jsonify({"error":"Scuola non trovata"}), 404
    if request.method == "DELETE":
        db.session.delete(s); db.session.commit(); return jsonify({"success":True})
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip()
    if not nome: return jsonify({"error":"Nome scuola obbligatorio"}), 400
    s.nome = nome[:180]; db.session.commit(); return jsonify({"success":True})

@app.route("/api/utenti/<int:user_id>", methods=["PUT", "DELETE"])
def api_utenti_mod(user_id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    utente = User.query.get(user_id)
    if not utente: return jsonify({"error":"Non trovato"}), 404
    if is_founder_user(utente.username) and session.get("username") != utente.username:
        return jsonify({"error":"L'account founder può essere gestito solo dal proprietario"}), 403
    if request.method == "DELETE":
        if is_founder_user(utente.username):
            return jsonify({"error":"Impossibile eliminare account founder"}), 403
        username_eliminato = utente.username; db.session.delete(utente); db.session.commit()
        return jsonify({"success":True,"message":f"Utente {username_eliminato} eliminato"})
    if request.method == "PUT":
        data = request.get_json()
        if "role" in data and (RoleDef.query.filter_by(nome=data["role"]).first() or data["role"] in ("user","admin")):
            utente.role = data["role"]; db.session.commit()
            return jsonify({"success":True,"message":f"Ruolo aggiornato a {data['role']}"})
        return jsonify({"error":"Dati non validi"}), 400

@app.route("/api/utenti/crea", methods=["POST"])
def api_utenti_crea():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    data = request.get_json()
    username, password, role = data.get("username","").strip(), data.get("password","").strip(), data.get("role","user")
    if not username or not password: return jsonify({"error":"Username e password richiesti"}), 400
    if not (RoleDef.query.filter_by(nome=role).first() or role in ("user","admin")): return jsonify({"error":"Ruolo non valido"}), 400
    if User.query.filter_by(username=username).first(): return jsonify({"error":"Username già esistente"}), 400
    nuovo = User(username=username, password=password_hash(password), role=role, account_status="attivo", created_at=datetime.now())
    db.session.add(nuovo); db.session.commit()
    return jsonify({"success":True,"message":f"Utente {username} creato"})

@app.route("/api/utenti/<int:user_id>/credenziali", methods=["PUT"])
def api_utenti_modifica_credenziali(user_id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    utente = User.query.get(user_id)
    if not utente: return jsonify({"error":"Non trovato"}), 404
    if is_founder_user(utente.username) and session.get("username") != utente.username:
        return jsonify({"error":"L'account founder può essere gestito solo dal proprietario"}), 403
    data = request.get_json()
    if "username" in data and data["username"].strip():
        if User.query.filter_by(username=data["username"].strip()).first(): return jsonify({"error":"Username già esistente"}), 400
        utente.username = data["username"].strip()
    if "password" in data and data["password"]: utente.password = password_hash(data["password"])
    db.session.commit(); return jsonify({"success":True,"username":utente.username})

@app.route("/api/utenti-anagrafica/<int:user_id>", methods=["GET", "PUT"])
def api_utenti_anagrafica(user_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    utente = User.query.get(user_id)
    if not utente: return jsonify({"error":"Non trovato"}), 404
    if request.method == "GET":
        stats = {"voti_count":Vote.query.filter_by(username=utente.username).count(),
                 "recensioni_count":Review.query.filter_by(username=utente.username).count(),
                 "sessioni_attive":SessionLog.query.filter_by(username=utente.username).count()}
        last_login = SessionLog.query.filter_by(username=utente.username).order_by(SessionLog.login_time.desc()).first()
        return jsonify({**utente.to_dict(include_sensitive=admin_required()),
                        "last_login":last_login.login_time.strftime("%Y-%m-%d %H:%M:%S") if last_login else "N/A", **stats})
    elif request.method == "PUT":
        if not admin_required(): return jsonify({"error":"Solo admin"}), 403
        if is_founder_user(utente.username) and session.get("username") != utente.username:
            return jsonify({"error":"L'account founder può essere gestito solo dal proprietario"}), 403
        data = request.get_json()
        if "role" in data and data["role"] and not RoleDef.query.filter_by(nome=data["role"]).first():
            return jsonify({"error":"Ruolo non valido"}), 400
        for campo in ["email","nome_cognome","scuola","telefono","data_nascita","indirizzo","citta","cap","account_status","admin_note","role"]:
            if campo in data: setattr(utente, campo, data[campo])
        if "password" in data and data["password"]: utente.password = password_hash(data["password"])
        db.session.commit()
        if "account_status" in data or "role" in data:
            msg = {"sospeso":"Il tuo account è stato sospeso.","bannato":"Il tuo account è stato bannato.","attivo":"Il tuo account è stato riattivato."}.get(data.get("account_status"))
            if msg: crea_notifica(utente.username, "sistema", "⚙️ Stato account aggiornato", msg, "/user" if data.get("account_status")=="attivo" else "/login")
        return jsonify({"success":True})

@app.route("/api/profilo", methods=["GET", "PUT"])
def api_profilo():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    utente = User.query.filter_by(username=session["username"]).first()
    if not utente: return jsonify({"error":"Non trovato"}), 404
    if request.method == "GET":
        pend = bool(PrivacyRequest.query.filter_by(username=utente.username, stato="pending").first())
        av = (utente.avatar_file or "").strip()
        av_url = (url_for("uploads_avatars_public", fname=av, _external=False) if av else None)
        return jsonify({"username":utente.username,"email":utente.email,"nome_cognome":utente.nome_cognome,
                        "scuola":utente.scuola,"role":utente.role,
                        "telefono":utente.telefono,"data_nascita":utente.data_nascita,"indirizzo":utente.indirizzo,
                        "citta":utente.citta,"cap":utente.cap,"avatar_preset":utente.avatar_preset or 0,
                        "avatar_url":av_url,"oauth_provider":utente.oauth_provider,
                        "created_at":utente.created_at.strftime("%Y-%m-%d %H:%M:%S") if utente.created_at else "N/A",
                        "richiesta_cancellazione_pending":pend})
    elif request.method == "PUT":
        data = request.get_json(force=True, silent=True) or {}
        if "email" in data and data["email"]:
            if "@" not in str(data["email"]): return jsonify({"error":"Email non valida"}), 400
            if User.query.filter(User.email==data["email"], User.username!=utente.username).first():
                return jsonify({"error":"Email già usata"}), 400
            utente.email = data["email"].strip()
        if "nome_cognome" in data: utente.nome_cognome = (data.get("nome_cognome") or "").strip() or utente.nome_cognome
        if "scuola" in data: utente.scuola = (data.get("scuola") or "").strip() or utente.scuola
        for fld in ("telefono","data_nascita","indirizzo","citta","cap"):
            if fld in data: setattr(utente, fld, (data.get(fld) or "").strip() or None)
        if "avatar_preset" in data:
            try: utente.avatar_preset = max(0, min(24, int(data.get("avatar_preset") or 0)))
            except (TypeError, ValueError): pass
        if "password" in data and data["password"]:
            if len(str(data["password"])) < 4: return jsonify({"error":"Password minima 4 caratteri"}), 400
            utente.password = password_hash(data["password"])
        if data.get("rimuovi_oauth"):
            utente.oauth_provider, utente.oauth_sub = None, None
        db.session.commit(); log_audit("profilo_aggiornato", target_username=session["username"])
        return jsonify({"success":True})

@app.route("/api/profilo/avatar", methods=["POST"])
def api_profilo_avatar_upload():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if not spam_allow(session.get("username"), "avatar_up", 8, 3600):
        return jsonify({"error":"Troppi caricamenti, riprova più tardi."}), 429
    utente = User.query.filter_by(username=session["username"]).first()
    if not utente: return jsonify({"error":"Non trovato"}), 404
    f = request.files.get("file")
    if not f or not f.filename: return jsonify({"error":"File mancante"}), 400
    ext = f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
    if ext not in ("png","jpg","jpeg","gif","webp"):
        return jsonify({"error":"Formato non consentito"}), 400
    fn = f"{utente.username}_{uuid.uuid4().hex}.{ext}"; safe = secure_filename(fn)
    path = os.path.join(AVATAR_FOLDER, safe)
    f.save(path)
    if os.path.getsize(path) > 2*1024*1024:
        os.remove(path); return jsonify({"error":"File troppo grande (max 2MB)"}), 400
    old = (utente.avatar_file or "").strip()
    if old:
        op = os.path.join(AVATAR_FOLDER, secure_filename(old))
        try:
            if os.path.isfile(op): os.remove(op)
        except OSError:
            pass
    utente.avatar_file = safe; db.session.commit()
    return jsonify({"success":True,"avatar_url":url_for("uploads_avatars_public", fname=safe)})

@app.route("/api/me/access-log", methods=["GET"])
def api_me_access_log():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    lim = min(int(request.args.get("limit", "40")), 200)
    rows = (LoginHistory.query.filter_by(username=session["username"]).order_by(LoginHistory.quando.desc())
            .limit(lim).all())
    return jsonify([r.to_dict() for r in rows])

# =====================================================
# ============= API - VOTI ============================
# =====================================================

@app.route("/api/storico-voti", methods=["GET"])
def api_storico_voti():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    voti = Vote.query.filter_by(username=session["username"]).all()
    return jsonify([v.to_dict() for v in voti])

@app.route("/api/voti", methods=["GET", "POST"])
def api_voti():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if request.method == "POST":
        nuovo = request.get_json(force=True, silent=True) or {}
        target_user = session["username"]
        if admin_required() and (nuovo.get("username") or "").strip():
            target_user = nuovo["username"].strip()
        if not User.query.filter_by(username=target_user).first():
            return jsonify({"error":"Utente non trovato"}), 404
        voto = Vote(username=target_user, voto=str(nuovo.get("voto")), nomeProf=(nuovo.get("nomeProf") or "").strip(),
                    materia=(nuovo.get("materia") or "").strip(), scuola=(nuovo.get("scuola") or "").strip() or None)
        db.session.add(voto); db.session.commit(); log_audit("voto_creato", target_username=target_user)
        return jsonify({"success":True})
    query = Vote.query
    materia, scuola = request.args.get("materia","").strip().lower(), request.args.get("scuola","").strip().lower()
    if materia: query = query.filter(Vote.materia.ilike(f"%{materia}%"))
    if scuola: query = query.filter(Vote.scuola.ilike(f"%{scuola}%"))
    return jsonify([v.to_dict() for v in query.all()])

@app.route("/api/voti/<int:voto_id>", methods=["PUT", "DELETE"])
def api_voti_mod(voto_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    voto = Vote.query.get(voto_id)
    if not voto: return jsonify({"error":"Non trovato"}), 404
    if session["role"] != "admin" and voto.username != session["username"]:
        return jsonify({"error":"Puoi modificare solo i tuoi voti"}), 403
    if request.method == "DELETE": db.session.delete(voto); db.session.commit(); return jsonify({"success":True})
    if request.method == "PUT":
        data = request.json
        for campo in ["voto","nomeProf","materia","scuola"]:
            if campo in data: setattr(voto, campo, data[campo])
        db.session.commit(); return jsonify({"success":True})

# =====================================================
# ============= API - RECENSIONI ======================
# =====================================================

@app.route("/api/recensioni", methods=["GET", "POST"])
@limiter.limit("180/day;40/hour", methods=["POST"])
def api_recensioni():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if request.method == "POST":
        if not spam_allow(session.get("username"), "review_post", 25, 86400):
            return jsonify({"error":"Limite giornaliero recensioni raggiunto. Riprova domani."}), 429
        nuovo = request.get_json(force=True, silent=True) or {}
        target_user = session["username"]
        if admin_required() and (nuovo.get("username") or "").strip():
            target_user = nuovo["username"].strip()
        if not User.query.filter_by(username=target_user).first():
            return jsonify({"error":"Utente non trovato"}), 404
        pid = nuovo.get("professor_id")
        try:
            pid_i = int(pid) if pid not in (None, "", []) else None
        except (TypeError, ValueError):
            pid_i = None
        rec = Review(username=target_user, nomeProfRec=(nuovo.get("nomeProfRec") or "").strip(),
                     scuola=(nuovo.get("scuola") or "").strip() or None,
                     recensione=(nuovo.get("recensione") or "").strip(), likes=0, dislikes=0, user_likes=[], user_dislikes=[], commenti=[],
                     is_anonymous=bool(nuovo.get("is_anonymous")), professor_id=pid_i or None)
        db.session.add(rec); db.session.commit()
        notify_favorites_new_review(rec); log_audit("recensione_creata", target_username=target_user)
        return jsonify({"success":True,"id":rec.id})
    query = Review.query
    scuola, prof = request.args.get("scuola","").strip().lower(), request.args.get("prof","").strip().lower()
    if scuola: query = query.filter(Review.scuola.ilike(f"%{scuola}%"))
    if prof: query = query.filter(Review.nomeProfRec.ilike(f"%{prof}%"))
    righe = query.all(); users = {u.username: u.role for u in User.query.all()}
    return jsonify([{**recensioni_mask_row(r), "user_role": users.get(r.username, "user")} for r in righe])

@app.route("/api/recensioni/<int:rec_id>", methods=["PUT", "DELETE"])
def api_recensioni_mod(rec_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    rec = Review.query.get(rec_id)
    if not rec: return jsonify({"error":"Non trovata"}), 404
    if session["role"] != "admin" and rec.username != session["username"]:
        return jsonify({"error":"Puoi modificare solo le tue recensioni"}), 403
    if request.method == "DELETE": db.session.delete(rec); db.session.commit(); return jsonify({"success":True})
    if request.method == "PUT":
        data = request.json
        for campo in ["recensione","nomeProfRec","scuola"]:
            if campo in data: setattr(rec, campo, data[campo])
        if "is_anonymous" in data:
            rec.is_anonymous = bool(data["is_anonymous"])
        if data.get("professor_id") is not None:
            try: rec.professor_id = int(data["professor_id"]) if data["professor_id"] not in ("", None) else None
            except (TypeError, ValueError): pass
        db.session.commit(); return jsonify({"success":True})

@app.route("/api/recensioni/<int:rec_id>/like", methods=["POST"])
@limiter.limit("800/day;120/hour", methods=["POST"])
def api_recensioni_like(rec_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    rec = Review.query.get(rec_id)
    if not rec: return jsonify({"error":"Non trovata"}), 404
    if not spam_allow(session.get("username"), "rec_like", 600, 86400):
        return jsonify({"error":"Troppe azioni ravvicinate sulle recensioni."}), 429
    data = request.get_json() or {}; username = session["username"]; azione = data.get("azione")
    likes_l = list(rec.user_likes or []); dis_l = list(rec.user_dislikes or [])
    if username in likes_l: likes_l.remove(username); rec.likes = max(0, (rec.likes or 0) - 1)
    if username in dis_l: dis_l.remove(username); rec.dislikes = max(0, (rec.dislikes or 0) - 1)
    if azione == "like" and username not in likes_l:
        likes_l.append(username); rec.likes = (rec.likes or 0) + 1
    elif azione == "dislike" and username not in dis_l:
        dis_l.append(username); rec.dislikes = (rec.dislikes or 0) + 1
    rec.user_likes, rec.user_dislikes = likes_l, dis_l
    db.session.commit()
    return jsonify({"success":True,"likes":rec.likes,"dislikes":rec.dislikes})

@app.route("/api/recensioni/<int:rec_id>/commenti", methods=["GET", "POST"])
@limiter.limit("500/day;120/hour", methods=["POST"])
def api_recensioni_commenti(rec_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    rec = Review.query.get(rec_id)
    if not rec: return jsonify({"error":"Non trovata"}), 404
    if request.method == "GET": return jsonify(rec.commenti if rec.commenti is not None else [])
    elif request.method == "POST":
        if not spam_allow(session.get("username"), "review_comment_post", 80, 86400):
            return jsonify({"error":"Limite giornaliero commenti."}), 429
        data = request.get_json() or {}; testo = data.get("testo","").strip()
        parent_raw = data.get("parent_id")
        parent_id = None
        try:
            parent_id = int(parent_raw) if parent_raw not in (None, "") else None
        except (TypeError, ValueError):
            parent_id = None
        if not testo: return jsonify({"error":"Commento vuoto"}), 400
        comms = list(rec.commenti or [])
        next_id = max_review_comment_id(comms) + 1
        nuovo_commento = {"id":next_id,"utente":session["username"],"ruolo":session.get("role","user"),
                          "testo":testo,"parent_id":parent_id,
                          "data":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"likes":0,"dislikes":0,"user_likes":[],"user_dislikes":[],
                          "replies":[]}
        inserted = False
        if parent_id is not None:
            parent = find_flat_comment(comms, parent_id)
            if parent:
                subs = list(parent.get("replies") or [])
                subs.append(nuovo_commento)
                parent["replies"] = subs
                inserted = True
        if not inserted:
            comms.append(nuovo_commento)
        rec.commenti = comms
        db.session.commit()
        if parent_id is not None:
            flat = flatten_comments_for_notify(rec.commenti or [])
            parent_obj = next((x for x in flat if int(x.get("id") or 0) == int(parent_id)), None)
            if parent_obj:
                autor = parent_obj.get("utente")
                if autor and autor != session["username"]:
                    crea_notifica(autor, "comment_reply", "Risposta al tuo commento",
                                  f"Hanno risposto a un tuo commento sulla recensione #{rec_id}.",
                                  "/user#recensioni")
        else:
            if rec.username and rec.username != session["username"] and not rec.is_anonymous:
                crea_notifica(rec.username, "comment_reply", "Nuovo commento alla tua recensione",
                              f"{session['username']} ha commentato una tua recensione.", "/user#recensioni")
        return jsonify({"success":True,"comment":nuovo_commento})

def _comment_vote_mutate(branch, cid, voter, azione):
    cid = int(cid)
    for j, raw in enumerate(branch):
        c = dict(raw)
        subs = list(c.get("replies") or [])
        if int(c.get("id") or -1) == cid:
            ul, udl = list(c.get("user_likes", [])), list(c.get("user_dislikes", []))
            if voter in ul: ul.remove(voter); c["likes"] = max(0, c.get("likes", 0) - 1)
            if voter in udl: udl.remove(voter); c["dislikes"] = max(0, c.get("dislikes", 0) - 1)
            if azione == "like" and voter not in ul:
                ul.append(voter); c["likes"] = c.get("likes", 0) + 1
            elif azione == "dislike" and voter not in udl:
                udl.append(voter); c["dislikes"] = c.get("dislikes", 0) + 1
            c["user_likes"], c["user_dislikes"] = ul, udl
            branch[j] = c
            return True, c.get("likes", 0), c.get("dislikes", 0)
        ok, lk, dk = _comment_vote_mutate(subs, cid, voter, azione)
        if ok:
            c["replies"] = subs; branch[j] = c
            return True, lk, dk
    return False, 0, 0

@app.route("/api/recensioni/<int:rec_id>/commenti/<int:comm_id>/like", methods=["POST"])
@limiter.limit("1200/day;240/hour", methods=["POST"])
def api_recensioni_commenti_like(rec_id, comm_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    rec = Review.query.get(rec_id)
    if not rec: return jsonify({"error":"Non trovata"}), 404
    if not spam_allow(session.get("username"), "cmt_vote", 400, 86400):
        return jsonify({"error":"Limite giornaliero voti su commenti."}), 429
    comms = list(rec.commenti or [])
    data = request.get_json() or {}; azione = data.get("azione")
    voter = session["username"]
    ok, lk, dk = _comment_vote_mutate(comms, comm_id, voter, azione)
    if not ok:
        return jsonify({"error":"Commento non trovato"}), 404
    rec.commenti = comms
    db.session.commit()
    return jsonify({"success":True,"likes":lk,"dislikes":dk})

# =====================================================
# ============= API - PROFESSORI ======================
# =====================================================

@app.route("/api/professori", methods=["GET", "POST"])
def api_professori():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if request.method == "POST":
        raw = request.get_json(force=True, silent=True) or {}
        nome = (raw.get("nome") or "").strip()
        if not nome: return jsonify({"error":"Nome obbligatorio"}), 400
        nuovo = Professor(nome=nome, materia=(raw.get("materia") or "").strip() or None,
                           scuola=(raw.get("scuola") or "").strip() or None,
                           descrizione=(raw.get("descrizione") or "").strip() or None)
        tax_upsert_subject(nuovo.materia, session.get("username"))
        tax_upsert_school(nuovo.scuola, session.get("username"))
        db.session.add(nuovo); db.session.commit()
        log_audit("professore_creato")
        return jsonify({"success":True})
    return jsonify([p.to_dict() for p in Professor.query.all()])

@app.route("/api/professori/<int:prof_id>", methods=["PUT", "DELETE"])
def api_professori_mod(prof_id):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    prof = Professor.query.get(prof_id)
    if not prof: return jsonify({"error":"Non trovato"}), 404
    if request.method == "DELETE":
        db.session.delete(prof); db.session.commit()
        log_audit("professore_eliminato")
        return jsonify({"success":True})
    if request.method == "PUT":
        raw = request.get_json(force=True, silent=True) or {}
        if "nome" in raw and (raw.get("nome") or "").strip():
            prof.nome = raw["nome"].strip()
        if "materia" in raw:
            prof.materia = (raw.get("materia") or "").strip() or None
        if "scuola" in raw:
            prof.scuola = (raw.get("scuola") or "").strip() or None
        if "descrizione" in raw:
            prof.descrizione = (raw.get("descrizione") or "").strip() or None
        if not prof.nome: return jsonify({"error":"Nome obbligatorio"}), 400
        tax_upsert_subject(prof.materia, session.get("username"))
        tax_upsert_school(prof.scuola, session.get("username"))
        db.session.commit()
        log_audit("professore_aggiornato")
        return jsonify({"success":True})

# =====================================================
# ============= API - SEGNALAZIONI ====================
# =====================================================

@app.route("/api/segnalazioni", methods=["GET", "POST"])
def api_segnalazioni():
    if request.method == "GET":
        if not admin_required(): return jsonify({"error":"Solo admin"}), 403
        return jsonify([r.to_dict() for r in Report.query.order_by(Report.data.desc()).all()])
    elif request.method == "POST":
        if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
        data = request.get_json()
        segnalatore = session["username"]
        if admin_required() and (data.get("segnalatore") or "").strip():
            segnalatore = (data.get("segnalatore") or "").strip()
        nuova = Report(tipo=data.get("tipo","recensione"), indice=data.get("indice"), motivo=data.get("motivo",""),
                       segnalatore=segnalatore, stato="pending")
        db.session.add(nuova); db.session.commit()
        for admin in User.query.filter_by(role="admin").all():
            crea_notifica(admin.username, "segnalazione", "🚩 Nuova Segnalazione", f"{segnalatore} ha segnalato un contenuto", "/admin#segnalazioni")
        return jsonify({"success":True})

@app.route("/api/segnalazioni/<int:id>", methods=["PUT", "DELETE"])
def api_segnalazioni_gestisci(id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    rep = Report.query.get(id)
    if not rep: return jsonify({"error":"Non trovata"}), 404
    if request.method == "DELETE": db.session.delete(rep); db.session.commit(); return jsonify({"success":True})
    if request.method == "PUT":
        data = request.get_json()
        if data.get("stato") in ["pending","resolved","dismissed"]:
            rep.stato, rep.admin_note = data["stato"], data.get("admin_note","")
            if rep.stato != "pending": rep.data_chiusura = datetime.now()
            db.session.commit(); return jsonify({"success":True})
        return jsonify({"error":"Stato non valido"}), 400

# =====================================================
# ============= API - SESSIONI ========================
# =====================================================

@app.route("/api/sessioni", methods=["GET"])
def api_sessioni_lista():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    return jsonify([s.to_dict() for s in SessionLog.query.all()])

@app.route("/api/sessioni/<session_id>", methods=["DELETE"])
def api_sessioni_termina(session_id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    s = SessionLog.query.filter_by(session_id=session_id).first()
    if not s: return jsonify({"error":"Non trovata"}), 404
    if s.username == session["username"]: return jsonify({"error":"Non puoi disconnettere te stesso"}), 403
    db.session.delete(s); db.session.commit()
    return jsonify({"success":True,"message":f"Sessione di {s.username} terminata"})

# =====================================================
# ============= API - ANALYTICS & PRIVACY =============
# =====================================================

@app.route("/api/analytics/overview", methods=["GET"])
def api_analytics_overview():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    return jsonify({
        "utenti_totali":User.query.count(),"utenti_admin":User.query.filter_by(role="admin").count(),
        "utenti_sospesi":User.query.filter_by(account_status="sospeso").count(),
        "voti_totali":Vote.query.count(),"recensioni_totali":Review.query.count(),
        "ticket_totali":Ticket.query.count(),
        "ticket_aperti":Ticket.query.filter(Ticket.stato.in_(["aperto","in_lavorazione"])).count(),
        "segnalazioni_pending":Report.query.filter_by(stato="pending").count(),
        "sessioni_attive":SessionLog.query.count(),
        "registrazioni_in_attesa":RegistrationRequest.query.filter_by(stato="in_attesa").count(),
        "timestamp":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/api/analytics/charts", methods=["GET"])
def api_analytics_charts():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    voti = Vote.query.order_by(Vote.timestamp.asc()).all()
    per_mese = Counter()
    for v in voti:
        ts = v.timestamp.strftime("%Y-%m") if v.timestamp else ""
        per_mesi_key = ts or "sconosciuto"
        per_mese[per_mesi_key] += 1
    mesi_recenti = sorted(per_mese.keys())[-8:]
    dati_linea = {"labels": mesi_recenti, "values": [per_mese[m] for m in mesi_recenti]}

    per_scuola = Counter((v.scuola or "").strip() or "(non indicata)" for v in voti)
    per_scuola.pop("", None)
    top_scuole = sorted(per_scuola.items(), key=lambda x: -x[1])[:8]

    medi_materia = defaultdict(list)
    for v in voti:
        try:
            vv = float(str(v.voto).replace(",", "."))
            if v.materia: medi_materia[v.materia.strip()].append(vv)
        except (ValueError, TypeError):
            pass
    bar_materie = sorted(((m, round(sum(vals)/len(vals), 2)) for m, vals in medi_materia.items() if vals), key=lambda x: -x[1])[:8]

    studenti = User.query.filter_by(role="user").count()
    admin_num = User.query.filter_by(role="admin").count()
    donut_ruoli = {"labels":["Studenti","Amministratori"], "values":[studenti, admin_num]}

    medi_prof = defaultdict(list)
    for v in voti:
        try:
            vv = float(str(v.voto).replace(",", "."))
            if v.nomeProf: medi_prof[v.nomeProf.strip()].append(vv)
        except (ValueError, TypeError):
            pass
    top_prof_tuple = ("—", None, None, None)
    if medi_prof:
        best = max(medi_prof.items(), key=lambda it: (sum(it[1])/len(it[1]), len(it[1])))
        nome_tp = best[0]
        media_tp = round(sum(best[1]) / len(best[1]), 2)
        n_voti = len(best[1])
        n_rec = Review.query.filter(Review.nomeProfRec.ilike(f"%{nome_tp}%")).count()
        top_prof_tuple = (nome_tp, media_tp, n_voti, n_rec)

    return jsonify({
        "linea_voti": dati_linea,
        "doughnut_scuole": {"labels":[x[0] for x in top_scuole], "values":[x[1] for x in top_scuole]},
        "bar_materie": {"labels":[x[0] for x in bar_materie], "values":[x[1] for x in bar_materie]},
        "pie_ruoli": donut_ruoli,
        "top_prof": {"nome": top_prof_tuple[0], "media": top_prof_tuple[1], "n_voti": top_prof_tuple[2], "n_recensioni": top_prof_tuple[3]},
    })

@app.route("/api/admin/avvisi", methods=["GET", "POST"])
def api_admin_avvisi():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    if request.method == "GET":
        return jsonify([n.to_dict() for n in Notice.query.order_by(Notice.created_at.desc()).all()])
    data = request.get_json(force=True, silent=True) or {}
    nuovo = Notice(titolo=(data.get("titolo") or "").strip()[:200], contenuto=(data.get("contenuto") or "").strip(),
                   attivo=bool(data.get("attivo", True)), priority=(data.get("priority") or "normal")[:20],
                   expires_at=None)
    if data.get("expires_at"):
        try:
            nuovo.expires_at = datetime.fromisoformat(str(data["expires_at"]).replace("Z",""))
        except (ValueError, TypeError):
            pass
    if not nuovo.titolo or not nuovo.contenuto: return jsonify({"error":"Titolo e contenuto obbligatori"}), 400
    db.session.add(nuovo); db.session.commit(); log_audit("avviso_creato")
    return jsonify({"success":True,"avviso":nuovo.to_dict()})

@app.route("/api/admin/avvisi/<int:nid>", methods=["PUT", "DELETE"])
def api_admin_avvisi_gestione(nid):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    n = Notice.query.get(nid)
    if not n: return jsonify({"error":"Non trovato"}), 404
    if request.method == "DELETE":
        db.session.delete(n); db.session.commit(); log_audit("avviso_eliminato"); return jsonify({"success":True})
    data = request.get_json(force=True, silent=True) or {}
    if "titolo" in data: n.titolo = (data["titolo"] or "").strip()[:200]
    if "contenuto" in data: n.contenuto = (data["contenuto"] or "").strip()
    if "attivo" in data: n.attivo = bool(data["attivo"])
    if "priority" in data: n.priority = (data["priority"] or "normal")[:20]
    if "expires_at" in data:
        raw = data["expires_at"]
        if not raw:
            n.expires_at = None
        else:
            try:
                n.expires_at = datetime.fromisoformat(str(raw).replace("Z",""))
            except (ValueError, TypeError):
                pass
    db.session.commit(); log_audit("avviso_aggiornato")
    return jsonify({"success":True,"avviso":n.to_dict()})

@app.route("/api/admin/notifiche-broadcast", methods=["POST"])
def api_admin_broadcast():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    data = request.get_json(force=True, silent=True) or {}
    titolo = (data.get("titolo") or "").strip()
    messaggio = (data.get("messaggio") or "").strip()
    filtro = (data.get("filtro") or "users").strip()
    if filtro not in ("all", "users", "admins"):
        filtro = "users"
    tipo_notif = (data.get("tipo") or "sistema").strip()
    link = data.get("link") or ""
    if not titolo or not messaggio: return jsonify({"error":"Titolo e messaggio richiesti"}), 400
    q = User.query
    if filtro == "users":
        q = q.filter_by(role="user")
    elif filtro == "admins":
        q = q.filter_by(role="admin")
    dest = q.all()
    mandati = 0
    for u in dest:
        if crea_notifica(u.username, tipo_notif, titolo, messaggio, link or None): mandati += 1
    log_audit("broadcast_notifica", dettagli={"destinatari": len(dest)})
    return jsonify({"success":True,"inviate":mandati})

@app.route("/api/admin/audit-log", methods=["GET"])
def api_audit_log():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    limite = min(int(request.args.get("limit", "200")), 500)
    rows = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(limite).all()
    return jsonify([r.to_dict() for r in rows])

def costruisci_pacchetto_privacy_utente(username):
    u_obj = User.query.filter_by(username=username).first()
    keys_pref = ProfessorFavorite.query.filter_by(username=username).all()
    follows_out = [{"followed": uf.followed} for uf in UserFollow.query.filter_by(follower=username)]
    gm = [{"group_id": m.group_id, "ruolo": m.ruolo} for m in GroupMember.query.filter_by(username=username)]
    mater = [{"id": m.id, "titolo": m.titolo, "professore_nome": m.professore_nome, "quando": m.quando.strftime("%Y-%m-%d %H:%M:%S") if m.quando else None}
             for m in StudyMaterial.query.filter_by(caricato_da=username)]
    evt = [{"id": e.id, "titolo": e.titolo, "scuola": e.scuola, "group_id": e.group_id,
            "quando": e.quando.strftime("%Y-%m-%d %H:%M:%S") if e.quando else None}
           for e in ExamEvent.query.filter_by(creato_da=username)]
    lh = LoginHistory.query.filter_by(username=username).order_by(LoginHistory.quando.desc()).limit(400).all()
    return {
        "utente": u_obj.to_dict(include_sensitive=True) if u_obj else {},
        "voti": [v.to_dict() for v in Vote.query.filter_by(username=username).all()],
        "recensioni": [r.to_dict() for r in Review.query.filter_by(username=username).all()],
        "ticket": [t.to_dict() for t in Ticket.query.filter_by(utente=username).all()],
        "notifiche": [n.to_dict() for n in Notification.query.filter_by(utente=username).all()],
        "privacy_requests": [p.to_dict() for p in PrivacyRequest.query.filter_by(username=username).all()],
        "preferiti_prof": [{"chiave_prof": k.chiave_prof, "etichetta": k.professore_etichetta, "professor_id": k.professor_id} for k in keys_pref],
        "follow": follows_out,
        "gruppi": gm,
        "materiale_caricati": mater,
        "eventi": evt,
        "access_history": [x.to_dict() for x in lh],
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

@app.route("/api/privacy/export", methods=["GET"])
def api_privacy_export():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    return jsonify(costruisci_pacchetto_privacy_utente(session["username"]))

@app.route("/api/privacy/export.csv", methods=["GET"])
def api_privacy_export_csv():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    pac = costruisci_pacchetto_privacy_utente(session["username"])
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["section", "chiave", "valore"])
    for k, v in (pac.get("utente") or {}).items():
        w.writerow(["utente", str(k), json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v])
    for sec, lst in ("voti", pac.get("voti")), ("recensioni", pac.get("recensioni")), ("ticket", pac.get("ticket")), ("notifiche", pac.get("notifiche")), ("access_history", pac.get("access_history")):
        for i, vo in enumerate(lst or []):
            w.writerow([sec, str(i), json.dumps(vo, ensure_ascii=False)])
    fn = f"privacy_export_{session['username']}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                      headers={"Content-Disposition": f'attachment; filename="{fn}"'})

@app.route("/api/privacy/delete-request", methods=["POST"])
def api_privacy_delete_request():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    username = session["username"]
    if PrivacyRequest.query.filter_by(username=username, stato="pending").first():
        return jsonify({"error":"Hai già una richiesta pending"}), 400
    data = request.get_json() or {}
    nuova = PrivacyRequest(username=username, motivo=data.get("motivo",""))
    db.session.add(nuova); db.session.commit(); log_audit("privacy_delete_requested", target_username=username)
    return jsonify({"success":True,"richiesta":nuova.to_dict()})

@app.route("/api/privacy/delete-requests", methods=["GET", "POST"])
def api_privacy_delete_requests_list():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    if request.method == "GET":
        return jsonify([r.to_dict() for r in PrivacyRequest.query.all()])
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username or not User.query.filter_by(username=username).first():
        return jsonify({"error":"Utente non trovato"}), 404
    if PrivacyRequest.query.filter_by(username=username, stato="pending").first():
        return jsonify({"error":"Richiesta già pending"}), 400
    nuova = PrivacyRequest(username=username, motivo=(data.get("motivo") or "").strip(), stato="pending")
    db.session.add(nuova); db.session.commit()
    return jsonify({"success":True,"richiesta":nuova.to_dict()})

@app.route("/api/privacy/delete-requests/<int:req_id>", methods=["PUT"])
def api_privacy_delete_requests_manage(req_id):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    req = PrivacyRequest.query.get(req_id)
    if not req: return jsonify({"error":"Non trovata"}), 404
    data = request.get_json() or {}
    if data.get("stato") not in ["approved","rejected"]: return jsonify({"error":"Stato non valido"}), 400
    utente_nom = req.username
    req.stato, req.admin_note = data["stato"], data.get("admin_note","")
    if req.stato != "pending": req.data_chiusura = datetime.now()
    db.session.commit()
    log_audit("privacy_delete_request_reviewed", target_username=utente_nom)
    if data["stato"] == "approved":
        candidato = User.query.filter_by(username=utente_nom).first()
        if candidato and is_founder_user(candidato.username):
            return jsonify({"error":"Impossibile eliminare account founder"}), 400
        elimina_utente_totale(utente_nom)
        return jsonify({"success":True,"message":"Utente e dati collegati eliminati."})
    return jsonify({"success":True,"richiesta":req.to_dict()})

def _exam_dict(ev):
    return {"id":ev.id,"scuola":ev.scuola,"group_id":ev.group_id,"materia":ev.materia,"titolo":ev.titolo,"note":ev.note,
            "creato_da":ev.creato_da,"quando":ev.quando.strftime("%Y-%m-%d %H:%M:%S") if ev.quando else None}

def _sam_sc(urow, schools):
    a = ((urow.scuola or "").strip().lower()); b = ((schools or "").strip().lower())
    return bool(a and b and a == b)

def _gruppo_mem(uid, gid):
    return GroupMember.query.filter_by(group_id=gid, username=uid).first() is not None

@app.route("/api/materiali", methods=["GET"])
def api_materiali_lista():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    q = StudyMaterial.query
    sc = request.args.get("scuola", "").strip()
    pn = request.args.get("prof", "").strip()
    pid = request.args.get("professor_id", "").strip()
    if sc:
        q = q.filter(StudyMaterial.scuola.ilike(f"%{sc}%"))
    if pn:
        q = q.filter(StudyMaterial.professore_nome.ilike(f"%{pn}%"))
    if pid.isdigit():
        q = q.filter_by(professor_id=int(pid))
    return jsonify([{**m.to_dict(), "file": True} for m in q.order_by(StudyMaterial.quando.desc()).limit(500).all()])

@app.route("/api/materiali", methods=["POST"])
@limiter.limit("80/day;20/hour", methods=["POST"])
def api_materiali_upload():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if not spam_allow(session.get("username"), "material_up", 30, 86400):
        return jsonify({"error":"Limite caricamenti giornaliero."}), 429
    ume = User.query.filter_by(username=session["username"]).first()
    f = request.files.get("file")
    if not f or not f.filename: return jsonify({"error":"File mancante"}), 400
    fname = secure_filename(f.filename)
    if not materiale_consentiti(fname): return jsonify({"error":"Estensione non consentita"}), 400
    titolo = (request.form.get("titolo") or fname).strip()[:200]
    pn = request.form.get("professore_nome", "").strip()[:120]
    mat = request.form.get("materia", "").strip()[:100]
    sc = (request.form.get("scuola") or "").strip()[:150] or (ume.scuola or "").strip()
    if not sc: return jsonify({"error":"Scuola obbligatoria"}), 400
    try:
        pid = int(request.form.get("professor_id")) if request.form.get("professor_id") else None
    except (TypeError, ValueError):
        pid = None
    safe_fn = uuid.uuid4().hex + "_" + fname
    disk = os.path.join(UPLOAD_FOLDER, safe_fn)
    f.save(disk)
    sz = os.path.getsize(disk)
    if sz > MAX_UPLOAD_BYTES:
        try: os.remove(disk)
        except OSError:
            pass
        return jsonify({"error":"File troppo grande"}), 400
    row = StudyMaterial(professor_id=pid, professore_nome=pn or None, materia=mat or None, scuola=sc,
                        titolo=titolo or fname, nome_file_sicuro=safe_fn, caricato_da=session["username"],
                        mime=f.mimetype or "", dimensione=int(sz))
    db.session.add(row); db.session.commit(); notify_favorites_new_material(row); log_audit("materiale_upload")
    return jsonify({"success":True,"id":row.id})

@app.route("/api/materiali/<int:mid>", methods=["DELETE"])
def api_materiali_delete(mid):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    m = StudyMaterial.query.get(mid)
    if not m: return jsonify({"error":"Non trovato"}), 404
    if m.caricato_da != session["username"] and not admin_required(): return jsonify({"error":"Non autorizzato"}), 403
    path = os.path.join(UPLOAD_FOLDER, secure_filename(m.nome_file_sicuro))
    db.session.delete(m); db.session.commit()
    try:
        if os.path.isfile(path): os.remove(path)
    except OSError:
        pass
    return jsonify({"success":True})

@app.route("/api/materiali/<int:mid>/file", methods=["GET"])
def api_materiali_download(mid):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    m = StudyMaterial.query.get(mid)
    if not m: return jsonify({"error":"Non trovato"}), 404
    uobj = User.query.filter_by(username=session["username"]).first()
    if not admin_required() and not _sam_sc(uobj, m.scuola):
        return jsonify({"error":"Solo membri della stessa scuola possono scaricare"}), 403
    fn = secure_filename(m.nome_file_sicuro)
    disk = os.path.join(UPLOAD_FOLDER, fn)
    if not os.path.isfile(disk): return jsonify({"error":"File archiviato non trovato"}), 404
    dl = secure_filename((m.titolo or "materiale").replace(" ", "_")[:80]) or "download"
    ext = os.path.splitext(fn)[1]
    if ext and not dl.endswith(ext):
        dl = dl + ext
    return send_file(disk, as_attachment=True, download_name=dl)

@app.route("/api/eventi-esame", methods=["GET"])
def api_eventi_lista():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    uid = User.query.filter_by(username=session["username"]).first()
    scfilt = request.args.get("scuola", "").strip() or ((uid.scuola or "").strip())
    gid = request.args.get("group_id")
    gi = None
    try:
        gi = int(gid) if gid not in ("", None) else None
    except (ValueError, TypeError):
        gi = None
    q = ExamEvent.query
    if scfilt:
        q = q.filter(ExamEvent.scuola.ilike(scfilt.strip()))
    if gi is not None:
        q = q.filter_by(group_id=gi)
        if not _gruppo_mem(session["username"], gi):
            return jsonify({"error":"Non sei membro del gruppo selezionato"}), 403
    rows = q.order_by(ExamEvent.quando.asc()).limit(500).all()
    return jsonify([_exam_dict(ev) for ev in rows])

@app.route("/api/eventi-esame", methods=["POST"])
@limiter.limit("120/day;40/hour", methods=["POST"])
def api_eventi_crea():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if not spam_allow(session.get("username"), "exam_post", 40, 86400): return jsonify({"error":"Limite giornaliero eventi"}), 429
    uid = User.query.filter_by(username=session["username"]).first()
    data = request.get_json(force=True, silent=True) or {}
    sc = ((data.get("scuola") or uid.scuola or "").strip()[:150])
    if not sc: return jsonify({"error":"Scuola mancante"}), 400
    if str(data.get("scuola","")).strip() and not _sam_sc(uid, data.get("scuola","")):
        return jsonify({"error":"Puoi inserire eventi solo per la tua scuola registrata"}), 403
    tit = (data.get("titolo") or "").strip()[:200]
    if not tit: return jsonify({"error":"Titolo obbligatorio"}), 400
    try:
        dq = datetime.fromisoformat(str(data.get("quando")).replace("Z",""))
    except (TypeError, ValueError):
        return jsonify({"error":"Data/ora non valida (ISO8601)"}), 400
    gid = None
    if data.get("group_id"):
        gid = int(data["group_id"])
        if not _gruppo_mem(session["username"], gid): return jsonify({"error":"Gruppo non consentito"}), 403
    ev = ExamEvent(scuola=sc, group_id=gid, materia=(data.get("materia") or "").strip()[:100] or None,
                   titolo=tit, note=(data.get("note") or "").strip(), quando=dq, creato_da=session["username"])
    db.session.add(ev); db.session.commit(); return jsonify({"success":True,"evento":_exam_dict(ev)})

@app.route("/api/eventi-esame/<int:eid>", methods=["DELETE"])
def api_eventi_delete(eid):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    ev = ExamEvent.query.get(eid)
    if not ev: return jsonify({"error":"Non trovato"}), 404
    if ev.creato_da != session["username"] and not admin_required(): return jsonify({"error":"Non autorizzato"}), 403
    db.session.delete(ev); db.session.commit(); return jsonify({"success":True})

@app.route("/api/gruppi", methods=["GET", "POST"])
@limiter.limit("40/day;12/hour", methods=["POST"])
def api_gruppi():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    uid = User.query.filter_by(username=session["username"]).first()
    if request.method == "GET":
        sc = (request.args.get("scuola") or uid.scuola or "").strip()
        q = UserGroup.query
        if sc: q = q.filter(UserGroup.scuola.ilike(sc))
        return jsonify([{"id":g.id,"nome":g.nome,"slug":g.slug,"scuola":g.scuola,"creator":g.creator,"descrizione":g.descrizione,
                         "quando":g.quando.strftime("%Y-%m-%d %H:%M:%S") if g.quando else None} for g in q.order_by(UserGroup.quando.desc()).limit(200).all()])
    data = request.get_json(force=True, silent=True) or {}
    nome = (data.get("nome") or "").strip()[:120]
    if not nome: return jsonify({"error":"Nome gruppo obbligatorio"}), 400
    sc = ((data.get("scuola") or uid.scuola or "").strip()[:150])
    if not sc: return jsonify({"error":"Scuola richiesta sul profilo o nel body"}), 400
    if not _sam_sc(uid, sc):
        return jsonify({"error":"Il gruppo deve appartenere alla tua scuola registrata"}), 403
    slug = uuid.uuid4().hex[:14]
    while UserGroup.query.filter_by(slug=slug).first():
        slug = uuid.uuid4().hex[:14]
    g = UserGroup(nome=nome, slug=slug, scuola=sc, creator=session["username"], descrizione=(data.get("descrizione") or "").strip())
    db.session.add(g); db.session.commit()
    db.session.add(GroupMember(group_id=g.id, username=session["username"], ruolo="creator"))
    db.session.commit(); return jsonify({"success":True,"gruppo":{"id":g.id,"slug":g.slug}})

@app.route("/api/gruppi/<int:gid>/unisciti", methods=["POST"])
@limiter.limit("120/day;50/hour", methods=["POST"])
def api_gruppo_unisciti(gid):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    g = UserGroup.query.get(gid)
    if not g: return jsonify({"error":"Non trovato"}), 404
    uobj = User.query.filter_by(username=session["username"]).first()
    if not _sam_sc(uobj, g.scuola): return jsonify({"error":"Puoi unirti solo a gruppi della tua scuola"}), 403
    if GroupMember.query.filter_by(group_id=gid, username=session["username"]).first():
        return jsonify({"success":True,"message":"Già membro"})
    db.session.add(GroupMember(group_id=gid, username=session["username"], ruolo="membro"))
    db.session.commit(); return jsonify({"success":True})

@app.route("/api/gruppi/<int:gid>/membri", methods=["POST", "DELETE"])
@limiter.limit("300/day;120/hour", methods=["POST", "DELETE"])
def api_gruppo_membri(gid):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    g = UserGroup.query.get(gid)
    if not g: return jsonify({"error":"Non trovato"}), 404
    row = GroupMember.query.filter_by(group_id=gid, username=session["username"]).first()
    if request.method == "POST":
        if not row or row.ruolo not in ("creator", "admin"):
            return jsonify({"error":"Solo il creatore può aggiungere"}), 403
        data = request.get_json(force=True, silent=True) or {}
        nu = (data.get("username") or "").strip()
        if not User.query.filter_by(username=nu).first(): return jsonify({"error":"Utente sconosciuto"}), 404
        uadd = User.query.filter_by(username=nu).first()
        if not _sam_sc(uadd, g.scuola): return jsonify({"error":"L'utente non appartiene alla stessa scuola"}), 400
        if GroupMember.query.filter_by(group_id=gid, username=nu).first():
            return jsonify({"success":True})
        db.session.add(GroupMember(group_id=gid, username=nu, ruolo="membro")); db.session.commit()
        crea_notifica(nu, "sistema", "Aggiunto a un gruppo", f"{session['username']} ti ha aggiunto al gruppo «{g.nome}»", "/user#gruppi")
        return jsonify({"success":True})
    if not row:
        return jsonify({"error":"Non sei nel gruppo"}), 403
    db.session.delete(row); db.session.commit(); return jsonify({"success":True})

@app.route("/api/utenti/cerca", methods=["GET"])
def api_utenti_cerca():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    qtxt = request.args.get("q", "").strip().lower()
    if len(qtxt) < 2: return jsonify([])
    hits = []
    for u in User.query.filter(or_(User.username.ilike(f"%{qtxt}%"), User.nome_cognome.ilike(f"%{qtxt}%"))).limit(25):
        if u.username == session["username"]: continue
        hits.append({"username":u.username,"nome_cognome":u.nome_cognome,"scuola":u.scuola,"role":u.role})
    return jsonify(hits)

@app.route("/api/social/follow/<username>", methods=["POST", "DELETE"])
@limiter.limit("500/day;160/hour", methods=["POST", "DELETE"])
def api_social_follow(username):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    who = (username or "").strip()
    if not who or who == session["username"]: return jsonify({"error":"Non valido"}), 400
    if not User.query.filter_by(username=who).first(): return jsonify({"error":"Utente non trovato"}), 404
    if request.method == "POST":
        if UserFollow.query.filter_by(follower=session["username"], followed=who).first():
            return jsonify({"success":True})
        db.session.add(UserFollow(follower=session["username"], followed=who)); db.session.commit()
        return jsonify({"success":True})
    UserFollow.query.filter_by(follower=session["username"], followed=who).delete()
    db.session.commit(); return jsonify({"success":True})

@app.route("/api/social/follow", methods=["GET"])
def api_social_follow_list():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    fl = [r.followed for r in UserFollow.query.filter_by(follower=session["username"]).all()]
    return jsonify({"seguiti":fl})

@app.route("/api/preferiti", methods=["GET", "POST"])
@limiter.limit("300/day;120/hour", methods=["POST"])
def api_preferiti():
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    if request.method == "GET":
        rows = ProfessorFavorite.query.filter_by(username=session["username"]).all()
        return jsonify([{"id":r.id,"chiave_prof":r.chiave_prof,"etichetta":r.professore_etichetta,"professor_id":r.professor_id} for r in rows])
    data = request.get_json(force=True, silent=True) or {}
    pid = data.get("professor_id")
    try:
        pid_i = int(pid) if pid not in (None, "") else None
    except (TypeError, ValueError):
        pid_i = None
    nome = (data.get("professore_nome") or "").strip()
    mat = (data.get("materia") or "").strip()
    sc = (data.get("scuola") or "").strip() or (User.query.filter_by(username=session["username"]).first().scuola or "")
    ck = chiav_prof(pref_id=pid_i, nome=nome, mat=mat, scuola=sc)
    eti = (data.get("etichetta") or nome or f"Prof {pid_i or ''}").strip()[:220]
    if ProfessorFavorite.query.filter_by(username=session["username"], chiave_prof=ck).first():
        return jsonify({"success":True,"message":"Già presente"})
    db.session.add(ProfessorFavorite(username=session["username"], professor_id=pid_i, chiave_prof=ck, professore_etichetta=eti))
    db.session.commit(); return jsonify({"success":True})

@app.route("/api/preferiti/<int:pid>", methods=["DELETE"])
def api_preferiti_del(pid):
    if not login_required(): return jsonify({"error":"Non autorizzato"}), 403
    r = ProfessorFavorite.query.get(pid)
    if not r or r.username != session["username"]: return jsonify({"error":"Non trovato"}), 404
    db.session.delete(r); db.session.commit(); return jsonify({"success":True})

@app.route("/api/admin/banner", methods=["GET", "PUT"])
def api_admin_banner():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    ensure_banner_singleton()
    b = SiteBanner.query.get(1)
    if request.method == "GET":
        return jsonify({"attivo":b.attivo,"messaggio":b.messaggio,"aggiornato":b.aggiornato.strftime("%Y-%m-%d %H:%M:%S") if b.aggiornato else None})
    data = request.get_json(force=True, silent=True) or {}
    if "attivo" in data: b.attivo = bool(data["attivo"])
    if "messaggio" in data: b.messaggio = (data.get("messaggio") or "")[:600]
    db.session.commit(); log_audit("site_banner"); return jsonify({"success":True})

@app.route("/api/admin/banned-ip", methods=["GET", "POST"])
def api_admin_banned_ip():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    if request.method == "GET":
        return jsonify([{"id":r.id,"ip":r.ip,"motivo":r.motivo,"creato_il":r.creato_il.strftime("%Y-%m-%d %H:%M:%S") if r.creato_il else None,
                         "banned_by":r.banned_by} for r in BannedIP.query.order_by(BannedIP.creato_il.desc()).all()])
    data = request.get_json(force=True, silent=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip: return jsonify({"error":"IP richiesto"}), 400
    if BannedIP.query.filter_by(ip=ip).first(): return jsonify({"error":"Già bannato"}), 400
    row = BannedIP(ip=ip, motivo=(data.get("motivo") or "").strip(), banned_by=session["username"])
    db.session.add(row); db.session.commit(); log_audit("ip_ban", dettagli={"ip":ip}); return jsonify({"success":True,"id":row.id})

@app.route("/api/admin/banned-ip/<int:bid>", methods=["DELETE"])
def api_admin_banned_ip_del(bid):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    r = BannedIP.query.get(bid)
    if not r: return jsonify({"error":"Non trovato"}), 404
    db.session.delete(r); db.session.commit(); return jsonify({"success":True})

@app.route("/api/admin/newsletter", methods=["POST"])
def api_admin_newsletter():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    data = request.get_json(force=True, silent=True) or {}
    subj = (data.get("subject") or data.get("oggetto") or "").strip()
    body = (data.get("html") or data.get("corpo") or "").strip()
    anche_admin = bool(data.get("anche_admin"))
    if not subj or not body: return jsonify({"error":"subject e html richiesti"}), 400
    if not EMAIL_PASSWORD: return jsonify({"error":"SMTP non configurato (EMAIL_PASSWORD)"}), 503
    q = User.query
    if not anche_admin:
        q = q.filter_by(role="user")
    ok, ko = 0, 0
    for u in q.all():
        em = (u.email or "").strip()
        if not em or "@" not in em: ko += 1; continue
        if invia_email_newsletter(em, subj, body): ok += 1
        else: ko += 1
    log_audit("newsletter_bulk", dettagli={"ok":ok,"ko":ko})
    return jsonify({"success":True,"inviati":ok,"saltati":ko})

@app.route("/api/admin/ticket-templates", methods=["GET", "POST"])
def api_admin_ticket_templates():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    if request.method == "GET":
        return jsonify([{"id":t.id,"nome":t.nome,"oggetto":t.oggetto,"corpo":t.corpo,"tipo":t.tipo} for t in TicketTemplate.query.all()])
    data = request.get_json(force=True, silent=True) or {}
    if not (data.get("nome") and data.get("corpo")): return jsonify({"error":"nome e corpo obbligatori"}), 400
    t = TicketTemplate(nome=data["nome"].strip()[:120], oggetto=(data.get("oggetto") or "").strip()[:200],
                       corpo=data["corpo"].strip(), tipo=(data.get("tipo") or "generale")[:40])
    db.session.add(t); db.session.commit(); return jsonify({"success":True,"id":t.id})

@app.route("/api/admin/ticket-templates/<int:tid>", methods=["PUT", "DELETE"])
def api_admin_ticket_templates_mod(tid):
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    t = TicketTemplate.query.get(tid)
    if not t: return jsonify({"error":"Non trovato"}), 404
    if request.method == "DELETE":
        db.session.delete(t); db.session.commit(); return jsonify({"success":True})
    data = request.get_json(force=True, silent=True) or {}
    for fld in ("nome","oggetto","corpo","tipo"):
        if fld in data and data[fld] is not None: setattr(t, fld, str(data[fld])[:500 if fld=="corpo" else 200])
    db.session.commit(); return jsonify({"success":True})

@app.route("/api/admin/system-reset", methods=["POST"])
def api_admin_system_reset():
    if not admin_required(): return jsonify({"error":"Solo admin"}), 403
    if not is_founder_user(session.get("username")):
        return jsonify({"error":"Solo il founder può accedere a questa funzione"}), 403
    data = request.get_json(force=True, silent=True) or {}
    pwd = str(data.get("password") or "")
    confirm = str(data.get("confirm") or "")
    expected = os.getenv("RESET_PANEL_PASSWORD", "Francesco@1")
    if pwd != expected:
        log_audit("system_reset_denied", esito="ko")
        return jsonify({"error":"Password sicurezza errata"}), 403
    if confirm != "RESET-SYSTEM":
        return jsonify({"error":"Conferma non valida (usa RESET-SYSTEM)"}), 400
    # Reset controllato: preserva account founder e tabelle di base
    keep_user = session.get("username")
    Vote.query.delete(); Review.query.delete(); Professor.query.delete()
    Ticket.query.delete(); Report.query.delete(); Notice.query.delete()
    SessionLog.query.delete(); RegistrationRequest.query.delete()
    Notification.query.delete(); NotificationPreference.query.delete()
    PrivacyRequest.query.delete(); StudyMaterial.query.delete()
    ExamEvent.query.delete(); UserGroup.query.delete(); GroupMember.query.delete()
    UserFollow.query.delete(); ProfessorFavorite.query.delete()
    BannedIP.query.delete(); TicketTemplate.query.delete(); LoginHistory.query.delete()
    Subject.query.delete(); School.query.delete()
    for u in User.query.all():
        if u.username != keep_user:
            db.session.delete(u)
    ensure_banner_singleton()
    db.session.commit()
    log_audit("system_reset_done")
    return jsonify({"success":True,"message":"Reset completato"})

# =====================================================
# ============= AVVIO APPLICAZIONE ====================
# =====================================================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        migrate_sqlite()
        ensure_banner_singleton()
        if not RoleDef.query.filter_by(nome="admin").first():
            db.session.add(RoleDef(nome="admin", is_system=True))
        if not RoleDef.query.filter_by(nome="user").first():
            db.session.add(RoleDef(nome="user", is_system=True))
        db.session.commit()
        # Crea admin di default se non esiste
        if not User.query.filter_by(role="admin").first():
            admin = User(username="admin", password=password_hash("admin123"),
                         email="admin@registro.local", role="admin", account_status="attivo")
            db.session.add(admin); db.session.commit()
            print("[setup] Admin creato: username='admin', password='admin123' — CAMBIALA!")
    print("[RegistroProf] Server http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)