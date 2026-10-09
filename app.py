
from datetime import date, datetime
from functools import wraps
import json
import os
from pathlib import Path
import sqlite3
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from models import db as orm_db
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

INSTANCE_DIR = BASE_DIR / "instance"

INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

DATABASE = INSTANCE_DIR / "banco.db"

load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)

print("BASE_DIR:", BASE_DIR)
print("TEMPLATES:", BASE_DIR / "templates")
print("ADMIN.HTML EXISTE:", (BASE_DIR / "templates" / "admin.html").exists())

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "chave-temporaria-tambatanqui"
)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATABASE}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

orm_db.init_app(app)


# ============================================================
# CONFIGURAÇÃO DO ADMINISTRADOR
# ============================================================

ADMIN_EMAIL = os.environ.get(
    "ADMIN_EMAIL",
    "admin@tambatanqui.com"
).strip().lower()

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "Tamba@Admin#2026!"
)


# ============================================================
# SESSÃO
# ============================================================

SESSION_TIMEOUT = 30 * 60


# ============================================================
# RATE LIMIT DO LOGIN
# ============================================================

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW = 15 * 60

login_attempts = defaultdict(deque)
login_lock = Lock()


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

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)

    if db is not None:
        db.close()


def criar_banco():
    """
    Cria as tabelas necessárias e faz pequenas migrações
    caso o banco antigo já exista.
    """

    db = get_db()

    # --------------------------------------------------------
    # USUÁRIOS
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            data_cadastro TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    colunas_usuarios = {
        coluna["name"]
        for coluna in db.execute(
            "PRAGMA table_info(usuarios)"
        ).fetchall()
    }

    if "is_admin" not in colunas_usuarios:
        db.execute(
            """
            ALTER TABLE usuarios
            ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0
            """
        )

    # --------------------------------------------------------
    # TANQUES
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tanques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            capacidade REAL NOT NULL,
            especie TEXT NOT NULL,
            data_cadastro TEXT NOT NULL,
            quantidade_inicial INTEGER NOT NULL,
            quantidade_atual INTEGER NOT NULL,
            temperatura REAL NOT NULL DEFAULT 0,
            ph REAL NOT NULL DEFAULT 0,
            oxigenio REAL NOT NULL DEFAULT 0,
            amonia REAL NOT NULL DEFAULT 0,
            nitrito REAL NOT NULL DEFAULT 0,
            usuario_id INTEGER,
            FOREIGN KEY (usuario_id)
                REFERENCES usuarios(id)
        )
        """
    )

    colunas_tanques = {
        coluna["name"]
        for coluna in db.execute(
            "PRAGMA table_info(tanques)"
        ).fetchall()
    }

    colunas_novas_tanques = {
        "usuario_id": "INTEGER",
        "temperatura": "REAL NOT NULL DEFAULT 0",
        "ph": "REAL NOT NULL DEFAULT 0",
        "oxigenio": "REAL NOT NULL DEFAULT 0",
        "amonia": "REAL NOT NULL DEFAULT 0",
        "nitrito": "REAL NOT NULL DEFAULT 0",
    }

    for coluna, tipo in colunas_novas_tanques.items():
        if coluna not in colunas_tanques:
            db.execute(
                f"ALTER TABLE tanques ADD COLUMN {coluna} {tipo}"
            )

    # --------------------------------------------------------
    # MORTALIDADES
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mortalidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanque_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            observacao TEXT DEFAULT '',
            FOREIGN KEY (tanque_id)
                REFERENCES tanques(id)
        )
        """
    )

    # --------------------------------------------------------
    # RELATÓRIOS
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS relatorios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanque_id INTEGER NOT NULL,
            data_gerado TEXT NOT NULL,
            temperatura REAL NOT NULL,
            ph REAL NOT NULL,
            oxigenio REAL NOT NULL,
            amonia REAL NOT NULL,
            nitrito REAL NOT NULL,
            FOREIGN KEY (tanque_id)
                REFERENCES tanques(id)
        )
        """
    )

    # --------------------------------------------------------
    # USUÁRIO ADMINISTRADOR
    # --------------------------------------------------------

    admin_existente = db.execute(
        """
        SELECT id
        FROM usuarios
        WHERE LOWER(email) = ?
        """,
        (ADMIN_EMAIL,),
    ).fetchone()

    if admin_existente:

        # Se o admin já existir, garante que ele seja administrador
        # e atualiza a senha para a senha configurada.
        db.execute(
            """
            UPDATE usuarios
            SET is_admin = 1,
                senha = ?
            WHERE id = ?
            """,
            (
                generate_password_hash(ADMIN_PASSWORD),
                admin_existente["id"],
            ),
        )

    else:

        # Se não existir, cria automaticamente.
        db.execute(
            """
            INSERT INTO usuarios
                (nome, email, senha, data_cadastro, is_admin)
            VALUES
                (?, ?, ?, ?, 1)
            """,
            (
                "Administrador",
                ADMIN_EMAIL,
                generate_password_hash(ADMIN_PASSWORD),
                date.today().isoformat(),
            ),
        )

    # --------------------------------------------------------
    # TANQUES ANTIGOS SEM USUÁRIO
    # --------------------------------------------------------

    primeiro_usuario = db.execute(
        """
        SELECT id
        FROM usuarios
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()

    if primeiro_usuario:
        db.execute(
            """
            UPDATE tanques
            SET usuario_id = ?
            WHERE usuario_id IS NULL
            """,
            (primeiro_usuario["id"],),
        )

    # --------------------------------------------------------
    # ÍNDICES
    # --------------------------------------------------------

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tanques_usuario
        ON tanques(usuario_id)
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mortalidades_tanque
        ON mortalidades(tanque_id)
        """
    )

    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_relatorios_tanque
        ON relatorios(tanque_id)
        """
    )

    db.commit()


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

            flash(
                "Faça login para acessar esta página.",
                "warning",
            )

            return redirect(
                url_for("login")
            )

        if not session.get("is_admin", False):

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

        db = get_db()

        usuario = db.execute(
            """
            SELECT *
            FROM usuarios
            WHERE LOWER(email) = ?
            """,
            (email,),
        ).fetchone()

        if (
            usuario
            and check_password_hash(
                usuario["senha"],
                senha
            )
        ):

            session.clear()

            session["usuario_id"] = usuario["id"]
            session["usuario"] = usuario["nome"]
            session["is_admin"] = bool(
                usuario["is_admin"]
            )
            session["ultima_atividade"] = time.time()

            if usuario["is_admin"]:
                return redirect(
                    url_for("admin")
                )

            return redirect(
                url_for("index")
            )

        registrar_tentativa_login(ip)

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

        if senha != confirmar_senha:

            flash(
                "As senhas não coincidem.",
                "danger",
            )

            return render_template(
                "cadastro.html"
            )

        if len(senha) < 6:

            flash(
                "A senha deve ter pelo menos 6 caracteres.",
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

        db = get_db()

        existente = db.execute(
            """
            SELECT id
            FROM usuarios
            WHERE LOWER(email) = ?
            """,
            (email,),
        ).fetchone()

        if existente:

            flash(
                "Este e-mail já está cadastrado.",
                "warning",
            )

            return render_template(
                "cadastro.html"
            )

        db.execute(
            """
            INSERT INTO usuarios
                (nome, email, senha, data_cadastro, is_admin)
            VALUES
                (?, ?, ?, ?, 0)
            """,
            (
                nome,
                email,
                generate_password_hash(senha),
                date.today().isoformat(),
            ),
        )

        db.commit()

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

@app.route("/perfil")
@login_required
def perfil():

    db = get_db()

    usuario = db.execute(
        """
        SELECT
            id,
            nome,
            email,
            data_cadastro,
            is_admin
        FROM usuarios
        WHERE id = ?
        """,
        (session["usuario_id"],),
    ).fetchone()

    return render_template(
        "perfil.html",
        usuario_perfil=usuario,
    )


# ============================================================
# PAINEL PRINCIPAL
# ============================================================

@app.route("/index")
@login_required
def index():

    if session.get("is_admin"):
        return redirect(url_for("admin"))

    db = get_db()

    tanques = db.execute(
        """
        SELECT *
        FROM tanques
        WHERE usuario_id = ?
        ORDER BY id DESC
        """,
        (session["usuario_id"],),
    ).fetchall()

    total_peixes = sum(
        tanque["quantidade_atual"]
        for tanque in tanques
    )

    estatisticas = {
        "tanques": len(tanques),
        "peixes": total_peixes,
        "capacidade": sum(
            float(tanque["capacidade"])
            for tanque in tanques
        ),
        "mortes": db.execute(
            """
            SELECT COALESCE(SUM(m.quantidade), 0) AS total
            FROM mortalidades m
            INNER JOIN tanques t
                ON t.id = m.tanque_id
            WHERE t.usuario_id = ?
            """,
            (session["usuario_id"],),
        ).fetchone()["total"],
    }

    mortes = db.execute(
        """
        SELECT
            m.*,
            t.nome AS tanque_nome
        FROM mortalidades m
        INNER JOIN tanques t
            ON t.id = m.tanque_id
        WHERE t.usuario_id = ?
        ORDER BY m.id DESC
        LIMIT 5
        """,
        (session["usuario_id"],),
    ).fetchall()

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

    db = get_db()

    usuarios = db.execute(
        """
        SELECT
            u.id,
            u.nome,
            u.email,
            u.data_cadastro,
            u.is_admin,
            COUNT(t.id) AS total_tanques,
            COALESCE(
                SUM(t.quantidade_atual),
                0
            ) AS total_peixes
        FROM usuarios u
        LEFT JOIN tanques t
            ON t.usuario_id = u.id
        GROUP BY
            u.id,
            u.nome,
            u.email,
            u.data_cadastro,
            u.is_admin
        ORDER BY u.id DESC
        """
    ).fetchall()

    total_tanques = db.execute(
        """
        SELECT COUNT(*) AS total
        FROM tanques
        """
    ).fetchone()["total"]

    total_peixes = db.execute(
        """
        SELECT
            COALESCE(
                SUM(quantidade_atual),
                0
            ) AS total
        FROM tanques
        """
    ).fetchone()["total"]

    return render_template(
        "admin.html",
        usuarios=usuarios,
        total_tanques=total_tanques,
        total_peixes=total_peixes,
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

    db = get_db()

    tanques = db.execute(
        """
        SELECT *
        FROM tanques
        WHERE usuario_id = ?
        ORDER BY nome
        """,
        (session["usuario_id"],),
    ).fetchall()

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

    db = get_db()

    relatorio_atual = db.execute(
        """
        SELECT
            r.*,
            t.nome AS tanque_nome
        FROM relatorios r
        INNER JOIN tanques t
            ON t.id = r.tanque_id
        WHERE r.id = ?
        AND t.usuario_id = ?
        """,
        (id, session["usuario_id"]),
    ).fetchone()

    if not relatorio_atual:
        flash("Relatório não encontrado.", "danger")
        return redirect(url_for("relatorio"))

    tanques = db.execute(
        """
        SELECT *
        FROM tanques
        WHERE usuario_id = ?
        ORDER BY nome
        """,
        (session["usuario_id"],),
    ).fetchall()

    relatorios = db.execute(
        """
        SELECT
            r.*,
            t.nome AS tanque_nome
        FROM relatorios r
        INNER JOIN tanques t
            ON t.id = r.tanque_id
        WHERE t.usuario_id = ?
        ORDER BY r.id DESC
        """,
        (session["usuario_id"],),
    ).fetchall()

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

    db = get_db()

    tanques = db.execute(
        """
        SELECT *
        FROM tanques
        WHERE usuario_id = ?
        ORDER BY nome
        """,
        (session["usuario_id"],),
    ).fetchall()

    if request.method == "POST":

        tanque_id = request.form.get(
            "tanque_id"
        )

        tanque = db.execute(
            """
            SELECT *
            FROM tanques
            WHERE id = ?
            AND usuario_id = ?
            """,
            (
                tanque_id,
                session["usuario_id"],
            ),
        ).fetchone()

        if not tanque:

            flash(
                "Tanque não encontrado.",
                "danger",
            )

            return redirect(
                url_for("relatorio")
            )

        db.execute(
            """
            INSERT INTO relatorios (
                tanque_id,
                data_gerado,
                temperatura,
                ph,
                oxigenio,
                amonia,
                nitrito
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tanque["id"],
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                tanque["temperatura"],
                tanque["ph"],
                tanque["oxigenio"],
                tanque["amonia"],
                tanque["nitrito"],
            ),
        )

        db.commit()

        flash(
            "Relatório gerado com sucesso.",
            "success",
        )

        return redirect(
            url_for("relatorio")
        )

    relatorios = db.execute(
        """
        SELECT
            r.*,
            t.nome AS tanque_nome
        FROM relatorios r
        INNER JOIN tanques t
            ON t.id = r.tanque_id
        WHERE t.usuario_id = ?
        ORDER BY r.id DESC
        """,
        (session["usuario_id"],),
    ).fetchall()

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

        db = get_db()

        db.execute(
            """
            INSERT INTO tanques (
                nome,
                capacidade,
                especie,
                data_cadastro,
                quantidade_inicial,
                quantidade_atual,
                temperatura,
                ph,
                oxigenio,
                amonia,
                nitrito,
                usuario_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome,
                float(capacidade),
                especie,
                date.today().isoformat(),
                int(quantidade_inicial),
                int(quantidade_inicial),
                0,
                0,
                0,
                0,
                0,
                session["usuario_id"],
            ),
        )

        db.commit()

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

    db = get_db()

    tanque = db.execute(
        """
        SELECT *
        FROM tanques
        WHERE id = ?
        AND usuario_id = ?
        """,
        (
            id,
            session["usuario_id"],
        ),
    ).fetchone()

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

        db.execute(
            """
            UPDATE tanques
            SET
                nome = ?,
                capacidade = ?,
                especie = ?,
                quantidade_atual = ?,
                temperatura = ?,
                ph = ?,
                oxigenio = ?,
                amonia = ?,
                nitrito = ?
            WHERE id = ?
            AND usuario_id = ?
            """,
            (
                nome,
                capacidade,
                especie,
                quantidade_atual,
                temperatura,
                ph,
                oxigenio,
                amonia,
                nitrito,
                id,
                session["usuario_id"],
            ),
        )

        db.commit()

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

    db = get_db()

    tanque = db.execute(
        """
        SELECT id
        FROM tanques
        WHERE id = ?
        AND usuario_id = ?
        """,
        (
            id,
            session["usuario_id"],
        ),
    ).fetchone()

    if not tanque:

        flash(
            "Tanque não encontrado.",
            "danger",
        )

        return redirect(
            url_for("index")
        )

    db.execute(
        """
        DELETE FROM mortalidades
        WHERE tanque_id = ?
        """,
        (id,),
    )

    db.execute(
        """
        DELETE FROM relatorios
        WHERE tanque_id = ?
        """,
        (id,),
    )

    db.execute(
        """
        DELETE FROM tanques
        WHERE id = ?
        AND usuario_id = ?
        """,
        (
            id,
            session["usuario_id"],
        ),
    )

    db.commit()

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

    db = get_db()

    tanques = db.execute(
        """
        SELECT *
        FROM tanques
        WHERE usuario_id = ?
        ORDER BY nome
        """,
        (session["usuario_id"],),
    ).fetchall()

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

        tanque = db.execute(
            """
            SELECT *
            FROM tanques
            WHERE id = ?
            AND usuario_id = ?
            """,
            (
                tanque_id,
                session["usuario_id"],
            ),
        ).fetchone()

        if not tanque:

            flash(
                "Tanque não encontrado.",
                "danger",
            )

            return redirect(
                url_for("mortalidade")
            )

        if quantidade > tanque["quantidade_atual"]:

            flash(
                "A mortalidade não pode ser maior que a quantidade atual de peixes.",
                "danger",
            )

            return render_template(
                "mortalidade.html",
                tanques=tanques,
            )

        db.execute(
            """
            INSERT INTO mortalidades (
                tanque_id,
                data,
                quantidade,
                observacao
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                tanque_id,
                data_mortalidade,
                quantidade,
                observacao,
            ),
        )

        db.execute(
            """
            UPDATE tanques
            SET quantidade_atual =
                quantidade_atual - ?
            WHERE id = ?
            """,
            (
                quantidade,
                tanque_id,
            ),
        )

        db.commit()

        flash(
            "Mortalidade registrada com sucesso.",
            "success",
        )

        return redirect(
            url_for("mortalidade")
        )

    registros = db.execute(
        """
        SELECT
            m.*,
            t.nome AS tanque_nome
        FROM mortalidades m
        INNER JOIN tanques t
            ON t.id = m.tanque_id
        WHERE t.usuario_id = ?
        ORDER BY m.id DESC
        """,
        (session["usuario_id"],),
    ).fetchall()

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
        "404.html"
    ), 404


# ============================================================
# ERRO 500
# ============================================================

@app.errorhandler(500)
def erro_servidor(error):

    return render_template(
        "500.html"
    ), 500


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    with app.app_context():
        criar_banco()

    app.run(
        debug=True
    )

