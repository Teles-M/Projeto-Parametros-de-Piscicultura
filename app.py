
from datetime import date, datetime, timedelta
from functools import wraps
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets
import sys
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.error import URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import db as orm_db, Mortalidade, Relatorio, Tanque, Usuario
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

INSTANCE_DIR = BASE_DIR / "instance"

INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

# O arquivo local do projeto deve prevalecer sobre variáveis herdadas pelo
# terminal/VS Code, que podem apontar para outro banco e impedir o login local.
load_dotenv(BASE_DIR / ".env", override=True)

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# O usuário é fixo para impedir que uma configuração de ambiente eleve a
# conexão web a root/admin; somente senha e localização vêm do ambiente.
MYSQL_USER = "app_user"
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "tambatanqui_db")
DATABASE_MODE = os.environ.get("DATABASE_MODE", "mysql").strip().lower()
if DATABASE_MODE == "mysql":
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    )
elif DATABASE_MODE == "sqlite":
    # SQLite é um modo explícito de desenvolvimento para iniciar o projeto
    # sem servidor MySQL; em produção, mantenha DATABASE_MODE=mysql.
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"sqlite:///{INSTANCE_DIR / 'banco_local.db'}"
    )
else:
    raise RuntimeError(
        "DATABASE_MODE deve ser 'mysql' ou 'sqlite'."
    )

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Cookies inacessíveis ao JavaScript, enviados somente em HTTPS e com política
# SameSite reduzem roubo de sessão e envio cross-site; permita HTTP apenas local.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "true").strip().lower()
    in {"1", "true", "yes"}
)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

orm_db.init_app(app)

