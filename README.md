<div align="center">
  <img src="/static/Logo.png" alt="Logo TambaTanqui" width="200px" style="">
</div>
<div style="text-align:justify;">
## Monitoramento de Parâmetros na Piscicultura

O seguinte projeto é realizado pelos estudantes da turma do 3º ano "A" - do curso Técnico em Informática - do Instituto Federal de Rondônia *Campus* Ariquemes. Ele visa resolver um problema identificado na área da piscicultura, realizando pesquisas sobre o assunto e o desenvolvimento de um software responsável por monitorar os parâmetros necessários para a criação de peixes, especificamente do Tambaqui (*Colossoma macropomum*), peixe representante da região.

- O problema: As variações bruscas na qualidade da água dos tanques, como oxigênio baixo, temperatura inadequada e pH desequilibrado, podem dizimar uma produção inteira de tambaqui em poucas horas.
- O sistema resolve essa dor, ao integrar funcionalidades de monitoramento e correção desses parâmetros.

## Funcionalidades da Aplicação:
No sistema, é possível cadastrar tanques de peixes, verificar seus parâmetros atuais, atualizar seus parâmetros e realizar check-ups de qualidade. Além disso, a aplicação gerará relatórios e os salvará em um histórico para a futura consulta dos dados.

## Como Acessar a Aplicação:
O sistema está disponível na web através da ferramenta PythonAnywhere, que permite a disponibilização do código da aplicação na Internet. Para acessar o site, basta [CLICAR AQUI](https://psicultura.pythonanywhere.com/login?next=/). O site também pode ser acessado por dispositivos mobile.

## Preparar banco de dados MySQL

1. Configure e execute `setup_database.sql` com uma conta administrativa do MySQL.
   O script cria o esquema, `admin_db`, `app_user`, a procedure de cadastro e os
   gatilhos de auditoria. Troque os dois valores de senha de exemplo no script
   antes da execução.
2. No terminal integrado do VS Code, selecione o interpretador Python do
   projeto e instale as dependências nele:

   ```powershell
   python -m pip install -r requirements.txt
   ```

   Usar `python -m pip` garante que o Flask seja instalado no mesmo ambiente
   Python que executará a aplicação. Se o comando `python` não estiver
   disponível, instale Python e selecione seu interpretador em
   **Python: Select Interpreter** no VS Code.
3. Defina no ambiente ou em um arquivo `.env` não versionado:

   ```text
   SECRET_KEY=<valor aleatório forte>
   DATABASE_MODE=mysql
   MYSQL_PASSWORD=<mesma senha configurada para app_user no MySQL>
   MYSQL_HOST=localhost
   MYSQL_PORT=3306
   MYSQL_DATABASE=tambatanqui_db
   ADMIN_EMAIL=admin@tambatanqui.com
   ADMIN_PASSWORD=<senha forte exclusiva para o administrador>
   SESSION_COOKIE_SECURE=true
   ```

   Gere `SECRET_KEY` com `python -c "import secrets; print(secrets.token_hex(32))"`.
   `ADMIN_PASSWORD` é convertida em hash pelo Werkzeug; sem essa variável, o
   aplicativo não cria nem redefine a conta administrativa. Depois de carregar
   o esquema e as variáveis de ambiente, execute uma vez
   `python -m flask --app app inicializar-admin` antes de iniciar o servidor WSGI.
4. Inicie a aplicação por um servidor WSGI em produção. `SESSION_COOKIE_SECURE`
   deve permanecer `true` atrás de HTTPS; use `false` apenas em desenvolvimento
   local sem TLS. Para testar localmente após instalar as dependências,
   execute `python -m flask --app app run`.

Para executar localmente sem MySQL, defina `DATABASE_MODE=sqlite` e
`SESSION_COOKIE_SECURE=false` no `.env`. Nesse modo, as tabelas são criadas
automaticamente em `instance/banco_local.db`. Esse modo é apenas para
desenvolvimento; no MySQL, configure corretamente `MYSQL_PASSWORD`, execute
`setup_database.sql` e mantenha `DATABASE_MODE=mysql`.

O Flask conecta-se com `app_user`, que possui apenas SELECT, INSERT, UPDATE e
DELETE no banco `tambatanqui_db`. Operações de estrutura devem ser realizadas
por uma conta administrativa, fora do processo web. Consulte o cabeçalho de
`setup_database.sql` para o comando `mysqldump`.

O banco SQLite antigo em `instance/banco.db` não é migrado automaticamente:
faça uma cópia e migre os dados antes de apontar uma instalação existente para
o novo banco MySQL.

**Nosso cardume:**
- Marcos Teles de Andrade
- Mariana Bongiolo de Jesus
- Natã Frederico Sangalletti
- Vitória Oliveira Titon
</div>
