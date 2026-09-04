"""FormaTesi: WSGI application. PostgreSQL in production; SQLite only for local tests."""
import base64, contextlib, datetime, hashlib, hmac, html, http.cookies, io, json, os, re, secrets, sqlite3, time, urllib.parse, urllib.request, uuid
from pathlib import Path

ROOT=Path(__file__).parent
FB='https://www.facebook.com/profile.php?id=61593221212687'
MESSENGER='https://m.me/61593221212687'
WHATSAPP='https://wa.me/393505815735?text='+urllib.parse.quote('Ciao FormaTesi, vorrei una consulenza gratuita per la mia tesi.')
ATENEI=['eCampus','Pegaso','Universitas Mercatorum','San Raffaele Roma','Altro ateneo']
STATUS={'waiting':'In attesa','delivered':'Consegnato','revision_requested':'Da revisionare','revised':'Revisionato'}
def esc(s): return html.escape(str(s or ''),quote=True)
def uid(): return uuid.uuid4().hex
def now(): return int(time.time())
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def normal(s): return ' '.join(re.sub(r'[^\w\s]',' ',s.lower()).split())
def password_hash(s):
 salt=secrets.token_hex(16); return salt+':'+hashlib.scrypt(s.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
def password_ok(s,stored):
 try:
  salt,val=stored.split(':'); return hmac.compare_digest(hashlib.scrypt(s.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex(),val)
 except Exception:return False
class Failure(Exception):
 def __init__(self,message,code=400):self.message=message;self.code=code
class Database:
 def __init__(self,url):self.url=url;self.pg=url.startswith(('postgres://','postgresql://'))
 @contextlib.contextmanager
 def connect(self):
  if self.pg:
   import psycopg
   from psycopg.rows import dict_row
   c=psycopg.connect(self.url,row_factory=dict_row)
  else:
   c=sqlite3.connect(self.url,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
  try:yield c;c.commit()
  except: c.rollback();raise
  finally:c.close()
 def sql(self,q):return q.replace('?','%s') if self.pg else q
 def run(self,c,q,args=()):return c.execute(self.sql(q),args)
 def init(self):
  schema='''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,surname TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,matricola TEXT NOT NULL,verified INTEGER NOT NULL DEFAULT 0,role TEXT NOT NULL DEFAULT 'student',created BIGINT NOT NULL,facebook_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,expires BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),ateneo TEXT NOT NULL,faculty TEXT NOT NULL,subject TEXT NOT NULL,title TEXT NOT NULL,outline TEXT NOT NULL,paragraph TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'waiting',revision INTEGER NOT NULL DEFAULT 0,free INTEGER NOT NULL DEFAULT 1,identity_key TEXT NOT NULL,created BIGINT NOT NULL,updated BIGINT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_free_per_student ON projects(user_id) WHERE free=1;
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,actor_id TEXT NOT NULL REFERENCES users(id),kind TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL,created BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,event_id TEXT REFERENCES events(id) ON DELETE CASCADE,name TEXT NOT NULL,mime TEXT NOT NULL,data TEXT NOT NULL,sha TEXT NOT NULL,created BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),description TEXT NOT NULL,cents INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'proposed',created BIGINT NOT NULL,accepted BIGINT);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,user_id TEXT,action TEXT NOT NULL,created BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,email TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0,created BIGINT NOT NULL);'''
  with self.connect() as c:
   for q in schema.split(';'):
    if q.strip():self.run(c,q)
   if self.pg:
    found=self.run(c,"SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='facebook_id'").fetchone()
   else:found=any(x['name']=='facebook_id' for x in self.run(c,'PRAGMA table_info(users)').fetchall())
   if not found:self.run(c,'ALTER TABLE users ADD COLUMN facebook_id TEXT')
   self.run(c,'CREATE UNIQUE INDEX IF NOT EXISTS users_facebook_id ON users(facebook_id) WHERE facebook_id IS NOT NULL')

class Site:
 def __init__(self,config=None):
  self.cfg=dict(os.environ);self.cfg.update(config or {});self.testing=self.cfg.get('TESTING')=='1'
  self.origin=self.cfg.get('PUBLIC_URL','http://localhost:8000').rstrip('/')
  url=self.cfg.get('DATABASE_URL','')
  if not url and self.testing:url=self.cfg['TEST_DB']
  self.db=Database(url) if url else None
  if self.db:
   if not self.testing and not self.db.pg:raise RuntimeError('Production requires durable PostgreSQL DATABASE_URL')
   self.db.init()
  self.ready=bool(self.db and (self.testing or all(self.cfg.get(k) for k in ['RESEND_API_KEY','MAIL_FROM','ADMIN_EMAIL','PUBLIC_URL','BUSINESS_NAME','PRIVACY_CONTACT','PRIVACY_PROVIDERS','RETENTION_POLICY']) and self.cfg.get('LEGAL_READY')=='yes'))
 def query(self,q,args=(),one=False):
  with self.db.connect() as c:
   cur=self.db.run(c,q,args);r=cur.fetchone() if one else cur.fetchall();return dict(r) if one and r else ([dict(x) for x in r] if not one else None)
 def mutate(self,q,args=()):
  with self.db.connect() as c:self.db.run(c,q,args)
 def audit(self,u,action):self.mutate('INSERT INTO audit VALUES(?,?,?,?)',(uid(),u,action,now()))
 def limit(self,key,maximum=10,seconds=900):
  key=digest(key)
  with self.db.connect() as c:
   self.db.run(c,'DELETE FROM rate_limits WHERE expires<?',(now(),))
   self.db.run(c,'INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=rate_limits.count+1',(key,now()+seconds))
   r=self.db.run(c,'SELECT count FROM rate_limits WHERE key=?',(key,)).fetchone()
   if r['count']>maximum:raise Failure('Troppi tentativi. Riprova tra qualche minuto.',429)
 def mail(self,email,subject,body):
  ident=uid();self.mutate('INSERT INTO outbox VALUES(?,?,?,?,0,?)',(ident,email,subject,body,now()))
  if self.testing:return
  self.send_mail(ident,email,subject,body)
 def send_mail(self,ident,email,subject,body):
  try:
   payload=json.dumps({'from':self.cfg['MAIL_FROM'],'to':[email],'subject':subject,'text':body}).encode()
   req=urllib.request.Request('https://api.resend.com/emails',data=payload,headers={'Authorization':'Bearer '+self.cfg['RESEND_API_KEY'],'Content-Type':'application/json','Idempotency-Key':ident})
   with urllib.request.urlopen(req,timeout=8) as response:
    if response.status not in (200,201):return
   self.mutate('UPDATE outbox SET sent=1,body=? WHERE id=?',('[Messaggio inviato]',ident))
  except Exception:pass # Retained outbox permits an explicit admin retry without losing deliveries.
 def token(self,user,kind):
  raw=secrets.token_urlsafe(32)
  with self.db.connect() as c:
   self.db.run(c,'DELETE FROM tokens WHERE user_id=? AND kind=?',(user['id'],kind))
   self.db.run(c,'INSERT INTO tokens VALUES(?,?,?,?)',(digest(raw),user['id'],kind,now()+3600))
  return raw
 def notify(self,project,subject):
  user=self.query('SELECT * FROM users WHERE id=?',(project['user_id'],),True)
  self.mail(user['email'],subject,'Ci sono aggiornamenti nel tuo spazio FormaTesi. Accedi qui: '+self.origin+'/lavori/'+project['id'])
 def __call__(self,environ,start_response):
  self_req=Request(self,environ)
  try:body,code,headers=self.route(self_req)
  except Failure as e:body,code,headers=self.page(self_req,'Attenzione',f'<section class="narrow panel"><span class="eyebrow">FormaTesi</span><h1>Un momento.</h1><p role="alert">{esc(e.message)}</p><a class="button" href="/area">Torna alla tua area</a></section>'),e.code,[]
  except Exception as e:
   import logging;logging.exception('Request failed')
   body,code,headers=self.page(self_req,'Problema temporaneo','<section class="narrow panel"><h1>Qualcosa non ha funzionato.</h1><p>Riprova tra poco. Le consegne già salvate restano nel tuo account.</p><a href="/area">Torna alla tua area</a></section>'),500,[]
  if isinstance(body,str):body=body.encode()
  headers=[('Content-Type','text/html; charset=utf-8'),('Content-Length',str(len(body))),('X-Content-Type-Options','nosniff'),('Referrer-Policy','same-origin'),('X-Frame-Options','DENY'),('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data: https://www.centrostudibn.it https://www.uniares.com https://www.studentitelematici.cloud https://mediakey.it; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://www.facebook.com"),('Cache-Control','no-store'),*headers]
  # avoid duplicate Content-Type for downloads/static assets
  types=[v for k,v in headers if k.lower()=='content-type'];headers=[(k,v) for k,v in headers if k.lower()!='content-type']+[('Content-Type',types[-1])]
  if not self.testing:headers.append(('Strict-Transport-Security','max-age=31536000; includeSubDomains'))
  if self_req.cookie:headers.append(('Set-Cookie',self_req.cookie))
  start_response(str(code)+' '+{200:'OK',303:'See Other',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',409:'Conflict',413:'Content Too Large',429:'Too Many Requests',500:'Internal Server Error',503:'Service Unavailable'}.get(code,'OK'),headers)
  return [body]
 def redirect(self,url):return '',303,[('Location',url)]
 def page(self,r,title,body):
  if r.method=='POST':
   for key,value in r.data.items():
    if key not in ['name','surname','matricola','email','faculty','subject','title','paragraph','outline','other_ateneo','body','description','amount']:continue
    body=re.sub(r'(<input\b[^>]*name="'+re.escape(key)+r'"[^>]*)(>)',lambda m:m[1]+' value="'+esc(value)+'"'+m[2],body)
    body=re.sub(r'(<textarea\b[^>]*name="'+re.escape(key)+r'"[^>]*>)(.*?)(</textarea>)',lambda m:m[1]+esc(value)+m[3],body,flags=re.S)
   selected=r.data.get('ateneo','')
   if selected in ATENEI:body=body.replace('<option>'+selected+'</option>','<option selected>'+selected+'</option>')
  auth=(f'<a href="/area">La mia area</a><form method="post" action="/logout" class="inline">{r.csrf()}<button class="text-button">Esci</button></form>' if r.user else '<a href="/login">Accedi</a><a class="button small" href="/registrati">Inizia da qui <span aria-hidden="true">↗</span></a>')
  return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · FormaTesi</title><meta name="description" content="Consulenza iniziale e un paragrafo dimostrativo gratuito sul tuo progetto di tesi."><link rel="icon" href="/static/favicon.svg"><link rel="stylesheet" href="/static/style.css"><script defer src="/static/app.js"></script></head><body><a class="skip" href="#main">Vai al contenuto</a><header><a class="brand" href="/" aria-label="FormaTesi, pagina iniziale"><span class="monogram">f.</span>Forma<span>Tesi</span></a><nav aria-label="Navigazione principale"><a class="desktop" href="/#come-funziona">Come funziona</a><a class="desktop" href="/#domande">Domande frequenti</a>{auth}</nav></header><main id="main">{body}</main><div class="contact-dock" aria-label="Contatti rapidi"><a class="dock-messenger" href="{MESSENGER}" target="_blank" rel="noopener" aria-label="Apri Messenger"><span aria-hidden="true">M</span><b>Messenger</b></a><a class="dock-whatsapp" href="{WHATSAPP}" target="_blank" rel="noopener" aria-label="Scrivi su WhatsApp al 350 581 5735"><span aria-hidden="true">☎</span><b>WhatsApp</b></a></div><footer><div><a class="brand" href="/">Forma<span>Tesi</span></a><p>Un progetto alla volta.<br>Il tuo, al centro.</p></div><div><a href="{FB}" rel="noopener" target="_blank">Facebook ↗</a><a href="{MESSENGER}" rel="noopener" target="_blank">Messenger ↗</a><a href="{WHATSAPP}" rel="noopener" target="_blank">WhatsApp ↗</a><a href="/privacy">Privacy</a><a href="/condizioni">Condizioni del servizio</a></div><p class="fine">Supporto alla ricerca e alla revisione accademica.<br>Servizio indipendente, non affiliato agli atenei indicati.<br>© {datetime.date.today().year} FormaTesi</p></footer></body></html>'''
 def closed(self,r):return self.page(r,'Il tuo spazio',f'<section class="narrow panel"><span class="eyebrow">Il nuovo spazio FormaTesi</span><h1>Ci siamo quasi.</h1><p>Stiamo completando l’attivazione dell’area riservata. Nel frattempo puoi contattarci su Facebook per parlare del tuo progetto.</p><a class="button" href="{FB}">Contatta FormaTesi ↗</a><a class="secondary" href="/anteprima">Esplora l’area di esempio</a><p class="fine">Le registrazioni non sono ancora aperte. In questa anteprima non vengono raccolti dati personali.</p></section>'),503,[]
 def route(self,r):
  p=r.path
  if p.startswith('/static/'):
   name=p.split('/')[-1]
   if name not in ['style.css','app.js','favicon.svg']:raise Failure('Pagina non trovata.',404)
   return (ROOT/'static'/name).read_bytes(),200,[('Content-Type',{'css':'text/css','js':'application/javascript','svg':'image/svg+xml'}[name.split('.')[-1]])]
  if p=='/favicon.ico':return (ROOT/'static/favicon.svg').read_bytes(),200,[('Content-Type','image/svg+xml')]
  if p=='/health':return json.dumps({'status':'ok','portal': 'active' if self.ready else 'setup_required'}),200,[('Content-Type','application/json')]
  if p=='/robots.txt':return 'User-agent: *\nDisallow: /area\nDisallow: /lavori/\nDisallow: /gestione\nDisallow: /verifica\nDisallow: /reimposta\n',200,[('Content-Type','text/plain')]
  if p=='/':return self.page(r,'La tua tesi comincia a prendere forma',landing()),200,[]
  if p=='/anteprima':return self.page(r,'Anteprima area personale',demo()),200,[]
  if p in ['/privacy','/condizioni']:return self.page(r,'Informazioni',legal(self,p)),200,[]
  if not self.ready:return self.closed(r)
  r.load_session()
  if r.method=='POST':r.check_csrf()
  if p=='/facebook':return self.facebook_start(r)
  if p=='/facebook/callback':return self.facebook_callback(r)
  if p in ['/registrati','/login','/recupera','/reimposta','/verifica','/registrati-facebook']:return self.auth(r)
  if not r.user:return self.redirect('/login')
  if p=='/logout' and r.method=='POST':
   self.mutate('DELETE FROM sessions WHERE id=?',(r.session['id'],));r.clear_cookie();return self.redirect('/')
  if p=='/area':return self.dashboard(r)
  if p=='/nuovo':return self.new_project(r)
  if p=='/gestione/email' and r.method=='POST':
   r.admin()
   for m in self.query('SELECT * FROM outbox WHERE sent=0 ORDER BY created LIMIT 10'):self.send_mail(m['id'],m['email'],m['subject'],m['body'])
   return self.redirect('/area')
  if p.startswith('/file/'):
   f=self.query('SELECT * FROM files WHERE id=?',(p.split('/')[-1],),True)
   if not f:raise Failure('File non trovato.',404)
   self.owned(r,f['project_id']);data=base64.b64decode(f['data'])
   return data,200,[('Content-Type',f['mime']),('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(f['name']))]
  if p.startswith('/lavori/'):
   bits=p.strip('/').split('/');project=self.owned(r,bits[1]);action=bits[2] if len(bits)>2 else ''
   if r.method=='POST':return self.project_action(r,project,action)
   return self.project_page(r,project)
  raise Failure('Pagina non trovata.',404)
 def facebook_enabled(self):return bool(self.cfg.get('FACEBOOK_APP_ID') and self.cfg.get('FACEBOOK_APP_SECRET'))
 def facebook_start(self,r):
  if not self.facebook_enabled():raise Failure('L’accesso con Facebook sarà disponibile appena l’app FormaTesi verrà collegata a Meta.',503)
  state=secrets.token_urlsafe(32);self.mutate('UPDATE sessions SET csrf=? WHERE id=?',(state,r.session['id']));r.session['csrf']=state
  params={'client_id':self.cfg['FACEBOOK_APP_ID'],'redirect_uri':self.origin+'/facebook/callback','state':state,'scope':'email,public_profile','response_type':'code'}
  return self.redirect('https://www.facebook.com/v23.0/dialog/oauth?'+urllib.parse.urlencode(params))
 def facebook_callback(self,r):
  if not self.facebook_enabled():raise Failure('Accesso Facebook non configurato.',503)
  if r.q.get('error'):raise Failure('Accesso con Facebook annullato.')
  if not r.session or not hmac.compare_digest(r.q.get('state',''),r.session['csrf']):raise Failure('La richiesta Facebook è scaduta. Riprova.',403)
  code=r.q.get('code','')
  if not code:raise Failure('Facebook non ha restituito un codice di accesso.')
  try:
   exchange=urllib.parse.urlencode({'client_id':self.cfg['FACEBOOK_APP_ID'],'client_secret':self.cfg['FACEBOOK_APP_SECRET'],'redirect_uri':self.origin+'/facebook/callback','code':code}).encode()
   req=urllib.request.Request('https://graph.facebook.com/v23.0/oauth/access_token',data=exchange)
   with urllib.request.urlopen(req,timeout=10) as response:token=json.loads(response.read())['access_token']
   graph='https://graph.facebook.com/v23.0/me?'+urllib.parse.urlencode({'fields':'id,first_name,last_name,email','access_token':token})
   with urllib.request.urlopen(graph,timeout=10) as response:profile=json.loads(response.read())
  except Exception:raise Failure('Non è stato possibile completare l’accesso con Facebook. Riprova tra poco.',503)
  if not profile.get('id') or not profile.get('email'):raise Failure('Facebook non ha fornito un indirizzo email. Registrati con email e password.')
  email=profile['email'].lower();facebook_id=str(profile['id'])
  existing=self.query('SELECT * FROM users WHERE facebook_id=? OR email=?',(facebook_id,email),True)
  if existing:
   self.mutate('UPDATE users SET facebook_id=?,verified=1 WHERE id=?',(facebook_id,existing['id']))
   existing=self.query('SELECT * FROM users WHERE id=?',(existing['id'],),True);r.login(existing);return self.redirect('/area')
  payload={'id':facebook_id,'name':profile.get('first_name',''),'surname':profile.get('last_name',''),'email':email,'exp':now()+600}
  encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
  signature=hmac.new(self.cfg['FACEBOOK_APP_SECRET'].encode(),encoded.encode(),hashlib.sha256).hexdigest()
  return self.redirect('/registrati-facebook?ticket='+urllib.parse.quote(encoded+'.'+signature))
 def facebook_ticket(self,ticket):
  try:
   encoded,signature=ticket.split('.',1);expected=hmac.new(self.cfg['FACEBOOK_APP_SECRET'].encode(),encoded.encode(),hashlib.sha256).hexdigest()
   if not hmac.compare_digest(signature,expected):raise ValueError()
   payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
   if payload['exp']<now():raise ValueError()
   return payload
  except Exception:raise Failure('La registrazione Facebook è scaduta. Accedi nuovamente con Facebook.')
 def owned(self,r,ident):
  project=self.query('SELECT * FROM projects WHERE id=?',(ident,),True)
  if not project or (project['user_id']!=r.user['id'] and r.user['role']!='admin'):raise Failure('Lavoro non trovato.',404)
  return project
 def auth(self,r):
  p=r.path;error='';message=''
  if p=='/verifica':
   token=self.query('SELECT * FROM tokens WHERE id=? AND kind=? AND expires>?',(digest(r.q.get('token','')),'verify',now()),True)
   if not token:raise Failure('Link scaduto o già utilizzato. Puoi richiederne un altro dalla pagina di accesso.')
   with self.db.connect() as c:
    user=self.db.run(c,'SELECT * FROM users WHERE id=?',(token['user_id'],)).fetchone()
    role='admin' if user['email'].lower()==self.cfg.get('ADMIN_EMAIL','').lower() else 'student'
    self.db.run(c,'UPDATE users SET verified=1,role=? WHERE id=?',(role,user['id']))
    self.db.run(c,'DELETE FROM tokens WHERE id=?',(token['id'],))
   return self.page(r,'Email verificata','<section class="narrow panel"><h1>Email verificata.</h1><p>Il tuo account è pronto.</p><a class="button" href="/login">Accedi alla tua area</a></section>'),200,[]
  if r.method=='POST':
   self.limit('auth:'+r.ip,30)
   try:
    email=r.data.get('email','').strip().lower()
    if p=='/registrati-facebook':
     profile=self.facebook_ticket(r.data.get('ticket',''));email=profile['email'];matricola=r.require('matricola',150)
     if r.data.get('terms')!='yes':raise Failure('Leggi e accetta le condizioni per continuare.')
     existing=self.query('SELECT * FROM users WHERE facebook_id=? OR email=?',(profile['id'],email),True)
     if existing:self.mutate('UPDATE users SET facebook_id=?,verified=1 WHERE id=?',(profile['id'],existing['id']));user=self.query('SELECT * FROM users WHERE id=?',(existing['id'],),True)
     else:
      ident=uid();self.mutate('INSERT INTO users(id,name,surname,email,password,matricola,verified,role,created,facebook_id) VALUES(?,?,?,?,?,?,1,?,?,?)',(ident,profile['name'],profile['surname'],email,password_hash(secrets.token_urlsafe(32)),matricola,'student',now(),profile['id']));user=self.query('SELECT * FROM users WHERE id=?',(ident,),True)
     r.login(user);self.audit(user['id'],'facebook.registration');return self.redirect('/area')
    if p!='/reimposta' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise Failure('Inserisci un indirizzo email valido.')
    if p=='/registrati':
     self.limit('register:'+r.ip,8,3600)
     values=[r.require(k,150) for k in ['name','surname','matricola']];password=r.password()
     if r.data.get('terms')!='yes':raise Failure('Leggi e accetta le condizioni per continuare.')
     existing=self.query('SELECT * FROM users WHERE email=?',(email,),True)
     if not existing:
      ident=uid();self.mutate('INSERT INTO users(id,name,surname,email,password,matricola,verified,role,created) VALUES(?,?,?,?,?,?,0,?,?)',(ident,*values[:2],email,password_hash(password),values[2],'student',now()))
      existing=self.query('SELECT * FROM users WHERE id=?',(ident,),True)
      raw=self.token(existing,'verify');self.mail(email,'Verifica il tuo account FormaTesi','Per verificare la tua email apri questo link entro un’ora: '+self.origin+'/verifica?token='+raw)
     message='Se l’indirizzo può essere registrato, riceverai un’email per attivare l’account. Se hai già un account, accedi o recupera la password.'
    elif p=='/login':
     self.limit('login:'+email,10)
     user=self.query('SELECT * FROM users WHERE email=?',(email,),True)
     if not user or not password_ok(r.data.get('password',''),user['password']):raise Failure('Email o password non corrette.')
     if not user['verified']:
      raw=self.token(user,'verify');self.mail(email,'Verifica il tuo account FormaTesi',self.origin+'/verifica?token='+raw);raise Failure('Verifica la tua email prima di accedere. Ti abbiamo inviato un nuovo link.')
     r.login(user);self.audit(user['id'],'login');return self.redirect('/area')
    elif p=='/recupera':
     self.limit('reset:'+email,3,3600);user=self.query('SELECT * FROM users WHERE email=?',(email,),True)
     if user:
      raw=self.token(user,'reset');self.mail(email,'Reimposta la password FormaTesi','Apri entro un’ora: '+self.origin+'/reimposta?token='+raw)
     message='Se l’indirizzo è associato a un account, riceverai le istruzioni via email.'
    elif p=='/reimposta':
     token=self.query('SELECT * FROM tokens WHERE id=? AND kind=? AND expires>?',(digest(r.data.get('token','')),'reset',now()),True)
     if not token:raise Failure('Link scaduto o non valido. Richiedi un nuovo link.')
     password=r.password()
     with self.db.connect() as c:
      # Claim the one-time token inside the same transaction as the password change.
      deleted=self.db.run(c,'DELETE FROM tokens WHERE id=?',(token['id'],)).rowcount
      if not deleted:raise Failure('Link già utilizzato.')
      self.db.run(c,'UPDATE users SET password=? WHERE id=?',(password_hash(password),token['user_id']))
      self.db.run(c,'DELETE FROM sessions WHERE user_id=?',(token['user_id'],))
     return self.page(r,'Password aggiornata','<section class="narrow panel"><h1>Password aggiornata.</h1><a class="button" href="/login">Accedi</a></section>'),200,[]
   except Failure as e:error=e.message
  title={'/registrati':'Il tuo progetto,\nil tuo spazio.','/login':'Bentornato.','/recupera':'Ritrova il tuo accesso.','/reimposta':'Una nuova password.','/registrati-facebook':'Completa il tuo profilo.'}[p]
  fields=''
  profile=None
  if p=='/registrati-facebook':
   try:profile=self.facebook_ticket(r.q.get('ticket',r.data.get('ticket','')))
   except Failure as e:error=e.message
   if profile:fields=f'<div class="facebook-profile"><span class="fb-mark">f</span><div><strong>{esc(profile["name"]+" "+profile["surname"])}</strong><span>{esc(profile["email"])}</span></div></div>'+field('matricola','Matricola universitaria')+f'<input type="hidden" name="ticket" value="{esc(r.q.get("ticket",r.data.get("ticket","")))}"><label class="check"><input type="checkbox" name="terms" value="yes" required> <span>Ho letto l’<a href="/privacy" target="_blank">informativa privacy</a> e accetto le <a href="/condizioni" target="_blank">condizioni del servizio</a>.</span></label>'
  if p=='/registrati':fields='<div class="grid two">'+field('name','Nome',autocomplete='given-name')+field('surname','Cognome',autocomplete='family-name')+'</div>'+field('matricola','Matricola universitaria')
  if p not in ['/reimposta','/registrati-facebook']:fields+=field('email','Email','email',autocomplete='email')
  if p in ['/registrati','/login','/reimposta']:fields+=field('password','Password','password',autocomplete='current-password' if p=='/login' else 'new-password',extra='minlength="12"' if p!='/login' else '')
  if p in ['/registrati','/reimposta']:fields+='<p class="fine">Almeno 12 caratteri. Puoi usare una frase facile da ricordare.</p>'
  if p=='/registrati':fields+='<label class="check"><input type="checkbox" name="terms" value="yes" required> <span>Ho letto l’<a href="/privacy" target="_blank">informativa privacy</a> e accetto le <a href="/condizioni" target="_blank">condizioni del servizio</a>.</span></label>'
  if p=='/reimposta':fields+=f'<input type="hidden" name="token" value="{esc(r.q.get("token",r.data.get("token","")))}">'
  label={'/registrati':'Crea il tuo account','/login':'Accedi','/recupera':'Invia il link','/reimposta':'Salva la password','/registrati-facebook':'Entra nella tua area'}[p]
  facebook_button='' if p in ['/recupera','/reimposta','/registrati-facebook'] else '<a class="facebook-login" href="/facebook"><span aria-hidden="true">f</span> Continua con Facebook</a><div class="form-divider"><span>oppure</span></div>'
  body=f'<section class="auth-layout"><div class="auth-intro"><span class="eyebrow">Il tuo spazio FormaTesi</span><h1>{esc(title).replace(chr(10),"<br>")}</h1><p>La tua richiesta, le consegne e ogni revisione. Tutto nello stesso posto.</p><div class="line-art">F<span>orma.</span></div></div><div class="panel">'+(f'<div role="status" class="notice">{esc(message)}</div>' if message else '')+(f'<div role="alert" class="notice error">{esc(error)}</div>' if error else '')+facebook_button+f'<form method="post">{r.csrf()}{fields}<button class="button full">{label} ↗</button></form><div class="auth-links"><a href="/login">Accedi</a><a href="/registrati">Registrati</a><a href="/recupera">Password dimenticata?</a></div></div></section>'
  return self.page(r,label,body),200,[]
 def dashboard(self,r):
  admin=r.user['role']=='admin';status=r.q.get('stato','');search=r.q.get('q','').strip()
  projects=self.query('SELECT p.*,u.name,u.surname,u.email FROM projects p JOIN users u ON u.id=p.user_id '+('' if admin else 'WHERE p.user_id=? ')+'ORDER BY p.updated DESC',() if admin else (r.user['id'],))
  counts={k:sum(p['status']==k for p in projects) for k in STATUS}
  filtered=[p for p in projects if (not status or p['status']==status) and (not search or normal(search) in normal(p['title']+' '+p['name']+' '+p['surname']+' '+p['ateneo']))]
  cards=''.join(project_card(p,admin) for p in filtered) or '<div class="empty"><span class="empty-icon">↗</span><h2>'+('Nessun lavoro trovato.' if search or status else 'Il tuo prossimo passo comincia qui.')+'</h2><p>'+('Prova a cambiare i filtri.' if search or status else 'Raccontaci la tua tesi per richiedere il primo lavoro.')+'</p>'+('' if admin else '<a class="button" href="/nuovo">Crea la tua richiesta</a>')+'</div>'
  filters=''.join(f'<a class="filter {"selected" if status==k else ""}" href="/area?stato={k}">{v} <b>{counts[k]}</b></a>' for k,v in STATUS.items())
  mail=''
  if admin:
   pending=self.query('SELECT COUNT(*) AS n FROM outbox WHERE sent=0',one=True)['n']
   if pending:mail=f'<div class="notice">{pending} notifiche email in attesa di invio.<form method="post" action="/gestione/email">{r.csrf()}<button class="text-button">Riprova gli invii</button></form></div>'
  body=f'<section class="workspace"><div class="page-heading"><div><span class="eyebrow">{"Pannello di gestione" if admin else "Area personale"}</span><h1>Ciao, {esc(r.user["name"])}.</h1><p>{"Le richieste da seguire, tutte qui." if admin else "Qui la tua tesi prende forma, un passo alla volta."}</p></div>'+('' if admin else '<a class="button" href="/nuovo">Nuova richiesta +</a>')+f'</div>{mail}<div class="stats">'+''.join(f'<div><strong>{counts[k]:02}</strong><span>{v}</span></div>' for k,v in STATUS.items())+f'</div><div class="toolbar"><div class="filters"><a class="filter {"selected" if not status else ""}" href="/area">Tutti</a>{filters}</div><form method="get" class="search"><label class="sr-only" for="search">Cerca un lavoro</label><input id="search" name="q" placeholder="Cerca un lavoro…" value="{esc(search)}"><button aria-label="Cerca">⌕</button></form></div><div class="project-list">{cards}</div></section>'
  return self.page(r,'La tua area',body),200,[]
 def new_project(self,r):
  error=''
  used=self.query('SELECT id FROM projects WHERE user_id=? AND free=1',(r.user['id'],),True)
  if r.method=='POST':
   try:
    self.limit('projects:'+r.user['id'],10,3600)
    ateneo=r.require('ateneo',150)
    if ateneo=='Altro ateneo':ateneo=r.require('other_ateneo',150)
    faculty=r.require('faculty',250);subject=r.require('subject',250);title=r.require('title',500)
    outline=r.data.get('outline','').strip();paragraph=r.data.get('paragraph','').strip()
    if max(len(outline),len(paragraph))>20000:raise Failure('Il testo inserito è troppo lungo.')
    attachment=r.attachment()
    if not outline and not paragraph and not attachment:raise Failure('Inserisci l’indice, carica il relativo file oppure indica il titolo del paragrafo.')
    if used and r.data.get('paid')!='yes':raise Failure('Hai già richiesto la prova gratuita. Puoi inviare una richiesta di preventivo.')
    ident=uid();identity=digest(normal(ateneo)+'|'+normal(r.user['matricola']))
    with self.db.connect() as c:
     self.db.run(c,'INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?)',(ident,r.user['id'],ateneo,faculty,subject,title,outline,paragraph,'waiting',0 if used else 1,identity,now(),now()))
     if attachment:self.save_file(c,ident,None,attachment)
    self.audit(r.user['id'],'project.created:'+ident)
    self.mail(self.cfg.get('ADMIN_EMAIL','admin@example.test'),'Nuova richiesta FormaTesi','Apri la richiesta: '+self.origin+'/lavori/'+ident)
    return self.redirect('/lavori/'+ident)
   except Failure as e:error=e.message
  options='<option value="">Scegli il tuo ateneo</option>'+''.join(f'<option>{esc(x)}</option>' for x in ATENEI)
  form=f'''<form method="post" class="project-form" data-upload>{r.csrf()}<div class="section-label">01 <span>Il tuo percorso</span></div><label>Ateneo<select name="ateneo" required id="ateneo">{options}</select></label><div id="other-ateneo" hidden>{field('other_ateneo','Nome dell’ateneo',required=False)}</div>{field('faculty','Facoltà / corso di laurea',placeholder='Es. Scienze dell’educazione · L-19')}{field('subject','Materia',placeholder='Es. Pedagogia generale')}<div class="section-label">02 <span>Il tuo progetto</span></div>{field('title','Titolo della tesi',placeholder='Anche provvisorio')}<label>Indice della tesi<textarea name="outline" rows="5" maxlength="20000" placeholder="Incolla qui l’indice, se disponibile…"></textarea></label><label>Oppure carica l’indice<input type="file" id="attachment" accept=".pdf,.docx,.txt"><span class="fine">PDF, Word (.docx) o TXT · massimo 5 MB. Evita dati personali non necessari.</span></label><input type="hidden" name="file_name"><input type="hidden" name="file_data">{field('paragraph','Titolo del paragrafo',required=False,placeholder='Indica il paragrafo da cui iniziare')}<p class="fine">Serve almeno l’indice (testo o file) oppure il titolo del paragrafo.</p>'''
  if used:form+='<div class="notice">La tua prova gratuita è già stata richiesta. Questa nuova richiesta serve a ricevere un preventivo.</div><label class="check"><input type="checkbox" name="paid" value="yes" required> Richiedo un preventivo senza impegno.</label>'
  form+='<button class="button full">'+('Richiedi un preventivo' if used else 'Invia la richiesta gratuita')+' ↗</button><p class="fine" data-upload-status role="status">Nessun pagamento richiesto in questa fase.</p></form>'
  body=f'<section class="workspace"><a class="back" href="/area">← Torna ai tuoi lavori</a><div class="page-heading"><div><span class="eyebrow">Un nuovo inizio</span><h1>Parlaci della tua tesi.</h1><p>Le informazioni giuste per un primo lavoro su misura.</p></div></div><div class="form-layout"><div class="panel">'+(f'<p class="notice error" role="alert">{esc(error)}</p>' if error else '')+form+'</div><aside><div class="document" id="cover"><div class="doc-university" data-preview="ateneo">Il tuo ateneo</div><div class="doc-rule"></div><p data-preview="faculty">Il tuo corso di laurea</p><span class="doc-kicker">TESI DI LAUREA</span><h2 data-preview="title">Il titolo della tua tesi prende forma qui.</h2><p data-preview="subject">La tua materia</p><div class="doc-bottom">'+esc(r.user['name']+' '+r.user['surname'])+'</div></div><p class="fine">Anteprima orientativa, non frontespizio ufficiale dell’ateneo.</p><div class="aside-note"><h3>E dopo l’invio?</h3><p>Valutiamo il materiale e prepariamo il primo lavoro. Troverai la consegna nella tua area e riceverai un avviso via email.</p></div></aside></div></section>'
  return self.page(r,'Nuova richiesta',body),200,[]
 def save_file(self,c,project,event,attachment):
  name,mime,data=attachment;self.db.run(c,'INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',(uid(),project,event,name,mime,base64.b64encode(data).decode(),hashlib.sha256(data).hexdigest(),now()))
 def project_action(self,r,p,action):
  if action not in ['revisione','consegna','preventivo','accetta','richiedi-preventivo']:raise Failure('Azione non valida.',404)
  if action in ['consegna','preventivo']:r.admin()
  elif r.user['id']!=p['user_id']:raise Failure('Questa azione è riservata allo studente.',403)
  with self.db.connect() as c:
   locked=self.db.run(c,'SELECT * FROM projects WHERE id=?'+(' FOR UPDATE' if self.db.pg else ''),(p['id'],)).fetchone();p=dict(locked)
   if action in ['consegna','revisione']:
    submitted=int(r.data.get('version','-1'))
    if submitted!=p['revision'] or r.data.get('status')!=p['status']:raise Failure('Il lavoro è stato aggiornato. Ricarica la pagina prima di continuare.',409)
    body=r.data.get('body','').strip()
    if len(body)>100000:raise Failure('Il testo supera il limite consentito.')
    attachment=r.attachment()
    if action=='revisione':
     if p['status'] not in ['delivered','revised']:raise Failure('Puoi richiedere una revisione dopo una consegna.',409)
     if not body:raise Failure('Spiega quali modifiche desideri.')
     status='revision_requested';revision=p['revision'];kind='revision_request'
    else:
     if p['status'] not in ['waiting','revision_requested']:raise Failure('La consegna è già stata pubblicata. Attendi una richiesta di revisione.',409)
     if not body and not attachment:raise Failure('Inserisci il testo oppure allega il documento da consegnare.')
     revision=0 if p['status']=='waiting' else p['revision']+1;status='delivered' if revision==0 else 'revised';kind='delivery'
    eid=uid();self.db.run(c,'INSERT INTO events VALUES(?,?,?,?,?,?,?)',(eid,p['id'],r.user['id'],kind,body,revision,now()))
    if attachment:self.save_file(c,p['id'],eid,attachment)
    self.db.run(c,'UPDATE projects SET status=?,revision=?,updated=? WHERE id=?',(status,revision,now(),p['id']))
   elif action=='preventivo':
    description=r.require('description',10000)
    value=r.require('amount',20).replace(',','.')
    from decimal import Decimal,InvalidOperation
    try:
     amount=Decimal(value)
     if not amount.is_finite() or amount<=0 or amount>100000 or amount.as_tuple().exponent < -2:raise InvalidOperation()
     cents=int(amount*100)
    except InvalidOperation:raise Failure('Inserisci un importo valido, con al massimo due decimali.')
    if self.db.run(c,'SELECT id FROM quotes WHERE project_id=? AND status=?',(p['id'],'accepted')).fetchone():raise Failure('Esiste già una proposta accettata per questo lavoro.',409)
    self.db.run(c,'UPDATE quotes SET status=? WHERE project_id=? AND status=?',('superseded',p['id'],'proposed'))
    self.db.run(c,'INSERT INTO quotes VALUES(?,?,?,?,?,?,NULL)',(uid(),p['id'],description,cents,'proposed',now()))
   elif action=='accetta':
    if r.data.get('confirm')!='yes':raise Failure('Conferma di aver letto la proposta.')
    changed=self.db.run(c,'UPDATE quotes SET status=?,accepted=? WHERE id=? AND project_id=? AND status=?',('accepted',now(),r.data.get('quote'),p['id'],'proposed')).rowcount
    if not changed:raise Failure('La proposta è già stata aggiornata. Ricarica la pagina.',409)
   elif action=='richiedi-preventivo':
    if not self.db.run(c,'SELECT id FROM events WHERE project_id=? AND kind=?',(p['id'],'quote_request')).fetchone():self.db.run(c,'INSERT INTO events VALUES(?,?,?,?,?,?,?)',(uid(),p['id'],r.user['id'],'quote_request','Richiesto un preventivo per proseguire.',p['revision'],now()))
  self.audit(r.user['id'],action+':'+p['id'])
  if action in ['consegna','preventivo']:self.notify(p,'Il tuo lavoro su FormaTesi è stato aggiornato')
  else:self.mail(self.cfg.get('ADMIN_EMAIL','admin@example.test'),'Aggiornamento richiesta FormaTesi',self.origin+'/lavori/'+p['id'])
  return self.redirect('/lavori/'+p['id'])
 def project_page(self,r,p):
  admin=r.user['role']=='admin';events=self.query('SELECT * FROM events WHERE project_id=? ORDER BY created,id',(p['id'],));files=self.query('SELECT id,event_id,name FROM files WHERE project_id=? ORDER BY created',(p['id'],))
  quotes=self.query('SELECT * FROM quotes WHERE project_id=? ORDER BY created DESC',(p['id'],))
  student=self.query('SELECT * FROM users WHERE id=?',(p['user_id'],),True)
  history=''
  for e in events:
   label=('Prima consegna' if e['revision']==0 else 'Revisione n. '+str(e['revision'])) if e['kind']=='delivery' else ('Richiesta di revisione' if e['kind']=='revision_request' else 'Richiesta di preventivo')
   history+=f'<article class="timeline-item"><div class="event-heading"><h3>{label}</h3><time>{date(e["created"])}</time></div><div class="prose">{esc(e["body"])}</div>'+''.join(file_link(f) for f in files if f['event_id']==e['id'])+'</article>'
  if not history:history='<div class="empty compact"><h3>La richiesta è arrivata.</h3><p>Qui compariranno la prima consegna e le successive revisioni.</p></div>'
  warnings=''
  if admin:
   others=self.query('SELECT p.*,u.name,u.surname FROM projects p JOIN users u ON u.id=p.user_id WHERE p.id<>?',(p['id'],))
   current_sha={x['sha'] for x in self.query('SELECT sha FROM files WHERE project_id=?',(p['id'],))}
   from difflib import SequenceMatcher
   matches=[]
   for other in others:
    reasons=[]
    if other['identity_key']==p['identity_key']:reasons.append('stesso ateneo e matricola')
    if SequenceMatcher(None,normal(other['title']),normal(p['title'])).ratio()>.85:reasons.append('titolo simile')
    if p['outline'] and other['outline'] and SequenceMatcher(None,normal(p['outline']),normal(other['outline'])).ratio()>.85:reasons.append('indice simile')
    if current_sha and current_sha.intersection(x['sha'] for x in self.query('SELECT sha FROM files WHERE project_id=?',(other['id'],))):reasons.append('allegato identico')
    if reasons:matches.append(f'<li><a href="/lavori/{other["id"]}">{esc(other["name"]+" "+other["surname"])}</a>: {esc(", ".join(reasons))}</li>')
   if matches:warnings='<div class="notice"><strong>Verifica possibili duplicati prima di lavorare</strong><ul>'+''.join(matches)+'</ul><p>Una somiglianza non dimostra che si tratti della stessa persona.</p></div>'
  editor=''
  can_deliver=admin and p['status'] in ['waiting','revision_requested'];can_request=not admin and p['status'] in ['delivered','revised']
  if can_deliver or can_request:
   action='consegna' if can_deliver else 'revisione';label='Pubblica la consegna' if can_deliver else 'Richiedi una revisione'
   editor=f'<section class="panel"><h2>{label}</h2><form method="post" action="/lavori/{p["id"]}/{action}" data-upload>{r.csrf()}<input type="hidden" name="version" value="{p["revision"]}"><input type="hidden" name="status" value="{p["status"]}"><label>{"Testo del lavoro" if can_deliver else "Quali modifiche servono?"}<textarea name="body" rows="9" maxlength="100000" {"" if can_deliver else "required"}></textarea></label><label>{"Documento da consegnare" if can_deliver else "Osservazioni del relatore o altro allegato"}<input type="file" id="attachment" accept=".pdf,.docx,.txt"></label><p class="fine">PDF, DOCX o TXT · massimo 5 MB</p><input type="hidden" name="file_name"><input type="hidden" name="file_data"><button class="button">{label}</button><p role="status" data-upload-status></p></form></section>'
  proposal=''
  for q in quotes:
   proposal+=f'<article class="quote"><span class="eyebrow">{"Proposta precedente" if q["status"]=="superseded" else "La tua proposta"}</span><h2>€ {q["cents"]/100:,.2f}</h2><div class="prose">{esc(q["description"])}</div>'
   if q['status']=='accepted':proposal+='<p class="badge success">Interesse confermato il '+date(q['accepted'])+'</p><p class="fine">Nessun pagamento effettuato sul sito. FormaTesi ti contatterà per confermare l’incarico e le modalità di pagamento.</p>'
   elif q['status']=='proposed' and not admin:proposal+=f'<form method="post" action="/lavori/{p["id"]}/accetta">{r.csrf()}<input type="hidden" name="quote" value="{q["id"]}"><label class="check"><input type="checkbox" name="confirm" value="yes" required> Ho letto la proposta e desidero essere contattato per procedere.</label><button class="button">Conferma interesse</button><p class="fine">Questa conferma non addebita importi e non conclude un acquisto.</p></form>'
   proposal+='</article>'
  if admin:proposal+=f'<details class="panel"><summary>Prepara un preventivo</summary><form method="post" action="/lavori/{p["id"]}/preventivo">{r.csrf()}{field("amount","Importo complessivo in euro",extra="inputmode=decimal")}<label>Cosa comprende, tempi e revisioni incluse<textarea name="description" rows="6" required maxlength="10000"></textarea></label><button class="button">Invia la proposta</button></form></details>'
  elif not quotes:proposal+=f'<section class="panel"><h3>Vuoi proseguire insieme?</h3><p>Richiedi una proposta riferita al tuo progetto.</p><form method="post" action="/lavori/{p["id"]}/richiedi-preventivo">{r.csrf()}<button class="button">Richiedi preventivo</button></form></section>'
  contact=self.cfg.get('WHATSAPP_NUMBER','393505815735');contact_html=f'<a class="button secondary" href="https://wa.me/{esc(contact)}?text={urllib.parse.quote("Ciao FormaTesi, vorrei una consulenza gratuita per la mia tesi.")}">Parliamone su WhatsApp ↗</a>' if re.fullmatch(r'\d{8,15}',contact) else f'<a class="secondary" href="{FB}">Contatta FormaTesi su Facebook ↗</a>'
  body=f'<section class="workspace"><a class="back" href="/area">← Tutti i lavori</a><div class="page-heading"><div><span class="eyebrow">{esc(p["ateneo"])} · {"Prova gratuita" if p["free"] else "Richiesta di preventivo"}</span><h1 class="project-title">{esc(p["title"])}</h1>{badge(p)}</div></div>{warnings}<div class="detail-layout"><div><section class="panel"><h2>Il percorso del lavoro</h2><div class="timeline">{history}</div></section>{editor}</div><aside><section class="panel"><span class="eyebrow">La scheda del progetto</span><dl><dt>Studente</dt><dd>{esc(student["name"]+" "+student["surname"])}</dd><dt>Facoltà / corso</dt><dd>{esc(p["faculty"])}</dd><dt>Materia</dt><dd>{esc(p["subject"])}</dd><dt>Paragrafo richiesto</dt><dd>{esc(p["paragraph"] or "Da individuare nell’indice")}</dd><dt>Data di richiesta</dt><dd>{date(p["created"])}</dd></dl><details><summary>Indice e materiali iniziali</summary><div class="prose">{esc(p["outline"])}</div>'+''.join(file_link(f) for f in files if not f['event_id'])+f'</details></section>{proposal}{contact_html}</aside></div></section>'
  return self.page(r,p['title'],body),200,[]

class Request:
 def __init__(self,site,env):
  self.site=site;self.env=env;self.path=env.get('PATH_INFO','/');self.method=env.get('REQUEST_METHOD','GET');self.q={k:v[-1] for k,v in urllib.parse.parse_qs(env.get('QUERY_STRING','')).items()};self.data={};self.user=None;self.session=None;self.cookie=None;self.ip=env.get('REMOTE_ADDR','unknown')
  if self.method=='POST':
   try:length=int(env.get('CONTENT_LENGTH','0') or 0)
   except ValueError:length=0
   if 0<=length<=8*1024*1024:
    try:self.data={k:v[-1] for k,v in urllib.parse.parse_qs(env['wsgi.input'].read(length).decode(),max_num_fields=40).items()}
    except (UnicodeDecodeError,ValueError):self.data={'_invalid':'1'}
   else:self.data={'_oversized':'1'}
 def load_session(self):
  try:
   cookies=http.cookies.SimpleCookie(self.env.get('HTTP_COOKIE',''));raw=cookies['ft_session'].value if 'ft_session' in cookies else ''
   self.session=self.site.query('SELECT * FROM sessions WHERE id=? AND expires>?',(digest(raw),now()),True) if raw else None
  except http.cookies.CookieError:self.session=None
  if self.session and self.session['user_id']:self.user=self.site.query('SELECT * FROM users WHERE id=?',(self.session['user_id'],),True)
 def set_cookie(self,raw,age=604800):self.cookie='ft_session='+raw+'; Path=/; HttpOnly; SameSite=Lax; Max-Age='+str(age)+('' if self.site.testing else '; Secure')
 def clear_cookie(self):self.set_cookie('',0)
 def login(self,user):
  if self.session:self.site.mutate('DELETE FROM sessions WHERE id=?',(self.session['id'],))
  raw=secrets.token_urlsafe(32);self.session={'id':digest(raw),'user_id':user['id'],'csrf':secrets.token_urlsafe(32),'expires':now()+604800}
  self.site.mutate('INSERT INTO sessions VALUES(?,?,?,?)',tuple(self.session.values()));self.set_cookie(raw);self.user=user
 def csrf(self):
  if not self.session:
   self.site.limit('forms:'+self.ip,200,3600)
   self.site.mutate('DELETE FROM sessions WHERE expires<?',(now(),))
   raw=secrets.token_urlsafe(32);self.session={'id':digest(raw),'user_id':None,'csrf':secrets.token_urlsafe(32),'expires':now()+3600};self.site.mutate('INSERT INTO sessions VALUES(?,?,?,?)',tuple(self.session.values()));self.set_cookie(raw,3600)
  return '<input type="hidden" name="csrf" value="'+self.session['csrf']+'">'
 def check_csrf(self):
  if self.data.get('_invalid'):raise Failure('Il modulo inviato non è valido.')
  if self.data.get('_oversized'):raise Failure('File troppo grande. Il limite è 5 MB.',413)
  origin=self.env.get('HTTP_ORIGIN')
  if origin and origin!=self.site.origin:raise Failure('Origine della richiesta non valida.',403)
  if not self.session or not hmac.compare_digest(self.data.get('csrf',''),self.session['csrf']):raise Failure('La sessione del modulo è scaduta. Ricarica la pagina e riprova.',403)
 def admin(self):
  if not self.user or self.user['role']!='admin':raise Failure('Accesso riservato.',403)
 def require(self,key,maximum):
  v=self.data.get(key,'').strip()
  if not v or len(v)>maximum:raise Failure('Controlla il campo '+{'faculty':'facoltà / corso','subject':'materia','title':'titolo','ateneo':'ateneo','body':'descrizione','name':'nome','surname':'cognome','matricola':'matricola'}.get(key,key)+'.')
  return v
 def password(self):
  value=self.data.get('password','')
  if not 12<=len(value)<=256:raise Failure('La password deve contenere da 12 a 256 caratteri.')
  return value
 def attachment(self):
  name=self.data.get('file_name','');raw=self.data.get('file_data','')
  if not name and not raw:return None
  if not name or not raw:raise Failure('L’allegato non è stato caricato. Riprova.')
  name=name.replace('\\','/').split('/')[-1][:180];extension=Path(name).suffix.lower()
  if extension not in ['.pdf','.docx','.txt']:raise Failure('Sono accettati soltanto PDF, DOCX e TXT.')
  try:data=base64.b64decode(raw,validate=True)
  except Exception:raise Failure('Allegato non valido.')
  if not data or len(data)>5*1024*1024:raise Failure('L’allegato deve essere inferiore a 5 MB.',413)
  if extension=='.pdf' and not data.startswith(b'%PDF-'):raise Failure('Il file non è un PDF valido.')
  if extension=='.docx':
   import zipfile
   try:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
     if 'word/document.xml' not in z.namelist() or any('vbaproject' in x.lower() for x in z.namelist()):raise ValueError()
     if sum(x.file_size for x in z.infolist())>40*1024*1024:raise ValueError()
   except Exception:raise Failure('Il file Word non è valido o contiene contenuti non consentiti.')
  if extension=='.txt':
   try:data.decode('utf-8')
   except UnicodeDecodeError:raise Failure('Salva il file di testo in formato UTF-8.')
  return name,{'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.txt':'text/plain'}[extension],data

def date(timestamp):return datetime.datetime.fromtimestamp(timestamp,datetime.timezone.utc).strftime('%d/%m/%Y · %H:%M UTC')
def field(name,label,kind='text',autocomplete='',extra='',required=True,placeholder=''):
 return f'<label>{esc(label)}<input name="{name}" type="{kind}" {"required" if required else ""} autocomplete="{autocomplete or "off"}" placeholder="{esc(placeholder)}" {extra}></label>'
def badge(p):return '<span class="badge '+p['status']+'">'+STATUS[p['status']]+(' · Revisione n. '+str(p['revision']) if p['status']=='revised' else '')+'</span>'
def file_link(f):return f'<a class="file" href="/file/{f["id"]}"><span aria-hidden="true">↓</span> {esc(f["name"])} <span class="fine">Scarica</span></a>'
def project_card(p,admin=False):
 return f'<a class="project-card" href="/lavori/{p["id"]}"><div class="project-symbol" aria-hidden="true">F/</div><div class="project-info"><span class="eyebrow">{esc(p["ateneo"])}</span><h2>{esc(p["title"])}</h2><p>{esc((p["name"]+" "+p["surname"]+" · ") if admin else "")}{esc(p["subject"])}</p></div><div class="project-meta">{badge(p)}<span class="fine">Aggiornato {date(p["updated"])}</span></div><span class="card-arrow" aria-hidden="true">↗</span></a>'

def landing():
 return '''<section class="hero"><div class="hero-copy"><div class="free-pill"><span>REGALO DI BENVENUTO</span> 1 paragrafo gratuito</div><h1>La tua tesi.<br>Finalmente,<br><em>prende forma.</em></h1><p>Non devi affidarti a parole o promesse. Inviaci i dati essenziali della tua tesi: prepariamo gratuitamente un primo paragrafo dimostrativo sul tuo progetto. Lo valuti e soltanto dopo decidi se continuare.</p><div class="hero-actions"><a class="button conversion" href="/registrati">Ottieni il paragrafo gratuito <span>↗</span></a><a class="underlined" href="/anteprima">Guarda l’area personale</a></div><div class="trust-line"><span>✓ Nessun pagamento</span><span>✓ Nessun abbonamento</span><span>✓ Decidi dopo la prova</span></div></div><div class="hero-visual"><div class="orbit-label">IL TUO PROGETTO.<br>UNA PROVA CONCRETA.</div><div class="paper-stack"><div class="document hero-doc"><div class="doc-university">IL TUO PROSSIMO TRAGUARDO</div><div class="doc-rule"></div><span class="doc-kicker">PARAGRAFO DIMOSTRATIVO</span><h2>Un primo testo<br>costruito sulla<br>tua tesi.</h2><div class="paper-lines"><i></i><i></i><i></i></div><div class="doc-bottom">FormaTesi <span>PROVA GRATUITA</span></div></div></div><div class="floating-note"><span class="note-icon">✓</span><div><strong>Gratis, davvero.</strong><span>Prima valuti. Poi scegli.</span></div></div><span class="visual-caption">Una prova per studente, previa verifica.</span></div></section><section class="consulting-strip"><div><strong>Hai un dubbio prima di iniziare?</strong><span>La consulenza iniziale è gratuita.</span></div><a href="https://wa.me/393505815735?text=Ciao%20FormaTesi%2C%20vorrei%20una%20consulenza%20gratuita%20per%20la%20mia%20tesi.">Scrivi ora su WhatsApp ↗</a></section><section class="university-section"><div class="section-heading compact-heading"><span class="eyebrow">CONOSCIAMO IL TUO PERCORSO</span><h2>Partiamo dal tuo ateneo.</h2><p>Seleziona l’università nella richiesta: il progetto sarà organizzato in base alle informazioni che ci fornisci.</p></div><div class="university-logos"><div class="uni-logo"><img src="https://www.centrostudibn.it/wp-content/uploads/2022/09/ecampus.png" alt="Università eCampus" loading="lazy"></div><div class="uni-logo"><img src="https://www.uniares.com/wp-content/uploads/2024/04/Pegaso-con-sfondo-white.jpg" alt="Università Telematica Pegaso" loading="lazy"></div><div class="uni-logo"><img src="https://www.studentitelematici.cloud/wp-content/uploads/2022/04/fb1e9709-c8c5-404a-9596-04c17b1ee230.png" alt="Universitas Mercatorum" loading="lazy"></div><div class="uni-logo"><img src="https://mediakey.it/wp-content/uploads/2025/06/uni-san-raffaele-logo.jpg" alt="Università San Raffaele Roma" loading="lazy"></div></div><p class="trademark-note">I marchi appartengono ai rispettivi titolari. FormaTesi è un servizio indipendente e non è affiliato agli atenei.</p></section><section id="come-funziona" class="section"><div class="section-heading"><span class="eyebrow">COME FUNZIONA</span><h2>Prima vedi come lavoriamo.<br>Poi scegli.</h2><p>Ci bastano le informazioni essenziali per preparare la tua prova personalizzata.</p></div><div class="steps"><article><span class="step-number">01</span><h3>Raccontaci la tua tesi.</h3><p>Indica ateneo, facoltà, materia e titolo. Aggiungi l’indice oppure il paragrafo da cui partire.</p></article><article class="featured-step"><span class="step-number">02</span><span class="gift-tag">TE LO REGALIAMO</span><h3>Ricevi un paragrafo gratuito.</h3><p>Il lavoro arriva nella tua area personale. È una prova concreta sul tuo progetto, non un esempio generico.</p></article><article><span class="step-number">03</span><h3>Decidi senza pressioni.</h3><p>Se il metodo ti convince, chiedi una proposta personalizzata. Prima di accettare sai che cosa comprende.</p></article></div><div class="center-cta"><a class="button conversion" href="/registrati">Richiedi la tua prova gratuita ↗</a><span>Richiede pochi minuti · una prova per studente</span></div></section><section class="feature-section"><div><span class="eyebrow">IL TUO SPAZIO, SEMPRE IN ORDINE</span><h2>Meno messaggi da cercare.<br>Più chiarezza sul lavoro.</h2><p>Ogni consegna resta nel tuo account. Le richieste di modifica sono collegate al lavoro e ogni revisione ha il proprio numero.</p><a class="button light" href="/anteprima">Esplora un’area di esempio ↗</a></div><div class="workflow-preview"><div class="preview-top"><span>Il percorso del tuo lavoro</span><span>↗</span></div><div class="workflow-row"><span class="workflow-index">01</span><div><strong>In attesa</strong><p>La richiesta è stata inviata.</p></div></div><div class="workflow-row"><span class="workflow-index">02</span><div><strong>Consegnato</strong><p>Il primo lavoro è disponibile.</p></div></div><div class="workflow-row"><span class="workflow-index">03</span><div><strong>Da revisionare</strong><p>Le tue osservazioni sono raccolte qui.</p></div></div><div class="workflow-row highlight"><span class="workflow-index">✓</span><div><strong>Revisionato <span class="mini-badge">n. 1</span></strong><p>Una nuova versione, senza perdere la precedente.</p></div></div></div></section><section class="section support"><div class="section-heading"><span class="eyebrow">DA DOVE PARTIAMO?</span><h2>Il supporto giusto<br>per il tuo momento.</h2></div><div class="support-grid"><article><span>↗</span><h3>Hai un’idea da sviluppare.</h3><p>Mettiamo a fuoco la struttura del lavoro e il percorso di ricerca.</p></article><article><span>¶</span><h3>Hai un testo da migliorare.</h3><p>Revisione del contenuto, attenzione alle fonti e alle indicazioni ricevute.</p></article><article><span>≡</span><h3>Vuoi dare ordine al documento.</h3><p>Un aiuto con l’impaginazione e la coerenza delle citazioni.</p></article></div></section><section id="domande" class="section faq"><div><span class="eyebrow">PRIMA DI COMINCIARE</span><h2>Facciamo chiarezza.</h2><p>Vuoi parlarne prima?<br><a href="https://wa.me/393505815735?text=Ciao%20FormaTesi%2C%20vorrei%20una%20consulenza%20gratuita%20per%20la%20mia%20tesi.">Consulenza gratuita su WhatsApp ↗</a></p></div><div><details><summary>Che cosa ricevo gratuitamente?</summary><p>Un primo paragrafo dimostrativo preparato a partire dai dati del tuo progetto. La richiesta viene valutata e la prova è disponibile una sola volta per studente.</p></details><details><summary>La prova mi obbliga ad acquistare?</summary><p>No. Non inserisci dati di pagamento e non attivi abbonamenti. Valuti il lavoro e decidi tu se richiedere una proposta.</p></details><details><summary>Che cosa serve per iniziare?</summary><p>Ateneo, facoltà o corso di laurea, materia e titolo della tesi. Aggiungi l’indice, anche come file, oppure il titolo del paragrafo.</p></details><details><summary>Posso chiedere modifiche?</summary><p>Dopo una consegna puoi inviare una richiesta di revisione dalla tua area. Le revisioni incluse negli eventuali lavori successivi vengono indicate nella proposta.</p></details><details><summary>Siete collegati alla mia università?</summary><p>No. FormaTesi è un servizio indipendente di supporto accademico. Lo studente rimane responsabile del proprio elaborato e delle regole dell’ateneo.</p></details></div></section><section class="final-cta"><span class="eyebrow">NON DEVI ANCORA SCEGLIERCI. DEVI SOLO PROVARCI.</span><h2>Il primo paragrafo<br>lo offriamo noi.</h2><p>Nessun pagamento. Nessun abbonamento. Una prova concreta sul tuo progetto.</p><a class="button conversion" href="/registrati">Ottieni il paragrafo gratuito ↗</a><div class="direct-contacts"><a href="https://m.me/61593221212687">Preferisci Messenger?</a><a href="https://wa.me/393505815735?text=Ciao%20FormaTesi%2C%20vorrei%20una%20consulenza%20gratuita%20per%20la%20mia%20tesi.">Scrivici su WhatsApp</a></div></section>'''

def demo():
 return '''<section class="workspace"><div class="notice">Anteprima dimostrativa · Il progetto qui sotto è un esempio, non appartiene a uno studente reale.</div><div class="page-heading"><div><span class="eyebrow">IL TUO SPAZIO FORMATESI</span><h1>Tutto il lavoro.<br>Un unico posto.</h1><p>Ecco come ritroverai le consegne e le revisioni del tuo progetto.</p></div><a class="button" href="/registrati">Crea il tuo account ↗</a></div><div class="detail-layout"><div><section class="panel"><span class="eyebrow">ESEMPIO · SCIENZE DELL’EDUCAZIONE</span><h2>Il gioco come esperienza di apprendimento.</h2><span class="badge revised">Revisionato · Revisione n. 1</span><div class="timeline"><article class="timeline-item"><span class="eyebrow">PRIMA CONSEGNA</span><h3>1.1 Il valore educativo del gioco</h3><p>Il lavoro iniziale viene pubblicato qui. Quando il servizio è attivo, puoi aprire il testo e scaricare il documento dalla stessa scheda.</p><div class="file">Documento della prima consegna <span class="fine">Esempio</span></div></article><article class="timeline-item"><span class="eyebrow">RICHIESTA DI REVISIONE</span><h3>Le osservazioni dello studente</h3><p>“Vorrei approfondire il collegamento con le attività nella scuola dell’infanzia.”</p></article><article class="timeline-item"><span class="eyebrow">REVISIONE N. 1</span><h3>La versione aggiornata</h3><p>La revisione compare dopo la consegna, con le modifiche richieste. La versione iniziale rimane consultabile.</p></article></div></section></div><aside><section class="panel"><h3>La scheda del progetto</h3><dl><dt>Ateneo</dt><dd>eCampus · esempio</dd><dt>Facoltà / corso</dt><dd>Scienze dell’educazione · L-19</dd><dt>Materia</dt><dd>Pedagogia generale</dd><dt>Paragrafo</dt><dd>1.1 Il valore educativo del gioco</dd></dl></section><section class="aside-note"><h3>Un passaggio alla volta.</h3><p>Il numero di revisione aumenta quando ricevi una nuova versione corretta, non quando invii una richiesta.</p></section></aside></div></section>'''

def legal(site,path):
 if not site.ready:return '<section class="narrow panel"><span class="eyebrow">ANTEPRIMA FORMATESI</span><h1>'+('Privacy' if path=='/privacy' else 'Condizioni del servizio')+'</h1><p>L’area di registrazione non è ancora attiva e questa anteprima non raccoglie richieste o documenti degli studenti. Il sito non utilizza strumenti pubblicitari o di analisi del traffico. Il fornitore di hosting può trattare i dati tecnici necessari all’erogazione del sito.</p><p>Le informazioni complete sul titolare e sulle condizioni saranno pubblicate prima dell’apertura delle registrazioni.</p><a href="'+FB+'">Contatta FormaTesi</a></section>'
 business=esc(site.cfg.get('BUSINESS_NAME'));contact=esc(site.cfg.get('PRIVACY_CONTACT'))
 if path=='/privacy':text=f'''<h1>Informativa privacy</h1><p>Titolare del trattamento: {business}. Contatto: {contact}.</p><h2>Dati e finalità</h2><p>Trattiamo nome, cognome, email e matricola per gestire l’account. I dati del percorso universitario, i testi e gli allegati servono a valutare le richieste e fornire il supporto richiesto. La base giuridica è l’esecuzione del servizio e delle misure precontrattuali richieste.</p><p>I controlli su richieste ripetute e i registri di sicurezza tutelano il servizio da abusi, sulla base del legittimo interesse. Le segnalazioni di somiglianza sono valutate da una persona e non determinano automaticamente l’esclusione.</p><h2>Accesso e fornitori</h2><p>I materiali sono accessibili allo studente e al gestore autorizzato. Sono coinvolti i fornitori di hosting, database e invio email necessari al servizio. Fornitori e garanzie applicabili: {esc(site.cfg.get('PRIVACY_PROVIDERS'))}.</p><h2>Conservazione e diritti</h2><p>Criteri di conservazione: {esc(site.cfg.get('RETENTION_POLICY'))}. Puoi chiedere accesso, rettifica, cancellazione o limitazione, e opporti ai trattamenti fondati sul legittimo interesse, scrivendo al contatto indicato. Puoi proporre reclamo al Garante per la protezione dei dati personali.</p><h2>Cookie</h2><p>Utilizziamo soltanto il cookie tecnico di sessione necessario all’accesso e alla sicurezza dei moduli. Non usiamo cookie pubblicitari né strumenti di profilazione.</p>'''
 else:text=f'''<h1>Condizioni del servizio</h1><p>Il servizio FormaTesi è gestito da {business}. Contatto: {contact}.</p><h2>Supporto accademico</h2><p>Il servizio offre assistenza alla ricerca, revisione e organizzazione dell’elaborato. Lo studente deve rispettare le regole del proprio ateneo e rimane responsabile dell’elaborato presentato. Non sono garantiti voti, approvazioni o risultati di software di rilevazione.</p><h2>Prova e richieste</h2><p>È disponibile una sola prova per studente, previa verifica dei materiali e della disponibilità. Creare più account non dà diritto a ulteriori prove. I tempi vengono concordati in base alla richiesta.</p><h2>Proposte e revisioni</h2><p>Le proposte indicano ambito del lavoro, importo e revisioni incluse. Il pulsante “Conferma interesse” richiede un contatto per procedere e non conclude un acquisto né effettua pagamenti. Eventuali incarichi a pagamento sono definiti separatamente, con le informazioni contrattuali applicabili prima del pagamento.</p><h2>Materiali caricati</h2><p>Carica soltanto documenti che hai diritto di condividere. Evita informazioni personali non necessarie. Le revisioni vengono archiviate nella scheda del lavoro.</p>'''
 return '<section class="narrow panel legal">'+text+'</section>'

app=Site()
if __name__=='__main__':
 from wsgiref.simple_server import make_server
 make_server('127.0.0.1',8000,app).serve_forever()