if DATABASE_MODE == "sqlite":
    # O banco local é criado pelo ORM, sem exigir privilégios DDL ao usuário
    # restrito do MySQL. A opção PRAGMA mantém as chaves estrangeiras ativas.
    from sqlalchemy import event

    with app.app_context():
        @event.listens_for(orm_db.engine, "connect")
        def ativar_chaves_estrangeiras_sqlite(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        orm_db.create_all()

# O arquivo de log registra eventos de segurança, sem gravar senhas nem corpos
# de requisição, para apoiar investigação sem expor credenciais.
security_logger = logging.getLogger("tambatanqui.security")
security_logger.setLevel(logging.WARNING)
security_logger.propagate = False
if not any(
    isinstance(handler, logging.FileHandler)
    and Path(handler.baseFilename) == (BASE_DIR / "security.log")
    for handler in security_logger.handlers
):
    security_handler = logging.FileHandler(
        BASE_DIR / "security.log",
        encoding="utf-8",
    )
    security_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    security_logger.addHandler(security_handler)
if not os.environ.get("SECRET_KEY"):
    security_logger.warning(
        "SECRET_KEY ausente; foi gerada uma chave temporária. "
        "Configure uma chave estável no ambiente."
    )
if not MYSQL_PASSWORD:
    security_logger.warning(
        "MYSQL_PASSWORD não foi configurada; a conexão MySQL não autenticará."
    )

# ============================================================
# CONFIGURAÇÃO DO ADMINISTRADOR
# ============================================================

ADMIN_EMAIL = os.environ.get(
    "ADMIN_EMAIL",
    "admin@tambatanqui.com"
).strip().lower()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")


# ============================================================
# SESSÃO
# ============================================================

SESSION_TIMEOUT = 30 * 60
APP_STARTED_AT = time.monotonic()


# ============================================================
# RATE LIMIT DO LOGIN
# ============================================================

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW = 15 * 60

login_attempts = defaultdict(deque)
login_lock = Lock()

COLUNAS_RELATORIO = (
    Relatorio.id,
    Relatorio.tanque_id,
    Relatorio.data_gerado,
    Relatorio.temperatura,
    Relatorio.ph,
    Relatorio.oxigenio,
    Relatorio.amonia,
    Relatorio.nitrito,
    Tanque.nome.label("tanque_nome"),
)


# ============================================================
# PARÂMETROS DA PISCICULTURA
# ============================================================

PARAMETROS = {
    "temperatura": {
        "nome": "Temperatura",
        "unidade": "°C",
        "min": 25,
        "max": 30,
    },
    "ph": {
        "nome": "pH",
        "unidade": "",
        "min": 6.5,
        "max": 8,
    },
    "oxigenio": {
        "nome": "Oxigênio",
        "unidade": " mg/L",
        "min": 5,
    },
    "amonia": {
        "nome": "Amônia",
        "unidade": " mg/L",
        "max": 0.1,
    },
    "nitrito": {
        "nome": "Nitrito",
        "unidade": " mg/L",
        "max": 0.5,
    },
}


# ============================================================
# BANCO DE DADOS
# ============================================================

def inicializar_admin():
    """Cria ou atualiza a conta administrativa se houver credencial no ambiente."""
    if not ADMIN_PASSWORD:
        security_logger.warning(
            "ADMIN_PASSWORD não foi configurada; inicialização de admin ignorada."
        )
        return

    usuario = Usuario.query.filter(
        func.lower(Usuario.email) == ADMIN_EMAIL
    ).first()
    if usuario is None:
        usuario = Usuario(
            nome="Administrador",
            email=ADMIN_EMAIL,
            # O hash é persistido; a senha configurada não é gravada em claro.
            senha=generate_password_hash(ADMIN_PASSWORD),
            data_cadastro=date.today().isoformat(),
            is_admin=True,
        )
        orm_db.session.add(usuario)
    else:
        usuario.is_admin = True
        usuario.senha = generate_password_hash(ADMIN_PASSWORD)
    orm_db.session.commit()


@app.cli.command("inicializar-admin")
def inicializar_admin_cli():
    """Prepara o usuário administrador depois de configurar o esquema MySQL."""
    if not ADMIN_PASSWORD:
        raise RuntimeError(
            "Defina ADMIN_PASSWORD no ambiente antes de inicializar o administrador."
        )
    inicializar_admin()
    print("Administrador inicializado com hash Werkzeug.")


# ============================================================
# VALIDAÇÕES
# ============================================================

def validar_tanque(
    nome,
    capacidade,
    especie,
    quantidade_inicial
):
    erros = []

    if not nome or not nome.strip():
        erros.append("Informe o nome do tanque.")

    try:
        capacidade = float(capacidade)

        if capacidade <= 0:
            erros.append(
                "A capacidade deve ser maior que zero."
            )

    except (TypeError, ValueError):
        erros.append("Informe uma capacidade válida.")

    if not especie or not especie.strip():
        erros.append("Informe a espécie.")

    try:
        quantidade_inicial = int(quantidade_inicial)

        if quantidade_inicial < 0:
            erros.append(
                "A quantidade inicial não pode ser negativa."
            )

    except (TypeError, ValueError):
        erros.append(
            "Informe uma quantidade inicial válida."
        )

    return erros


def validar_email(email):
    """Aceita apenas endereços em formato básico e de tamanho limitado."""
    return bool(
        email
        and len(email) <= 254
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    )


def avaliar_parametro(nome, valor):

    parametro = PARAMETROS.get(nome)

    if not parametro:
        return ("ok", "Sem referência disponível.")

    try:
        valor = float(valor)

    except (TypeError, ValueError):
        return ("alerta", "Valor inválido.")

    minimo = parametro.get("min")
    maximo = parametro.get("max")

    if minimo is not None and valor < minimo:
        return (
            "alerta",
            f"Abaixo do mínimo recomendado ({minimo}{parametro['unidade']})",
        )

    if maximo is not None and valor > maximo:
        return (
            "alerta",
            f"Acima do máximo recomendado ({maximo}{parametro['unidade']})",
        )

    return ("ok", "Dentro do intervalo recomendado.")


def validar_parametros(
    temperatura,
    ph,
    oxigenio,
    amonia,
    nitrito
):

    valores = {
        "temperatura": temperatura,
        "ph": ph,
        "oxigenio": oxigenio,
        "amonia": amonia,
        "nitrito": nitrito,
    }

    resultados = {}

    for nome, valor in valores.items():
        resultados[nome] = avaliar_parametro(
            nome,
            valor
        )

    return resultados


# ============================================================
# CONTEXTO DOS TEMPLATES
# ============================================================

@app.context_processor
def contexto_global():

    usuario = None

    if session.get("usuario_id"):

        usuario = {
            "id": session.get("usuario_id"),
            "nome": session.get("usuario"),
            "is_admin": bool(
                session.get("is_admin", False)
            ),
        }

    return {
        "today": date.today(),
        "usuario": usuario,
        "avaliar_parametro": avaliar_parametro,
    }


# ============================================================
# RATE LIMIT
# ============================================================

def login_bloqueado(ip):

    agora = time.time()

    with login_lock:

        tentativas = login_attempts[ip]

        while (
            tentativas
            and agora - tentativas[0] > LOGIN_WINDOW
        ):
            tentativas.popleft()

        return len(tentativas) >= LOGIN_MAX_ATTEMPTS


def registrar_tentativa_login(ip):

    agora = time.time()

    with login_lock:

        tentativas = login_attempts[ip]

        while (
            tentativas
            and agora - tentativas[0] > LOGIN_WINDOW
        ):
            tentativas.popleft()

        tentativas.append(agora)


# ============================================================
# CONTROLE DE SESSÃO
# ============================================================

@app.before_request
def controlar_sessao():

    if "usuario_id" not in session:
        return

    agora = time.time()
    ultima_atividade = session.get(
        "ultima_atividade"
    )

    if ultima_atividade:

        if agora - ultima_atividade > SESSION_TIMEOUT:

            session.clear()

            flash(
                "Sua sessão expirou. Entre novamente.",
                "warning",
            )

            return redirect(
                url_for("login")
            )

    session["ultima_atividade"] = agora


# ============================================================
# DECORATORS
# ============================================================

def login_required(view):

    @wraps(view)
    def wrapped_view(*args, **kwargs):

        if "usuario_id" not in session:

            security_logger.warning(
                "Acesso não autenticado à rota %s; IP=%s",
                request.path,
                request.remote_addr or "desconhecido",
            )
            flash(
                "Faça login para acessar esta página.",
                "warning",
            )

            return redirect(
                url_for("login")
            )

        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):

    @wraps(view)
    def wrapped_view(*args, **kwargs):

        if "usuario_id" not in session:

            security_logger.warning(
                "Tentativa de acesso administrativo sem autenticação; "
                "rota=%s IP=%s",
                request.path,
                request.remote_addr or "desconhecido",
            )
            flash(
                "Faça login para acessar esta página.",
                "warning",
            )

            return redirect(
                url_for("login")
            )

        if not session.get("is_admin", False):

            security_logger.warning(
                "Acesso administrativo não autorizado; usuario_id=%s "
                "rota=%s IP=%s",
                session.get("usuario_id"),
                request.path,
                request.remote_addr or "desconhecido",
            )
            flash(
                "Acesso permitido somente ao administrador.",
                "danger",
            )

            return redirect(
                url_for("index")
            )

        return view(*args, **kwargs)

    return wrapped_view


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if "usuario_id" in session:
        if session.get("is_admin"):
            return redirect(url_for("admin"))
        return redirect(url_for("index"))

    return render_template("home.html")


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if "usuario_id" in session:
        if session.get("is_admin"):
            return redirect(url_for("admin"))
        return redirect(url_for("index"))

    if request.method == "POST":

        ip = request.remote_addr or "desconhecido"

        if login_bloqueado(ip):

            security_logger.warning(
                "Login bloqueado após excesso de tentativas; IP=%s",
                ip,
            )
            flash(
                "Muitas tentativas de login. Tente novamente mais tarde.",
                "danger",
            )

            return render_template(
                "login.html"
            )

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        senha = request.form.get(
            "senha",
            ""
        )

        if not validar_email(email) or not senha or len(senha) > 256:
            registrar_tentativa_login(ip)
            security_logger.warning(
                "Falha de login por entrada inválida; IP=%s",
                ip,
            )
            flash("E-mail ou senha incorretos.", "danger")
            return render_template("login.html")

        # O ORM transforma o valor em parâmetro vinculado; não se monta SQL
        # concatenando o e-mail recebido do formulário.
        usuario = Usuario.query.filter(
            func.lower(Usuario.email) == email
        ).first()

        if (
            usuario
            and check_password_hash(
                usuario.senha,
                senha
            )
        ):

            # Rotaciona o estado autenticado após validar a senha, evitando
            # reaproveitar valores de sessão fornecidos antes do login.
            session.clear()

            session.permanent = True
            session["usuario_id"] = usuario.id
            session["usuario"] = usuario.nome
            session["is_admin"] = usuario.is_admin
            session["ultima_atividade"] = time.time()

            if usuario.is_admin:
                return redirect(
                    url_for("admin")
                )

            return redirect(
                url_for("index")
            )

        registrar_tentativa_login(ip)
        security_logger.warning(
            "Falha de autenticação; IP=%s",
            ip,
        )

        flash(
            "E-mail ou senha incorretos.",
            "danger",
        )

    return render_template("login.html")


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    # Remove identidade e estado auxiliar do cookie assinado do Flask.
    session.clear()

    flash(
        "Você saiu da sua conta.",
        "success",
    )

    return redirect(
        url_for("home")
    )


