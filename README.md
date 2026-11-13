# Formatador de Petições — entrega única v0.7 (homologação)

Esta versão substitui a v0.6. É uma atualização consolidada, **não uma certificação de segurança de produção**.

## Instalação em três ações (sem criar serviços novos)

1. **Supabase → SQL Editor**: abra `migracao_unica.sql`, substitua `SUBSTITUA_PELO_EMAIL_DA_CONTA_QUE_ENTRA_NO_SITE` pelo e-mail exato do usuário que atualmente consegue entrar no site, e execute **uma vez**. Isso cria os campos de ativação e troca de senha, a configuração do papel timbrado e concede `admin` à conta escolhida. Não cole senhas ou chaves no SQL.
2. **Render → Environment**: adicione `SUPABASE_SECRET_KEY` com a chave `sb_secret_...` obtida em **Supabase → Project Settings → API Keys → Secret keys**. **Jamais coloque essa chave no GitHub, no navegador ou neste chat.** Mantenha as três variáveis anteriores. A chave é altamente privilegiada; se vazada, revogue imediatamente.
3. **GitHub**: envie **todos os arquivos e a pasta `assets`** deste pacote para a raiz do repositório privado, substituindo os existentes. **Não envie o ZIP como um único arquivo.** Faça **um único Commit changes**. No Render, use **Manual Deploy → Deploy latest commit** se necessário.

Após o deploy, faça login com a conta identificada no SQL. Deverá aparecer **Administração de colaboradores**.

## Funcionalidades

- Login com Supabase Auth, verificação de perfil ativo, e distinção entre administradores e colaboradores.
- Cadastro de usuários diretamente pelo site com senha temporária aleatória, exibida somente uma vez; a senha precisa ser alterada no primeiro uso.
- Desativação e reativação de colaboradores, redefinição administrativa de senha, alteração de senha pelo usuário.
- Recuperação por e-mail com formulário `/recuperar` e página `/redefinir` para receber o link padrão do Supabase.
- Substituição do modelo de papel timbrado pelo administrador; o arquivo fica no bucket privado e a escolha persiste no banco.
- Envio de `.docx` ou texto colado; geração de Word; histórico próprio, download e exclusão.
- Não reescreve o conteúdo da petição. Arquivos Word com elementos não suportados são recusados para evitar perda silenciosa.

## Recuperação por e-mail

Em **Supabase → Authentication → URL Configuration**, configure `Site URL` como `https://formatador-peticoes-pa.onrender.com` e autorize `https://formatador-peticoes-pa.onrender.com/redefinir` em Redirect URLs. O Supabase pode limitar e-mails no plano gratuito e a recuperação precisa ser testada com e-mail de teste. Se o provedor não retornar o token no fragmento `#access_token=...&type=recovery`, o fluxo precisará ser ajustado ao template de e-mail efetivo.

## Limites e segurança

**NÃO utilizar documentos reais de clientes nesta versão sem auditoria de segurança e avaliação de LGPD.** O plano gratuito do Render não possui garantias de disponibilidade e pode suspender por inatividade. Os dados e documentos ficam em serviço de terceiros, possivelmente com transferência internacional. A aplicação não oferece ainda: MFA obrigatório, auditoria centralizada persistente, política automatizada de retenção, revogação imediata de todos os tokens de um usuário, antimalware para DOCX, verificação abrangente de todas as estruturas OOXML, nem testes de penetração. As sessões em memória se perdem quando o Render reinicia.

As chaves secretas ficam somente no Render, e o servidor autoriza cada ação. A chave administrativa contorna RLS, portanto sua proteção e a revisão do código são indispensáveis. O código de testes não prova isolamento completo nem conformidade jurídica. Evite compartilhar senhas temporárias por canais inseguros.

**Atenção:** o formatador preserva conteúdo textual em documentos simples, mas não garante fidelidade total em todos os tipos de Word. Documentos com relações complexas são bloqueados; compare sempre o resultado ao original.
