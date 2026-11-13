import os, secrets, html, time
from pathlib import Path
from io import BytesIO
import httpx
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
from formatter import format_docx, inspect_risks, pasted_text_to_docx

BASE=Path(__file__).resolve().parent
URL=os.environ.get('SUPABASE_URL','').rstrip('/')
KEY=os.environ.get('SUPABASE_PUBLISHABLE_KEY','')
SECRET=os.environ.get('SESSION_SECRET','')
if not (URL.startswith('https://') and KEY and len(SECRET)>=32):
    raise RuntimeError('Configure SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY e SESSION_SECRET (32+ caracteres)')
app=FastAPI(title='Formatador de Petições - PA Advocacia')
app.add_middleware(SessionMiddleware,secret_key=SECRET,same_site='lax',https_only=os.getenv('COOKIE_SECURE','1')=='1',max_age=3600)
# Tokens de acesso ficam apenas na memória do processo, nunca nos cookies ou HTML.
# Sessões expiram/reiniciam quando o serviço gratuito hiberna.
SESSIONS={}
MAX_SIZE=10*1024*1024
LETTERHEAD=(BASE/'assets/papel_timbrado.docx').read_bytes()

@app.middleware('http')
async def headers(req,call_next):
    response=await call_next(req)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
    return response

def page(req,body):
    csrf=req.session.get('csrf')
    if not csrf:
        csrf=secrets.token_urlsafe(24); req.session['csrf']=csrf
    body=body.replace('CSRF_VALUE',html.escape(csrf))
    return HTMLResponse('''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Formatador PA</title><style>body{font:16px system-ui;margin:0;background:#f2f6f9;color:#16344a}header{background:#103e5a;color:white;padding:20px}main{max-width:750px;margin:30px auto;padding:0 16px}section{background:white;padding:25px;border-radius:12px;box-shadow:0 2px 12px #0001}input,textarea,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0 16px;border:1px solid #b9cbd7;border-radius:7px;font:inherit}button{background:#103e5a;color:white;cursor:pointer}label{font-weight:600}small{color:#536978}.alert{padding:12px;background:#fff1d4;border-radius:8px}a{color:#164f76}</style><header><strong>PAULO TENÓRIO ADVOCACIA · FORMATADOR</strong></header><main>'''+body+'</main></html>')

def check_csrf(req,value):
    if not value or not secrets.compare_digest(value,req.session.get('csrf','')):
        raise HTTPException(403,'Formulário inválido')

async def identity(req):
    sid=req.session.get('sid'); s=SESSIONS.get(sid)
    if not s or s['expires']<time.time(): return None
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r=await client.get(URL+'/auth/v1/user',headers={'apikey':KEY,'Authorization':'Bearer '+s['token']})
        if r.status_code!=200: return None
        return r.json()
    except httpx.HTTPError: return None

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/')
async def home(req:Request):
    user=await identity(req)
    if not user:
        return page(req,'''<section><h2>Entrar</h2><form method="post" action="/login"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>E-mail</label><input type="email" name="email" required autocomplete="username"><label>Senha</label><input type="password" name="password" required autocomplete="current-password"><button>Entrar</button></form><small>Acesso somente para usuários cadastrados no escritório.</small></section>''')
    email=html.escape(user.get('email',''))
    return page(req,f'''<section><h2>Formatar petição</h2><p>Conectado: {email}</p><p class="alert">Versão de homologação: use somente textos fictícios, sem dados pessoais de clientes. Arquivos não são salvos no histórico.</p><form action="/formatar" method="post" enctype="multipart/form-data"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>Opção A — Enviar Word (.docx)</label><input type="file" name="arquivo" accept=".docx"><p>OU</p><label>Opção B — Colar texto</label><textarea name="texto" rows="11" placeholder="Cole aqui a petição..."></textarea><small>Escolha apenas uma opção. Texto colado não preserva negritos, tabelas ou estilos originais.</small><button>Gerar documento Word</button></form><form method="post" action="/sair"><input type="hidden" name="csrf" value="CSRF_VALUE"><button>Sair</button></form></section>''')