# ============================================================
# CADASTRO
# ============================================================

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():

    if "usuario_id" in session:
        if session.get("is_admin"):
            return redirect(url_for("admin"))
        return redirect(url_for("index"))

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        senha = request.form.get(
            "senha",
            ""
        )

        confirmar_senha = request.form.get(
            "confirmar_senha",
            ""
        )

        if not nome or not email or not senha:

            flash(
                "Preencha todos os campos.",
                "danger",
            )

            return render_template(
                "cadastro.html"
            )

        if len(nome) > 120 or not validar_email(email):
            flash(
                "Informe um nome e um e-mail válidos.",
                "danger",
            )
            return render_template("cadastro.html")

        if senha != confirmar_senha:

            flash(
                "As senhas não coincidem.",
                "danger",
            )

            return render_template(
                "cadastro.html"
            )

        if len(senha) < 8 or len(senha) > 256:

            flash(
                "A senha deve ter entre 8 e 256 caracteres.",
                "danger",
            )

            return render_template(
                "cadastro.html"
            )

        if email == ADMIN_EMAIL:

            flash(
                "Este e-mail é reservado para o administrador.",
                "danger",
            )

            return render_template(
                "cadastro.html"
            )

        # Verificação também usa expressão ORM parametrizada antes da inserção.
        existente = Usuario.query.filter(
            func.lower(Usuario.email) == email
        ).first()

        if existente:

            flash(
                "Este e-mail já está cadastrado.",
                "warning",
            )

            return render_template(
                "cadastro.html"
            )

        novo_usuario = Usuario(
            nome=nome,
            email=email,
            # A senha original nunca entra no ORM nem é armazenada no banco.
            senha=generate_password_hash(senha),
            data_cadastro=date.today().isoformat(),
            is_admin=False,
        )
        orm_db.session.add(novo_usuario)
        try:
            orm_db.session.commit()
        except IntegrityError:
            orm_db.session.rollback()
            flash("Este e-mail já está cadastrado.", "warning")
            return render_template("cadastro.html")

        flash(
            "Conta criada com sucesso! Agora faça login.",
            "success",
        )

        return redirect(
            url_for("login")
        )

    return render_template(
        "cadastro.html"
    )


