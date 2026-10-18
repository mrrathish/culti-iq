"""Prototype local accounts: SQLite-backed credentials and HttpOnly session cookies."""
import hashlib, hmac, os, re, secrets, sqlite3, time
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

router=APIRouter(prefix='/api/auth',tags=['Accounts'])
DB=os.environ.get('CULTIIQ_DB','/tmp/cultiiq.sqlite3')
COOKIE='cultiiq_session'
def conn():
    os.makedirs(os.path.dirname(DB) or '.',exist_ok=True)
    db=sqlite3.connect(DB,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS app_users (username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS app_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires INTEGER NOT NULL)')
    return db
class Credentials(BaseModel):
    username:str=Field(min_length=3,max_length=40)
    password:str=Field(min_length=8,max_length=128)
def validate(c):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',c.username):raise HTTPException(422,'Username must contain 3–40 letters, digits, dots, hyphens or underscores')
def hash_pw(password,salt):return hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)
def set_session(db,username,response):
    token=secrets.token_urlsafe(40)
    db.execute('INSERT INTO app_sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),username,int(time.time())+86400))
    db.commit()
    response.set_cookie(COOKIE,token,max_age=86400,httponly=True,samesite='lax',secure=os.getenv('CULTIIQ_HTTPS','false').lower()=='true',path='/')
    return {'username':username}
@router.post('/register')
def register(c:Credentials,response:Response):
    validate(c)
    salt=os.urandom(16)
    with conn() as db:
        if db.execute('SELECT 1 FROM app_users WHERE username=?',(c.username,)).fetchone():raise HTTPException(409,'Username already exists')
        db.execute('INSERT INTO app_users VALUES (?,?,?)',(c.username,salt,hash_pw(c.password,salt)))
        return set_session(db,c.username,response)
@router.post('/login')
def login(c:Credentials,response:Response):
    validate(c)
    with conn() as db:
        row=db.execute('SELECT salt,password_hash FROM app_users WHERE username=?',(c.username,)).fetchone()
        if not row or not hmac.compare_digest(hash_pw(c.password,row[0]),row[1]):raise HTTPException(401,'Incorrect username or password')
        return set_session(db,c.username,response)
@router.get('/me')
def me(request:Request):
    token=request.cookies.get(COOKIE,'')
    if not token:return {'username':None}
    with conn() as db:
        row=db.execute('SELECT username FROM app_sessions WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
    return {'username':row[0] if row else None}
@router.post('/logout')
def logout(request:Request,response:Response):
    token=request.cookies.get(COOKIE,'')
    if token:
        with conn() as db:
            db.execute('DELETE FROM app_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}