@app.post('/login')
async def login(req:Request,email:str=Form(...),password:str=Form(...),csrf:str=Form(...)):
    check_csrf(req,csrf)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r=await client.post(URL+'/auth/v1/token?grant_type=password',headers={'apikey':KEY,'Content-Type':'application/json'},json={'email':email,'password':password})
    except httpx.HTTPError:
        return page(req,'<section><p>Serviço temporariamente indisponível. Tente novamente.</p><a href="/">Voltar</a></section>')
    if r.status_code!=200:
        # Exibe somente um código diagnóstico seguro; nunca imprime senhas, tokens ou chaves.
        try:
            error_data = r.json()
            error_code = str(error_data.get('error_code') or error_data.get('code') or error_data.get('error') or 'unknown')
        except (ValueError, AttributeError):
            error_code = 'invalid_response'
        known = {
            'invalid_credentials': 'O Supabase recusou o e-mail ou a senha. Redefina a senha em Authentication > Users e tente novamente.',
            'email_not_confirmed': 'O Supabase informa que o e-mail ainda não está confirmado.',
            'invalid_api_key': 'A chave pública do Supabase está incorreta ou não pertence a este projeto.',
            'signup_disabled': 'O provedor de autenticação não permitiu esta operação.',
            'user_banned': 'Este usuário está bloqueado no Supabase.',
            'over_request_rate_limit': 'Limite temporário de tentativas atingido. Aguarde antes de tentar novamente.',
        }
        message = known.get(error_code, 'O Supabase recusou a autenticação. Confira o código de diagnóstico abaixo.')
        safe_code = html.escape(error_code[:70])
        return page(req, f'<section><h2>Não foi possível entrar</h2><p>{html.escape(message)}</p><p><small>Diagnóstico: HTTP {r.status_code} / {safe_code}</small></p><a href="/">Voltar</a></section>')
    data=r.json(); sid=secrets.token_urlsafe(32)
    SESSIONS[sid]={'token':data['access_token'],'expires':time.time()+min(int(data.get('expires_in',3600)),3500)}
    req.session.clear();req.session['sid']=sid
    return RedirectResponse('/',status_code=303)

@app.post('/sair')
async def logout(req:Request,csrf:str=Form(...)):
    check_csrf(req,csrf);SESSIONS.pop(req.session.get('sid'),None);req.session.clear()
    return RedirectResponse('/',status_code=303)

@app.post('/formatar')
async def formatar(req:Request,csrf:str=Form(...),texto:str=Form(''),arquivo:UploadFile|None=File(None)):
    check_csrf(req,csrf)
    if not await identity(req): raise HTTPException(401,'Faça login novamente')
    has_file=bool(arquivo and arquivo.filename)
    if has_file==bool(texto.strip()): raise HTTPException(400,'Envie um arquivo OU cole um texto')
    if has_file:
        if not arquivo.filename.lower().endswith('.docx'): raise HTTPException(400,'Envie apenas .docx')
        source=await arquivo.read(MAX_SIZE+1)
        if len(source)>MAX_SIZE: raise HTTPException(413,'Arquivo excede 10 MB')
    else:
        if len(texto)>300000: raise HTTPException(413,'Texto muito longo')
        source=pasted_text_to_docx(texto)
    try:
        risks=inspect_risks(source)
        if risks: raise HTTPException(422,'Documento possui elementos complexos não suportados: '+', '.join(risks[:5]))
        output=format_docx(source,LETTERHEAD)
    except HTTPException: raise
    except Exception: raise HTTPException(422,'Não foi possível formatar este documento. Verifique o arquivo.')
    return StreamingResponse(BytesIO(output),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="peticao_formatada.docx"','Cache-Control':'no-store'})