# ============================================================
# PERFIL
# ============================================================

@app.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():

    usuario = orm_db.session.get(Usuario, session["usuario_id"])
    if usuario is None:
        session.clear()
        flash("Sua conta não foi encontrada. Entre novamente.", "warning")
        return redirect(url_for("login"))

    def renderizar_perfil():
        # Não envie a entidade completa ao template, pois ela contém o hash
        # da senha que não é necessário para editar os demais dados.
        dados_publicos = {
            "id": usuario.id,
            "nome": usuario.nome,
            "email": usuario.email,
            "data_cadastro": usuario.data_cadastro,
            "is_admin": usuario.is_admin,
        }
        return render_template(
            "perfil.html",
            usuario_perfil=dados_publicos,
        )

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirmacao = request.form.get("confirmacao", "")

        if not nome or len(nome) > 120 or not validar_email(email):
            flash("Informe um nome e um e-mail válidos.", "danger")
            return renderizar_perfil()

        email_existente = Usuario.query.filter(
            func.lower(Usuario.email) == email,
            Usuario.id != usuario.id,
        ).first()
        if email_existente:
            flash("Este e-mail já está cadastrado em outra conta.", "warning")
            return renderizar_perfil()

        senha_solicitada = bool(senha_atual or nova_senha or confirmacao)
        if senha_solicitada:
            if not all((senha_atual, nova_senha, confirmacao)):
                flash(
                    "Preencha a senha atual, a nova senha e a confirmação.",
                    "danger",
                )
                return renderizar_perfil()
            if not check_password_hash(usuario.senha, senha_atual):
                security_logger.warning(
                    "Tentativa de alteração de senha com senha atual inválida; "
                    "usuario_id=%s IP=%s",
                    usuario.id,
                    request.remote_addr or "desconhecido",
                )
                flash("A senha atual está incorreta.", "danger")
                return renderizar_perfil()
            if len(nova_senha) < 8 or len(nova_senha) > 256:
                flash("A nova senha deve ter entre 8 e 256 caracteres.", "danger")
                return renderizar_perfil()
            if nova_senha != confirmacao:
                flash("A nova senha e a confirmação não coincidem.", "danger")
                return renderizar_perfil()
            usuario.senha = generate_password_hash(nova_senha)

        usuario.nome = nome
        usuario.email = email
        try:
            orm_db.session.commit()
        except IntegrityError:
            orm_db.session.rollback()
            flash("Não foi possível salvar: este e-mail já está cadastrado.", "warning")
            return renderizar_perfil()

        # Atualiza o nome armazenado na sessão para a navegação refletir a
        # alteração imediatamente sem permitir que o formulário mude permissões.
        session["usuario"] = usuario.nome
        flash("Perfil atualizado com sucesso.", "success")
        return redirect(url_for("perfil"))

    return renderizar_perfil()


