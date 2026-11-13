# Formatador de Petições — PA Advocacia (homologação)

Versão para testes com dados fictícios. Não usar documentos reais de clientes.

## Implantação
1. Suba o conteúdo desta pasta (arquivos soltos e subpasta `assets`) para repositório **privado** do GitHub.
2. No Render, crie um **Blueprint** a partir desse repositório (arquivo `render.yaml`).
3. Configure `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY` no Render. **Não use Secret Key**.
4. Aguarde a implantação e acesse o link fornecido pelo Render.
5. Entre com a conta criada em Supabase Authentication > Users.

## O que funciona
- Login por Supabase Auth (e-mail e senha), cadastro público desabilitado no Supabase.
- Upload de DOCX OU colagem de texto, aplicação do papel timbrado e formatação Cambria 13, margens ABNT, espaçamento simples, 6pt e recuo 1,25cm.
- Download de DOCX, sem gravar o conteúdo em banco ou Storage.
- Sessões em memória; hibernação do Render encerra sessões e exige novo login.

## Pendências para produção
- Testes de fidelidade visual e jurídica com documentos variados; scanner de arquivos; controles de abuso e rate limit; política de retenção; trilha de auditoria; testes de autorização; gestão de colaboradores e segundo administrador; proteção LGPD e revisão jurídica/técnica; armazenamento e histórico persistentes no Supabase.
- A aplicação não usa a tabela `documents` nem o bucket `peticoes` nesta versão; ambos permanecem configurados para evolução posterior.
- O Render Free não é adequado para produção com documentos pessoais ou sigilosos.
