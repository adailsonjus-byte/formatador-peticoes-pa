-- EXECUTAR UMA ÚNICA VEZ NO SQL EDITOR DO PROJETO EXISTENTE.
-- Não elimina documentos nem perfis existentes.
alter table public.profiles add column if not exists active boolean not null default true;
alter table public.profiles add column if not exists must_change_password boolean not null default false;
create table if not exists public.app_settings (
  id integer primary key check (id=1),
  template_path text,
  updated_at timestamptz not null default now()
);
alter table public.app_settings enable row level security;
-- Somente o backend com chave secreta acessará app_settings.
-- Não criar políticas públicas ou para authenticated.
insert into public.app_settings(id,template_path) values (1,null)
on conflict (id) do nothing;
-- Garante que ninguém possa executar a função is_admin via chamada pública sem necessidade.
-- As políticas antigas ainda usam is_admin(); por isso não revogamos EXECUTE.

-- IMPORTANTE: substitua o texto abaixo pelo E-MAIL DA CONTA COM QUE VOCÊ ENTRA NO SITE.
-- Isso resolve a situação em que o usuário novo consegue entrar, mas não é admin.
insert into public.profiles(id, full_name, role, active, must_change_password)
select id, 'Administrador principal', 'admin', true, false
from auth.users
where lower(email)=lower('SUBSTITUA_PELO_EMAIL_DA_CONTA_QUE_ENTRA_NO_SITE')
on conflict (id) do update set role='admin', active=true;