# ============================================================
# PAINEL PRINCIPAL
# ============================================================

@app.route("/index")
@login_required
def index():

    if session.get("is_admin"):
        return redirect(url_for("admin"))

    tanques = Tanque.query.filter_by(
        usuario_id=session["usuario_id"]
    ).order_by(Tanque.id.desc()).all()

    total_peixes = sum(
        tanque.quantidade_atual
        for tanque in tanques
    )

    estatisticas = {
        "tanques": len(tanques),
        "peixes": total_peixes,
        "capacidade": sum(
            float(tanque.capacidade)
            for tanque in tanques
        ),
        "mortes": orm_db.session.scalar(
            select(func.coalesce(func.sum(Mortalidade.quantidade), 0))
            .join(Tanque, Mortalidade.tanque_id == Tanque.id)
            .where(Tanque.usuario_id == session["usuario_id"])
        ),
    }

    mortes = orm_db.session.execute(
        select(
            Mortalidade.id,
            Mortalidade.tanque_id,
            Mortalidade.data,
            Mortalidade.quantidade,
            Mortalidade.observacao,
            Tanque.nome.label("tanque_nome"),
        )
        .join(Tanque, Mortalidade.tanque_id == Tanque.id)
        .where(Tanque.usuario_id == session["usuario_id"])
        .order_by(Mortalidade.id.desc())
        .limit(5)
    ).mappings().all()

    return render_template(
        "index.html",
        tanques=tanques,
        total_peixes=total_peixes,
        parametros=PARAMETROS,
        estatisticas=estatisticas,
        mortes=mortes,
    )


# ============================================================
# ADMINISTRADOR
# ============================================================

@app.route("/admin")
@admin_required
def admin():
    pesquisa = request.args.get("q", "").strip()[:100]

    # Um ping real ao banco evita apresentar indicadores com aparência normal
    # quando a aplicação perdeu acesso ao armazenamento.
    try:
        with orm_db.engine.connect() as connection:
            connection.execute(select(1))
    except SQLAlchemyError:
        orm_db.session.rollback()
        security_logger.exception(
            "Falha ao verificar o banco no painel administrativo."
        )
        return render_template(
            "admin.html",
            dashboard_disponivel=False,
            erro_banco=True,
            modo_banco=DATABASE_MODE.upper(),
            sistema_operacional=platform.system(),
            versao_python=platform.python_version(),
            tempo_atividade=None,
            verificado_em=datetime.now(),
            pesquisa=pesquisa,
            usuarios=[],
            total_usuarios=None,
            total_tanques=None,
            total_peixes=None,
            total_mortalidades=None,
            total_relatorios=None,
            novos_usuarios_30_dias=None,
        ), 503

    consulta_usuarios = select(
        Usuario.id,
        Usuario.nome,
        Usuario.email,
        Usuario.data_cadastro,
        Usuario.is_admin,
        select(func.count(Tanque.id))
        .where(Tanque.usuario_id == Usuario.id)
        .correlate(Usuario)
        .scalar_subquery()
        .label("total_tanques"),
        select(func.coalesce(func.sum(Tanque.quantidade_atual), 0))
        .where(Tanque.usuario_id == Usuario.id)
        .correlate(Usuario)
        .scalar_subquery()
        .label("total_peixes"),
        select(func.count(Mortalidade.id))
        .join(Tanque, Mortalidade.tanque_id == Tanque.id)
        .where(Tanque.usuario_id == Usuario.id)
        .correlate(Usuario)
        .scalar_subquery()
        .label("total_mortalidades"),
        select(func.count(Relatorio.id))
        .join(Tanque, Relatorio.tanque_id == Tanque.id)
        .where(Tanque.usuario_id == Usuario.id)
        .correlate(Usuario)
        .scalar_subquery()
        .label("total_relatorios"),
    )

    if pesquisa:
        termo = f"%{pesquisa}%"
        filtros = [
            Usuario.nome.ilike(termo),
            Usuario.email.ilike(termo),
        ]
        if pesquisa.isdigit():
            filtros.append(Usuario.id == int(pesquisa))
        consulta_usuarios = consulta_usuarios.where(
            or_(*filtros)
        )

    usuarios = orm_db.session.execute(
        consulta_usuarios.order_by(Usuario.id.desc())
    ).mappings().all()

    total_usuarios = orm_db.session.scalar(
        select(func.count(Usuario.id))
    )
    total_tanques = orm_db.session.scalar(
        select(func.count(Tanque.id))
    )
    total_peixes = orm_db.session.scalar(
        select(func.coalesce(func.sum(Tanque.quantidade_atual), 0))
    )
    total_mortalidades = orm_db.session.scalar(
        select(func.count(Mortalidade.id))
    )
    total_relatorios = orm_db.session.scalar(
        select(func.count(Relatorio.id))
    )
    data_limite = (date.today() - timedelta(days=30)).isoformat()
    novos_usuarios_30_dias = orm_db.session.scalar(
        select(func.count(Usuario.id)).where(
            Usuario.data_cadastro >= data_limite
        )
    )

    segundos_ativo = int(time.monotonic() - APP_STARTED_AT)
    horas, resto = divmod(segundos_ativo, 3600)
    minutos, segundos = divmod(resto, 60)
    tempo_atividade = (
        f"{horas}h {minutos:02d}min {segundos:02d}s"
    )

    return render_template(
        "admin.html",
        dashboard_disponivel=True,
        erro_banco=False,
        modo_banco=DATABASE_MODE.upper(),
        sistema_operacional=platform.system(),
        versao_python=sys.version.split()[0],
        tempo_atividade=tempo_atividade,
        verificado_em=datetime.now(),
        pesquisa=pesquisa,
        usuarios=usuarios,
        total_usuarios=total_usuarios,
        total_tanques=total_tanques,
        total_peixes=total_peixes,
        total_mortalidades=total_mortalidades,
        total_relatorios=total_relatorios,
        novos_usuarios_30_dias=novos_usuarios_30_dias,
    )


