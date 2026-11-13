import os, secrets, html, time, uuid
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

def auth_headers(req):
    sid=req.session.get('sid'); entry=SESSIONS.get(sid)
    if not entry or entry['expires'] < time.time(): raise HTTPException(401,'Sessão expirada')
    return {'apikey':KEY,'Authorization':'Bearer '+entry['token']}

async def supa(req,method,path,**kwargs):
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            r=await client.request(method,URL+path,headers={**auth_headers(req),**kwargs.pop('headers',{})},**kwargs)
        except httpx.HTTPError:
            raise HTTPException(503,'Serviço temporariamente indisponível')
    if r.status_code >= 400:
        # Nunca expor detalhes internos, credenciais ou dados pessoais do serviço.
        raise HTTPException(502,'Não foi possível concluir a operação no Supabase')
    return r

async def role_of(req,user_id):
    r=await supa(req,'GET','/rest/v1/profiles',params={'id':'eq.'+user_id,'select':'role'})
    rows=r.json()
    return rows[0].get('role','collaborator') if rows else 'collaborator'

async def documents_for(req,user_id):
    r=await supa(req,'GET','/rest/v1/documents',params={'user_id':'eq.'+user_id,'select':'id,output_filename,created_at','order':'created_at.desc','limit':'30'})
    return r.json()

async def document_row(req,doc_id,user_id):
    r=await supa(req,'GET','/rest/v1/documents',params={'id':'eq.'+doc_id,'user_id':'eq.'+user_id,'select':'id,storage_path,output_filename'})
    rows=r.json()
    if not rows: raise HTTPException(404,'Documento não encontrado')
    return rows[0]

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/')
async def home(req:Request):
    user=await identity(req)
    if not user:
        return page(req,'''<section><h2>Entrar</h2><form method="post" action="/login"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>E-mail</label><input type="email" name="email" required autocomplete="username"><label>Senha</label><input type="password" name="password" required autocomplete="current-password"><button>Entrar</button></form><small>Acesso somente para usuários cadastrados no escritório.</small></section>''')
    email=html.escape(user.get('email',''))
    uid=user['id']
    try:
        role=await role_of(req,uid)
        docs=await documents_for(req,uid)
        history=''.join(f'<li>{html.escape(str(d.get("created_at", ""))[:16])} · {html.escape(d.get("output_filename") or "peticao_formatada.docx")} · <a href="/documentos/{html.escape(d["id"])}">Baixar</a> <form style="display:inline" method="post" action="/documentos/{html.escape(d["id"])}/excluir"><input type="hidden" name="csrf" value="CSRF_VALUE"><button style="width:auto;padding:5px">Excluir</button></form></li>' for d in docs)
        if not history: history='<li>Nenhum documento salvo.</li>'
        admin=''
        if role=='admin':
            profiles=(await supa(req,'GET','/rest/v1/profiles',params={'select':'full_name,role,created_at','order':'created_at.desc','limit':'100'})).json()
            people=''.join(f'<li>{html.escape(x.get("full_name") or "Sem nome")} — {html.escape(x.get("role") or "")}</li>' for x in profiles)
            admin=f'<section><h3>Painel administrativo (consulta)</h3><p>Perfis cadastrados: {len(profiles)}</p><ul>{people}</ul><small>Convites e alterações de permissões ainda são feitos no Supabase.</small></section>'
        notice='<p class="alert">Versão de homologação: use somente textos fictícios. Documentos gerados serão salvos em armazenamento privado e poderão ser excluídos do histórico.</p>'
    except HTTPException:
        # Falha de integração não deve permitir acesso privilegiado nem salvar documentos silenciosamente.
        return page(req,'<section><h2>Configuração incompleta</h2><p>Não foi possível consultar os perfis ou o histórico no Supabase. Verifique as tabelas e políticas configuradas.</p><form method="post" action="/sair"><input type="hidden" name="csrf" value="CSRF_VALUE"><button>Sair</button></form></section>')
    return page(req,f"""<section><h2>Formatar petição</h2><p>Conectado: {email} · {"Administrador" if role=="admin" else "Colaborador"}</p>{notice}<form action="/formatar" method="post" enctype="multipart/form-data"><input type="hidden" name="csrf" value="CSRF_VALUE"><label>Opção A — Enviar Word (.docx)</label><input type="file" name="arquivo" accept=".docx"><p>OU</p><label>Opção B — Colar texto</label><textarea name="texto" rows="11" placeholder="Cole aqui a petição..."></textarea><small>Escolha apenas uma opção. Texto colado não preserva negritos, tabelas ou estilos originais.</small><button>Gerar documento Word</button></form><h3>Meus documentos (últimos 30)</h3><ul>{history}</ul><form method="post" action="/sair"><input type="hidden" name="csrf" value="CSRF_VALUE"><button>Sair</button></form></section>{admin}""")

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
    user=await identity(req)
    uid=user['id']; doc_id=str(uuid.uuid4())
    path=f'{uid}/{doc_id}.docx'
    # O arquivo original não é armazenado; somente a saída formatada.
    await supa(req,'POST','/storage/v1/object/peticoes/'+path,data=output,headers={'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','x-upsert':'false'})
    try:
        await supa(req,'POST','/rest/v1/documents',json={'id':doc_id,'user_id':uid,'original_filename':arquivo.filename if has_file else None,'output_filename':'peticao_formatada.docx','storage_path':path},headers={'Content-Type':'application/json','Prefer':'return=minimal'})
    except HTTPException:
        try: await supa(req,'DELETE','/storage/v1/object/peticoes/'+path)
        except HTTPException: pass
        raise
    return StreamingResponse(BytesIO(output),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="peticao_formatada.docx"','Cache-Control':'no-store'})

@app.get('/documentos/{doc_id}')
async def baixar(req:Request,doc_id:uuid.UUID):
    user=await identity(req)
    if not user: raise HTTPException(401,'Faça login novamente')
    doc=await document_row(req,str(doc_id),user['id'])
    r=await supa(req,'GET','/storage/v1/object/authenticated/peticoes/'+doc['storage_path'])
    return StreamingResponse(BytesIO(r.content),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="peticao_formatada.docx"','Cache-Control':'no-store'})

@app.post('/documentos/{doc_id}/excluir')
async def excluir(req:Request,doc_id:uuid.UUID,csrf:str=Form(...)):
    check_csrf(req,csrf)
    user=await identity(req)
    if not user: raise HTTPException(401,'Faça login novamente')
    doc=await document_row(req,str(doc_id),user['id'])
    await supa(req,'DELETE','/storage/v1/object/peticoes/'+doc['storage_path'])
    await supa(req,'DELETE','/rest/v1/documents',params={'id':'eq.'+str(doc_id),'user_id':'eq.'+user['id']})
    return RedirectResponse('/',status_code=303)
