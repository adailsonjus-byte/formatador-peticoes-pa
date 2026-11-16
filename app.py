
"""Formatador PA: backend de homologação com operações administrativas server-side.
A chave secreta nunca é enviada ao navegador. Ver README para limites de segurança.
"""
import html, os, secrets, time, uuid, re
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
import httpx
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
from formatter import format_docx, inspect_risks, pasted_text_to_docx

BASE = Path(__file__).resolve().parent
URL = os.getenv('SUPABASE_URL','').rstrip('/')
PUBLIC = os.getenv('SUPABASE_PUBLISHABLE_KEY','')
ADMIN_KEY = os.getenv('SUPABASE_SECRET_KEY','')
SESSION_SECRET = os.getenv('SESSION_SECRET','')
if not (URL.startswith('https://') and PUBLIC and ADMIN_KEY and len(SESSION_SECRET) >= 32):
    raise RuntimeError('Configure SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY e SESSION_SECRET')
app = FastAPI(title='Formatador PA')
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site='lax', https_only=True, max_age=3600)
SESSIONS = {}
MAX_SIZE = 10 * 1024 * 1024
MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
DEFAULT_TEMPLATE = (BASE/'assets/papel_timbrado.docx').read_bytes()

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store','Permissions-Policy':'camera=(), microphone=()','Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
    return response

def esc(value): return html.escape(str(value or ''), quote=True)
def page(req, body):
    csrf = req.session.get('csrf')
    if not csrf:
        csrf = secrets.token_urlsafe(24)
        req.session['csrf'] = csrf
    body = body.replace('CSRF_VALUE', esc(csrf))
    return HTMLResponse('''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Formatador PA</title><style>body{font:16px system-ui;margin:0;background:#f3f6f9;color:#17354b}header{background:#123c58;color:#fff;padding:19px}main{max-width:900px;margin:25px auto;padding:0 15px}section{background:#fff;padding:23px;margin:0 0 18px;border-radius:11px;box-shadow:0 2px 9px #0001}input,textarea,button,select{box-sizing:border-box;width:100%;padding:11px;margin:7px 0 14px;border:1px solid #b8c9d4;border-radius:6px;font:inherit}button{background:#164c6b;color:white;cursor:pointer}button:disabled{opacity:.5}label{font-weight:600}small{color:#526879}.warn{background:#fff3d9;padding:12px;border-radius:7px}a{color:#17587b}.inline{display:inline}.inline button{width:auto;padding:5px 9px}.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1;min-width:170px}li{margin:9px 0}code{overflow-wrap:anywhere}</style></head><body><header><strong>PAULO TENÓRIO ADVOCACIA · FORMATADOR DE PETIÇÕES</strong></header><main>'''+body+'</main></body></html>')

def csrf_check(req, token):
    if not token or not secrets.compare_digest(token, req.session.get('csrf','')):
        raise HTTPException(403, 'Formulário expirado. Atualize a página.')

def session_token(req):
    s = SESSIONS.get(req.session.get('sid'))
    if not s or s['expires'] < time.time(): raise HTTPException(401, 'Sessão expirada')
    return s['token']

async def request_api(method, path, *, admin=False, token=None, expected=(200,), **kwargs):
    headers = {'apikey': ADMIN_KEY if admin else PUBLIC}
    # Chaves sb_secret_* não são JWT; use somente apikey para operações administrativas.
    if token and not admin: headers['Authorization'] = 'Bearer '+token
    headers.update(kwargs.pop('headers', {}))
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            response = await client.request(method, URL+path, headers=headers, **kwargs)
    except httpx.HTTPError: raise HTTPException(503, 'Serviço temporariamente indisponível')
    if response.status_code not in expected:
        # Não incluir corpo da resposta do provedor: pode conter dados pessoais.
        raise HTTPException(502, f'Operação não concluída (serviço: {response.status_code})')
    return response

async def current(req):
    token = session_token(req)
    r = await request_api('GET','/auth/v1/user',token=token)
    user = r.json()
    profile = await request_api('GET','/rest/v1/profiles',admin=True,params={'id':'eq.'+user['id'],'select':'id,role,full_name,active,must_change_password'})
    rows = profile.json()
    if not rows or not rows[0]['active']:
        SESSIONS.pop(req.session.get('sid'),None)
        raise HTTPException(403,'Conta desativada ou sem perfil autorizado')
    return user, rows[0]

async def require_admin(req):
    user, profile = await current(req)
    if profile['role'] != 'admin': raise HTTPException(403,'Acesso exclusivo de administradores')
    return user

async def documents_for(uid):
    r = await request_api('GET','/rest/v1/documents',admin=True,params={'user_id':'eq.'+uid,'select':'id,output_filename,created_at','order':'created_at.desc','limit':'50'})
    return r.json()