# ============================================================
# API DO CLIMA
# ============================================================

@app.route("/api/clima")
@login_required
def clima():

    latitude = request.args.get("latitude")
    longitude = request.args.get("longitude")

    if not latitude or not longitude:

        return jsonify({
            "erro": "Latitude e longitude são necessárias."
        }), 400

    try:

        parametros = urlencode({
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,"
                "relative_humidity_2m,"
                "wind_speed_10m,"
                "apparent_temperature,"
                "weather_code"
            ),
            "timezone": "auto",
        })

        url = (
            "https://api.open-meteo.com/v1/forecast?"
            + parametros
        )

        requisicao = Request(
            url,
            headers={
                "User-Agent": "TambaTanqui/1.0"
            },
        )

        with urlopen(
            requisicao,
            timeout=10
        ) as resposta:

            dados = json.loads(
                resposta.read().decode("utf-8")
            )

        atual = dados.get("current", {})

        return jsonify({
            "temperatura": atual.get("temperature_2m"),
            "sensacao": atual.get("apparent_temperature"),
            "umidade": atual.get("relative_humidity_2m"),
            "vento": atual.get("wind_speed_10m"),
            "codigo_tempo": atual.get("weather_code"),
            "timezone": dados.get("timezone"),
        })

    except (
        URLError,
        TimeoutError,
        ValueError
    ):

        return jsonify({
            "erro": "Não foi possível consultar o clima."
        }), 502


# ============================================================
# COMPARAR TANQUES
# ============================================================

@app.route("/comparar")
@login_required
def comparar():

    tanques = Tanque.query.filter_by(
        usuario_id=session["usuario_id"]
    ).order_by(Tanque.nome).all()

    return render_template(
        "comparar.html",
        tanques=tanques,
        parametros=PARAMETROS,
    )


# ============================================================
# RELATÓRIO
# ============================================================

@app.route("/relatorio/<int:id>")
@login_required
def relatorio_detalhe(id):

    relatorio_atual = orm_db.session.execute(
        select(*COLUNAS_RELATORIO)
        .join(Tanque, Relatorio.tanque_id == Tanque.id)
        .where(
            Relatorio.id == id,
            Tanque.usuario_id == session["usuario_id"],
        )
    ).mappings().first()

    if not relatorio_atual:
        flash("Relatório não encontrado.", "danger")
        return redirect(url_for("relatorio"))

    tanques = Tanque.query.filter_by(
        usuario_id=session["usuario_id"]
    ).order_by(Tanque.nome).all()
    relatorios = orm_db.session.execute(
        select(*COLUNAS_RELATORIO)
        .join(Tanque, Relatorio.tanque_id == Tanque.id)
        .where(Tanque.usuario_id == session["usuario_id"])
        .order_by(Relatorio.id.desc())
    ).mappings().all()

    return render_template(
        "relatorio.html",
        tanques=tanques,
        relatorios=relatorios,
        relatorio_atual=relatorio_atual,
        parametros=PARAMETROS,
    )


