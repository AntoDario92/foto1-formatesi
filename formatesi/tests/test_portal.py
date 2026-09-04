import sys, tempfile, unittest, io, urllib.parse, re, json, base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Site, digest
class Client:
 def __init__(self,app):self.app=app;self.cookie='';self.csrf=''
 def call(self,path='/',data=None,method=None):
  path,_,query=path.partition('?');raw=urllib.parse.urlencode(data or {}).encode();captured={}
  env={'PATH_INFO':path,'QUERY_STRING':query,'REQUEST_METHOD':method or ('POST' if data is not None else 'GET'),'wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'HTTP_COOKIE':self.cookie,'REMOTE_ADDR':'test-'+str(id(self))}
  def start(status,headers):captured.update(status=int(status.split()[0]),headers=dict(headers))
  body=b''.join(self.app(env,start));self.body=body.decode(errors='replace');self.headers=captured['headers'];self.status=captured['status']
  if 'Set-Cookie' in self.headers:self.cookie=self.headers['Set-Cookie'].split(';')[0]
  match=re.search('name="csrf" value="([^"]+)"',self.body)
  if match:self.csrf=match.group(1)
  return self.status
 def post(self,path,**data):return self.call(path,dict(csrf=self.csrf,**data))
class PortalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=Site({'TESTING':'1','TEST_DB':self.tmp.name+'/db.sqlite','ADMIN_EMAIL':'owner@example.com'});self.student=self.register('a@example.com');self.other=self.register('b@example.com');self.admin=self.register('owner@example.com')
 def tearDown(self):self.tmp.cleanup()
 def register(self,email):
  c=Client(self.app);c.call('/registrati');self.assertEqual(c.post('/registrati',name='Anna',surname='Rossi',email=email,matricola=email.split('@')[0],password='Una password lunga 123',terms='yes'),200)
  m=self.app.query('SELECT * FROM outbox WHERE email=? ORDER BY created DESC',(email,),True);token=m['body'].split('token=')[1]
  self.assertEqual(c.call('/verifica?token='+token),200)
  c.call('/login');self.assertEqual(c.post('/login',email=email,password='Una password lunga 123'),303);c.call('/area');return c
 def new(self):
  self.student.call('/nuovo');self.assertEqual(self.student.post('/nuovo',ateneo='eCampus',faculty='Scienze L-19',subject='Pedagogia',title='Il gioco e apprendimento',paragraph='Il ruolo educativo',file_name='indice.txt',file_data=base64.b64encode(b'1. Il gioco').decode()),303);return self.student.headers['Location']
 def test_workflow_isolation_and_revisions(self):
  url=self.new();self.assertEqual(self.student.call(url),200)
  self.assertEqual(self.other.call(url),404)
  file=self.app.query('SELECT id FROM files',one=True)['id'];self.assertEqual(self.other.call('/file/'+file),404);self.assertEqual(self.student.call('/file/'+file),200)
  self.other.call('/area');self.assertEqual(self.other.post(url+'/consegna',body='hack',version='0',status='waiting'),404)
  self.student.call(url);self.assertEqual(self.student.post(url+'/consegna',body='hack',version='0',status='waiting'),403)
  self.admin.call(url);self.assertEqual(self.admin.post(url+'/consegna',body='Testo <script>alert(1)</script>',version='0',status='waiting',file_name='consegna.txt',file_data=base64.b64encode(b'Prima consegna').decode()),303)
  self.assertEqual(self.admin.post(url+'/consegna',body='Doppio invio',version='0',status='waiting'),409)
  self.student.call(url);self.assertIn('&lt;script&gt;',self.student.body)
  self.assertEqual(self.student.post(url+'/revisione',body='Approfondire il tema',version='0',status='delivered'),303)
  self.admin.call(url);self.assertEqual(self.admin.post(url+'/consegna',body='Revisione 1',version='0',status='revision_requested'),303)
  self.student.call(url);self.assertIn('Revisione n. 1',self.student.body)
  self.assertEqual(self.student.post(url+'/revisione',body='Un ultimo passaggio',version='1',status='revised'),303)
  self.admin.call(url);self.assertEqual(self.admin.post(url+'/consegna',body='Revisione 2',version='1',status='revision_requested'),303)
  self.student.call(url);self.assertIn('Revisione n. 2',self.student.body)
  self.assertEqual(len(self.app.query("SELECT * FROM events WHERE kind='delivery'")),3)
 def test_trial_limit_and_quote(self):
  url=self.new();self.student.call('/nuovo');self.assertEqual(self.student.post('/nuovo',ateneo='eCampus',faculty='F',subject='S',title='Altro',paragraph='P'),200);self.assertIn('già richiesto la prova gratuita',self.student.body)
  self.assertEqual(len(self.app.query('SELECT * FROM projects')),1)
  self.admin.call(url);self.assertEqual(self.admin.post(url+'/preventivo',amount='125,50',description='Revisione di un capitolo. Due revisioni incluse. 7 giorni.'),303)
  q=self.app.query('SELECT * FROM quotes',one=True);self.assertEqual(q['cents'],12550)
  self.student.call(url);self.assertEqual(self.student.post(url+'/accetta',quote=q['id'],confirm='yes'),303)
  self.assertEqual(self.student.post(url+'/accetta',quote=q['id'],confirm='yes'),409)
 def test_csrf_and_reset(self):
  self.assertEqual(self.student.call('/logout',{'csrf':'wrong'}),403)
  c=Client(self.app);c.call('/recupera');c.post('/recupera',email='a@example.com')
  mails=self.app.query("SELECT * FROM outbox WHERE subject='Reimposta la password FormaTesi'");token=mails[-1]['body'].split('token=')[1]
  c.call('/reimposta?token='+token);self.assertEqual(c.post('/reimposta',token=token,password='Nuova password lunga 456'),200)
  self.assertEqual(self.student.call('/area'),303)
  self.assertEqual(c.post('/reimposta',token=token,password='Nuova password lunga 789'),200);self.assertIn('non valido',c.body)
 def test_public_gate_and_file_rejection(self):
  closed=Client(Site({}));self.assertEqual(closed.call('/'),200);self.assertEqual(closed.call('/registrati'),503);self.assertEqual(closed.call('/anteprima'),200)
  self.student.call('/nuovo');self.assertEqual(self.student.post('/nuovo',ateneo='eCampus',faculty='F',subject='S',title='T',paragraph='P',file_name='evil.pdf',file_data=base64.b64encode(b'not PDF').decode()),200);self.assertIn('non è un PDF valido',self.student.body)
  self.assertEqual(len(self.app.query('SELECT * FROM projects')),0)
 def test_missing_information_and_admin_role(self):
  self.assertEqual(self.app.query('SELECT role FROM users WHERE email=?',('a@example.com',),True)['role'],'student')
  self.assertEqual(self.app.query('SELECT role FROM users WHERE email=?',('owner@example.com',),True)['role'],'admin')
  self.student.call('/nuovo');self.student.post('/nuovo',ateneo='eCampus',faculty='F',subject='S',title='T');self.assertIn('titolo del paragrafo',self.student.body);self.assertEqual(len(self.app.query('SELECT * FROM projects')),0)
 def test_facebook_ticket_registration(self):
  self.app.cfg.update(FACEBOOK_APP_ID='123',FACEBOOK_APP_SECRET='test-secret')
  payload={'id':'fb-44','name':'Lucia','surname':'Verdi','email':'lucia@example.com','exp':9999999999}
  encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
  import hashlib,hmac
  ticket=encoded+'.'+hmac.new(b'test-secret',encoded.encode(),hashlib.sha256).hexdigest()
  c=Client(self.app);self.assertEqual(c.call('/registrati-facebook?ticket='+urllib.parse.quote(ticket)),200)
  self.assertIn('Lucia Verdi',c.body);self.assertNotIn('name="password"',c.body)
  self.assertEqual(c.post('/registrati-facebook',ticket=ticket,matricola='M-998',terms='yes'),303)
  user=self.app.query('SELECT * FROM users WHERE email=?',('lucia@example.com',),True)
  self.assertEqual(user['facebook_id'],'fb-44');self.assertEqual(user['matricola'],'M-998');self.assertEqual(user['verified'],1)
  c.call('/area');self.assertIn('Ciao, Lucia',c.body)
  bad=Client(self.app);bad.call('/registrati-facebook?ticket='+urllib.parse.quote(ticket+'x'));self.assertIn('scaduta',bad.body)
if __name__=='__main__':unittest.main()