async def template_bytes():
    r = await request_api('GET','/rest/v1/app_settings',admin=True,params={'id':'eq.1','select':'template_path'})
    rows=r.json()
    if not rows or not rows[0].get('template_path'): return DEFAULT_TEMPLATE
    path=rows[0]['template_path']
    if not re.fullmatch(r'templates/[a-f0-9-]{36}\.docx',path): raise HTTPException(500,'Modelo inválido')
    r=await request_api('GET','/storage/v1/object/peticoes/'+path,admin=True)
    return r.content

def file_response(content, name='peticao_formatada.docx'):
    return StreamingResponse(BytesIO(content),media_type=MIME,headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'no-store'})

@app.get('/health')
def health(): return {'status':'ok'}

PUBLIC_HITS = {}

def public_limit(req):
    # Limite por IP da conexão, em memória; reinicia com o serviço.
    ip = req.client.host if req.client else 'unknown'
    now = time.monotonic()
    if len(PUBLIC_HITS) > 5000:
        PUBLIC_HITS.clear()
    hits = [t for t in PUBLIC_HITS.get(ip, []) if now-t < 60]
    if len(hits) >= 8:
        raise HTTPException(429, 'Muitas solicitações. Aguarde um minuto e tente novamente.')
    hits.append(now)
    PUBLIC_HITS[ip] = hits

PUBLIC_FORM = '''<section><h2>Formatar petição</h2><p>Envie um arquivo Word (.docx) ou cole o texto. Não é necessário criar conta.</p><p class="warn">Serviço de acesso público. Use inicialmente apenas documentos fictícios: antes do uso com dados de clientes, valide segurança, privacidade e capacidade da hospedagem.</p><form method="post" action="/formatar" enctype="multipart/form-data"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>A — Enviar Word (.docx)</label><input type="file" name="arquivo" accept=".docx"><p>OU</p><label>B — Colar texto</label><textarea name="texto" rows="12"></textarea><small>Escolha apenas uma opção. Texto simples não preserva estilos originais. O resultado é entregue por download, sem histórico público.</small><button>Gerar documento Word</button></form><small>Administrador: <a href="/admin">entrar para alterar papel timbrado</a>.</small></section>'''

@app.get('/')
async def home(req:Request):
    return page(req, PUBLIC_FORM)

@app.get('/admin')
async def admin_home(req:Request):
    try:
        user = await require_admin(req)
    except HTTPException:
        return page(req, '''<section><h2>Acesso do administrador</h2><form method="post" action="/login"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>E-mail</label><input name="email" type="email" required autocomplete="username"><label>Senha</label><input name="password" type="password" required autocomplete="current-password"><button>Entrar</button></form><a href="/recuperar">Esqueci minha senha</a></section>''')
    return page(req, '''<section><h2>Administração do papel timbrado</h2><p>Administrador autenticado.</p><form method="post" action="/admin/timbrado" enctype="multipart/form-data"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>Novo papel timbrado (.docx)</label><input type="file" name="arquivo" accept=".docx" required><button>Substituir papel timbrado</button></form><small>Use somente o modelo do escritório, com cabeçalho, marca-d'água e rodapé, sem dados de clientes.</small><form method="post" action="/sair"><input type="hidden" name="csrf" value="CSRF_VALUE"><button>Sair da administração</button></form></section>''')

@app.post('/login')
async def login(req:Request,csrf:str=Form(...),email:str=Form(...),password:str=Form(...)):
    csrf_check(req,csrf)
    r=await request_api('POST','/auth/v1/token?grant_type=password',json={'email':email.strip(),'password':password},expected=(200,400,401,403))
    if r.status_code!=200: return page(req,'<section><h2>Não foi possível entrar</h2><p>Confira o e-mail e a senha ou utilize a recuperação de acesso.</p><a href="/recuperar">Recuperar senha</a></section>')
    data=r.json(); sid=secrets.token_urlsafe(32)
    SESSIONS[sid]={'token':data['access_token'],'expires':time.time()+min(int(data.get('expires_in',3600)),3500)}
    req.session.clear();req.session['sid']=sid
    try: await require_admin(req)
    except HTTPException:
        SESSIONS.pop(sid,None);req.session.clear()
        return page(req,'<section><h2>Conta sem acesso</h2><p>Solicite ao administrador a ativação do perfil.</p></section>')
    return RedirectResponse('/admin',status_code=303)

@app.post('/sair')
async def logout(req:Request,csrf:str=Form(...)):
    csrf_check(req,csrf); SESSIONS.pop(req.session.get('sid'),None);req.session.clear()
    return RedirectResponse('/admin',status_code=303)

@app.post('/minha-senha')
async def own_password(req:Request,csrf:str=Form(...),password:str=Form(...)):
    csrf_check(req,csrf)
    user,_=await current(req)
    if len(password)<12: raise HTTPException(400,'Use no mínimo 12 caracteres')
    await request_api('PUT','/auth/v1/user',token=session_token(req),json={'password':password})
    await request_api('PATCH','/rest/v1/profiles',admin=True,params={'id':'eq.'+user['id']},json={'must_change_password':False})
    SESSIONS.pop(req.session.get('sid'),None);req.session.clear()
    return page(req,'<section><h2>Senha atualizada</h2><p>Entre novamente com sua nova senha.</p><a href="/admin">Entrar</a></section>')