@app.route("/relatorio", methods=["GET", "POST"])
@login_required
def relatorio():

    tanques = Tanque.query.filter_by(
        usuario_id=session["usuario_id"]
    ).order_by(Tanque.nome).all()

    if request.method == "POST":

        tanque_id = request.form.get(
            "tanque_id"
        )

        try:
            tanque_id = int(tanque_id)
        except (TypeError, ValueError):
            tanque_id = -1

        tanque = Tanque.query.filter_by(
            id=tanque_id,
            usuario_id=session["usuario_id"],
        ).first()

        if not tanque:

            flash(
                "Tanque não encontrado.",
                "danger",
            )

            return redirect(
                url_for("relatorio")
            )

        relatorio_novo = Relatorio(
            tanque_id=tanque.id,
            data_gerado=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            temperatura=tanque.temperatura,
            ph=tanque.ph,
            oxigenio=tanque.oxigenio,
            amonia=tanque.amonia,
            nitrito=tanque.nitrito,
        )
        orm_db.session.add(relatorio_novo)
        orm_db.session.commit()

        flash(
            "Relatório gerado com sucesso.",
            "success",
        )

        return redirect(
            url_for("relatorio")
        )

    relatorios = orm_db.session.execute(
        select(*COLUNAS_RELATORIO)
        .join(Tanque, Relatorio.tanque_id == Tanque.id)
        .where(Tanque.usuario_id == session["usuario_id"])
        .order_by(Relatorio.id.desc())
    ).mappings().all()

    return render_template(
        "relatorio.html",
        tanques=tanques,
        relatorios=relatorios,
        relatorio_atual=None,
        parametros=PARAMETROS,
    )


# ============================================================
# CADASTRAR TANQUE
# ============================================================

@app.route(
    "/cadastrar_tanque",
    methods=["GET", "POST"]
)
@login_required
def cadastrar_tanque():

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        capacidade = request.form.get(
            "capacidade",
            ""
        )

        especie = request.form.get(
            "especie",
            ""
        ).strip()

        quantidade_inicial = request.form.get(
            "quantidade_inicial",
            ""
        )

        erros = validar_tanque(
            nome,
            capacidade,
            especie,
            quantidade_inicial,
        )

        if erros:

            for erro in erros:
                flash(erro, "danger")

            return render_template(
                "cadastro_tanque.html"
            )

        tanque = Tanque(
            nome=nome,
            capacidade=float(capacidade),
            especie=especie,
            data_cadastro=date.today().isoformat(),
            quantidade_inicial=int(quantidade_inicial),
            quantidade_atual=int(quantidade_inicial),
            usuario_id=session["usuario_id"],
        )
        orm_db.session.add(tanque)
        orm_db.session.commit()

        flash(
            "Tanque cadastrado com sucesso!",
            "success",
        )

        return redirect(
            url_for("index")
        )

    return render_template(
        "cadastro_tanque.html"
    )


# ============================================================
# EDITAR TANQUE
# ============================================================

@app.route(
    "/editar_tanque/<int:id>",
    methods=["GET", "POST"]
)
@login_required
def editar_tanque(id):

    tanque = Tanque.query.filter_by(
        id=id,
        usuario_id=session["usuario_id"],
    ).first()

    if not tanque:

        flash(
            "Tanque não encontrado.",
            "danger",
        )

        return redirect(
            url_for("index")
        )

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        capacidade = request.form.get(
            "capacidade",
            ""
        )

        especie = request.form.get(
            "especie",
            ""
        ).strip()

        quantidade_atual = request.form.get(
            "quantidade_atual",
            ""
        )

        temperatura = request.form.get(
            "temperatura",
            0
        )

        ph = request.form.get(
            "ph",
            0
        )

        oxigenio = request.form.get(
            "oxigenio",
            0
        )

        amonia = request.form.get(
            "amonia",
            0
        )

        nitrito = request.form.get(
            "nitrito",
            0
        )

        try:

            capacidade = float(capacidade)
            quantidade_atual = int(
                quantidade_atual
            )

            temperatura = float(
                temperatura
            )

            ph = float(ph)
            oxigenio = float(oxigenio)
            amonia = float(amonia)
            nitrito = float(nitrito)

        except (TypeError, ValueError):

            flash(
                "Verifique os valores informados.",
                "danger",
            )

            return render_template(
                "editar_tanque.html",
                tanque=tanque,
            )

        if not nome:

            flash(
                "Informe o nome do tanque.",
                "danger",
            )

            return render_template(
                "editar_tanque.html",
                tanque=tanque,
            )

        if capacidade <= 0:

            flash(
                "A capacidade deve ser maior que zero.",
                "danger",
            )

            return render_template(
                "editar_tanque.html",
                tanque=tanque,
            )

        if quantidade_atual < 0:

            flash(
                "A quantidade atual não pode ser negativa.",
                "danger",
            )

            return render_template(
                "editar_tanque.html",
                tanque=tanque,
            )

        tanque.nome = nome
        tanque.capacidade = capacidade
        tanque.especie = especie
        tanque.quantidade_atual = quantidade_atual
        tanque.temperatura = temperatura
        tanque.ph = ph
        tanque.oxigenio = oxigenio
        tanque.amonia = amonia
        tanque.nitrito = nitrito
        orm_db.session.commit()

        flash(
            "Tanque atualizado com sucesso!",
            "success",
        )

        return redirect(
            url_for("index")
        )

    return render_template(
        "editar_tanque.html",
        tanque=tanque,
    )


# ============================================================
# EXCLUIR TANQUE
# ============================================================

@app.route(
    "/excluir_tanque/<int:id>",
    methods=["POST", "GET"]
)
@login_required
def excluir_tanque(id):

    tanque = Tanque.query.filter_by(
        id=id,
        usuario_id=session["usuario_id"],
    ).first()

    if not tanque:

        flash(
            "Tanque não encontrado.",
            "danger",
        )

        return redirect(
            url_for("index")
        )

    orm_db.session.delete(tanque)
    orm_db.session.commit()

    flash(
        "Tanque excluído com sucesso.",
        "success",
    )

    return redirect(
        url_for("index")
    )


# ============================================================
# MORTALIDADE
# ============================================================

@app.route(
    "/mortalidade",
    methods=["GET", "POST"]
)
@login_required
def mortalidade():

    tanques = Tanque.query.filter_by(
        usuario_id=session["usuario_id"]
    ).order_by(Tanque.nome).all()

    if request.method == "POST":

        tanque_id = request.form.get(
            "tanque_id"
        )

        quantidade_texto = request.form.get(
            "quantidade",
            ""
        ).strip()

        data_mortalidade = request.form.get(
            "data"
        ) or date.today().isoformat()

        observacao = request.form.get(
            "observacao",
            ""
        ).strip()

        try:

            quantidade = int(quantidade_texto)

            if quantidade <= 0:
                raise ValueError

        except (TypeError, ValueError):

            flash(
                "Informe uma quantidade de mortalidade válida.",
                "danger",
            )

            return render_template(
                "mortalidade.html",
                tanques=tanques,
            )

        try:
            tanque_id = int(tanque_id)
        except (TypeError, ValueError):
            tanque_id = -1

        tanque = Tanque.query.filter_by(
            id=tanque_id,
            usuario_id=session["usuario_id"],
        ).first()

        if not tanque:

            flash(
                "Tanque não encontrado.",
                "danger",
            )

            return redirect(
                url_for("mortalidade")
            )

        if quantidade > tanque.quantidade_atual:

            flash(
                "A mortalidade não pode ser maior que a quantidade atual de peixes.",
                "danger",
            )

            return render_template(
                "mortalidade.html",
                tanques=tanques,
            )

        registro = Mortalidade(
            tanque_id=tanque.id,
            data=data_mortalidade,
            quantidade=quantidade,
            observacao=observacao,
        )
        tanque.quantidade_atual -= quantidade
        orm_db.session.add(registro)
        orm_db.session.commit()

        flash(
            "Mortalidade registrada com sucesso.",
            "success",
        )

        return redirect(
            url_for("mortalidade")
        )

    registros = orm_db.session.execute(
        select(
            Mortalidade.id,
            Mortalidade.tanque_id,
            Mortalidade.data,
            Mortalidade.quantidade,
            Mortalidade.observacao,
            Tanque.nome.label("tanque_nome"),
        )
        .join(Tanque, Mortalidade.tanque_id == Tanque.id)
        .where(Tanque.usuario_id == session["usuario_id"])
        .order_by(Mortalidade.id.desc())
    ).mappings().all()

    return render_template(
        "mortalidade.html",
        tanques=tanques,
        registros=registros,
    )


# ============================================================
# ERRO 404
# ============================================================

@app.errorhandler(404)
def pagina_nao_encontrada(error):

    return render_template(
        "errors/404.html"
    ), 404


# ============================================================
# ERRO 500
# ============================================================

@app.errorhandler(500)
def erro_servidor(error):

    return render_template(
        "errors/500.html"
    ), 500


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":
    with app.app_context():
        # O esquema é criado pelo script setup_database.sql com uma conta
        # administrativa; o processo web não recebe permissões de DDL.
        inicializar_admin()

    app.run(
        debug=os.environ.get("FLASK_DEBUG", "false").strip().lower()
        in {"1", "true", "yes"}
    )