@app.get('/recuperar')
async def recover_form(req:Request):
    return page(req,'<section><h2>Recuperar senha</h2><form method="post" action="/recuperar"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>E-mail</label><input type="email" name="email" required><button>Enviar e-mail de recuperação</button></form></section>')

@app.post('/recuperar')
async def recover(req:Request,csrf:str=Form(...),email:str=Form(...)):
    csrf_check(req,csrf)
    # Não revelar se o e-mail existe.
    origin=str(req.base_url).rstrip('/')
    await request_api('POST','/auth/v1/recover',params={'redirect_to':origin+'/redefinir'},json={'email':email.strip()},expected=(200,400,422,429))
    return page(req,'<section><p>Se o e-mail estiver cadastrado, receberá instruções de recuperação. Confira também o spam.</p><a href="/">Voltar</a></section>')

@app.get('/redefinir')
async def reset_form(req:Request):
    return page(req,'<section><h2>Definir nova senha</h2><p>Abra esta página pelo link de recuperação enviado pelo Supabase.</p><form method="post" action="/redefinir" id="reset-form"><input type="hidden" name="csrf" value="CSRF_VALUE"><input type="hidden" name="access_token" id="reset-token"><label>Nova senha (mínimo 12 caracteres)</label><input name="password" type="password" minlength="12" required><button id="reset-submit" disabled>Salvar nova senha</button></form><p id="reset-help">Verificando link de recuperação...</p><script src="/reset.js" defer></script></section>')

@app.get('/reset.js')
def reset_script():
    # Supabase padrão retorna access_token no fragmento URL (#...), nunca ao servidor.
    return Response('''(()=>{const p=new URLSearchParams(location.hash.slice(1));const token=p.get('access_token');const type=p.get('type');const field=document.getElementById('reset-token');const btn=document.getElementById('reset-submit');const help=document.getElementById('reset-help');if(token&&type==='recovery'){field.value=token;btn.disabled=false;help.textContent='Link recebido. Defina sua nova senha.';history.replaceState(null,'',location.pathname);}else{help.textContent='Link inválido ou expirado. Solicite outro e-mail de recuperação.';}})();''',media_type='application/javascript')

@app.post('/redefinir')
async def reset_password(req:Request,csrf:str=Form(...),access_token:str=Form(...),password:str=Form(...)):
    csrf_check(req,csrf)
    if len(password)<12 or len(access_token)>4096: raise HTTPException(400,'Dados inválidos')
    await request_api('PUT','/auth/v1/user',token=access_token,json={'password':password},expected=(200,))
    return page(req,'<section><h2>Senha alterada</h2><p>Entre com sua nova senha.</p><a href="/">Entrar</a></section>')

@app.post('/formatar')
async def formatar(req:Request,csrf:str=Form(...),texto:str=Form(''),arquivo:UploadFile|None=File(None)):
    csrf_check(req,csrf)
    public_limit(req)
    has_file=bool(arquivo and arquivo.filename)
    if has_file == bool(texto.strip()): raise HTTPException(400,'Envie um arquivo OU cole um texto')
    if has_file:
        if not arquivo.filename.lower().endswith('.docx'): raise HTTPException(400,'Envie um .docx')
        source=await arquivo.read(MAX_SIZE+1)
        if len(source)>MAX_SIZE: raise HTTPException(413,'Limite de 10 MB')
    else:
        if len(texto)>300000: raise HTTPException(413,'Texto muito longo')
        source=pasted_text_to_docx(texto)
    try:
        risks=inspect_risks(source)
        if risks: raise HTTPException(422,'Documento complexo não suportado: '+', '.join(risks[:5]))
        output=format_docx(source,await template_bytes())
    except HTTPException: raise
    except Exception: raise HTTPException(422,'Documento inválido ou incompatível')
    # Não armazenar arquivo, texto ou documento resultante no Supabase.
    return file_response(output)

@app.post('/admin/timbrado')
async def update_template(req:Request,csrf:str=Form(...),arquivo:UploadFile=File(...)):
    csrf_check(req,csrf); await require_admin(req)
    if not arquivo.filename.lower().endswith('.docx'): raise HTTPException(400,'Modelo deve ser .docx')
    content=await arquivo.read(MAX_SIZE+1)
    if len(content)>MAX_SIZE: raise HTTPException(413,'Limite de 10 MB')
    try:
        from docx import Document
        Document(BytesIO(content))
    except Exception: raise HTTPException(422,'Arquivo Word inválido')
    path='templates/'+str(uuid.uuid4())+'.docx'
    await request_api('POST','/storage/v1/object/peticoes/'+path,admin=True,data=content,headers={'Content-Type':MIME,'x-upsert':'false'},expected=(200,201))
    await request_api('POST','/rest/v1/app_settings',admin=True,json={'id':1,'template_path':path},headers={'Prefer':'resolution=merge-duplicates,return=minimal'},expected=(201,))
    return RedirectResponse('/admin',status_code=303)
