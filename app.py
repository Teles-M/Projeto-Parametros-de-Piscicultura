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

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "banco.db"
load_dotenv(BASE_DIR / ".env")
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
SESSION_TIMEOUT_SECONDS = 30 * 60
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 15 * 60
login_attempts = defaultdict(deque)
login_attempts_lock = Lock()

PARAMETROS = {
    "temperatura": {"nome": "Temperatura", "unidade": "°C", "min": 25, "max": 30},
    "ph": {"nome": "pH", "unidade": "", "min": 6.5, "max": 8},
    "oxigenio": {"nome": "Oxigênio dissolvido", "unidade": "mg/L", "min": 5, "max": None},
    "amonia": {"nome": "Amônia tóxica", "unidade": "mg/L", "min": None, "max": 0.1},
    "nitrito": {"nome": "Nitrito", "unidade": "mg/L", "min": None, "max": 0.5},
}

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def criar_banco():
    db = sqlite3.connect(DATABASE)
    db.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE, senha TEXT NOT NULL,
        data_cadastro TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS tanques (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
        capacidade REAL NOT NULL, especie TEXT NOT NULL,
        data_cadastro TEXT NOT NULL, quantidade_inicial INTEGER NOT NULL,
        quantidade_atual INTEGER NOT NULL, temperatura REAL NOT NULL DEFAULT 0,
        ph REAL NOT NULL DEFAULT 0, oxigenio REAL NOT NULL DEFAULT 0,
        amonia REAL NOT NULL DEFAULT 0, nitrito REAL NOT NULL DEFAULT 0)""")
    colunas = {linha[1] for linha in db.execute("PRAGMA table_info(tanques)")}
    for nome in PARAMETROS:
        if nome not in colunas:
            db.execute(f"ALTER TABLE tanques ADD COLUMN {nome} REAL NOT NULL DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS mortalidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tanque_id INTEGER NOT NULL,
        data TEXT NOT NULL, quantidade INTEGER NOT NULL,
        observacao TEXT DEFAULT '',
        FOREIGN KEY (tanque_id) REFERENCES tanques(id) ON DELETE CASCADE)""")
    db.execute("""CREATE TABLE IF NOT EXISTS relatorios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tanque_id INTEGER NOT NULL,
        data_gerado TEXT NOT NULL, temperatura REAL NOT NULL,
        ph REAL NOT NULL, oxigenio REAL NOT NULL, amonia REAL NOT NULL,
        nitrito REAL NOT NULL,
        FOREIGN KEY (tanque_id) REFERENCES tanques(id) ON DELETE CASCADE)""")
    db.commit()
    db.close()

criar_banco()

def validar_tanque(form, atual=False):
    try:
        quantidade = int(form["quantidade_atual" if atual else "quantidade_inicial"])
        parametros = tuple(float(form[nome]) for nome in PARAMETROS)
        dados = (form["nome"].strip(), float(form["capacidade"]), form["especie"].strip(), form["data_cadastro"], quantidade, *parametros)
        if not dados[0] or not dados[2] or not dados[3] or dados[1] <= 0 or quantidade < 0 or any(valor < 0 for valor in parametros):
            raise ValueError
        return dados
    except (KeyError, TypeError, ValueError):
        return None

def avaliar_parametro(chave, valor):
    if chave == "temperatura":
        if valor < 22 or valor > 32:
            return "critico", "Fora da faixa segura"
        if valor < 25 or valor > 30:
            return "atencao", "Fora da faixa ideal"
    elif chave == "ph":
        if valor < 6 or valor > 9:
            return "critico", "Fora da faixa segura"
        if valor < 6.5 or valor > 8:
            return "atencao", "Fora da faixa ideal"
    elif chave == "oxigenio":
        if valor < 4:
            return "critico", "Abaixo do mínimo"
        if valor <= 5:
            return "atencao", "Abaixo do manejo ideal"
    elif chave == "amonia":
        if valor > 0.2:
            return "critico", "Acima do limite crítico"
        if valor >= 0.1:
            return "atencao", "Acima do recomendado"
    elif chave == "nitrito":
        if valor >= 0.5:
            return "critico", "Acima do limite seguro"
        if valor > 0:
            return "atencao", "Acima do cenário ideal"
    return "ok", "Dentro do recomendado"

def validar_parametros(form):
    try:
        valores = {chave: float(form[chave]) for chave in PARAMETROS}
        if any(valor < 0 for valor in valores.values()):
            raise ValueError
        return valores
    except (KeyError, TypeError, ValueError):
        return None

@app.context_processor
def template_globals():
    return {"today": date.today().isoformat(), "usuario": session.get("usuario"), "avaliar_parametro": avaliar_parametro}

def login_limit_keys(email):
    return (f"ip:{request.remote_addr or 'desconhecido'}", f"email:{email}")

def login_is_limited(keys, now=None):
    now = time.monotonic() if now is None else now
    with login_attempts_lock:
        for key in keys:
            tentativas = login_attempts[key]
            while tentativas and now - tentativas[0] >= LOGIN_RATE_WINDOW_SECONDS:
                tentativas.popleft()
            if len(tentativas) >= LOGIN_RATE_LIMIT:
                return True
    return False

def register_login_failure(keys, now=None):
    now = time.monotonic() if now is None else now
    with login_attempts_lock:
        for key in keys:
            tentativas = login_attempts[key]
            while tentativas and now - tentativas[0] >= LOGIN_RATE_WINDOW_SECONDS:
                tentativas.popleft()
            tentativas.append(now)

def clear_login_failures(keys):
    with login_attempts_lock:
        for key in keys:
            login_attempts.pop(key, None)

@app.before_request
def enforce_session_timeout():
    if "usuario_id" not in session:
        return None
    agora = time.time()
    ultima_atividade = session.get("ultima_atividade", agora)
    if agora - ultima_atividade > SESSION_TIMEOUT_SECONDS:
        session.clear()
        flash("Sua sessão expirou por inatividade. Entre novamente.", "error")
        return redirect(url_for("login", next=request.path))
    session["ultima_atividade"] = agora
    return None

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "usuario_id" not in session:
            flash("Entre na sua conta para acessar o painel.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        flash("Você já está logado. Redirecionando para o painel.", "success")
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        chaves_limite = login_limit_keys(email)
        if login_is_limited(chaves_limite):
            resposta = render_template("login.html")
            return resposta, 429, {"Retry-After": str(LOGIN_RATE_WINDOW_SECONDS)}
        usuario = get_db().execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
        if usuario is None or not check_password_hash(usuario["senha"], senha):
            register_login_failure(chaves_limite)
            flash("E-mail ou senha inválidos.", "error")
        else:
            clear_login_failures(chaves_limite)
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["usuario"] = usuario["nome"]
            session["ultima_atividade"] = time.time()
            destino = request.form.get("next") or url_for("index")
            return redirect(destino if destino.startswith("/") else url_for("index"))
    return render_template("login.html")

@app.get("/logout")
def logout():
    session.clear()
    flash("Você saiu da sua conta.", "success")
    return redirect(url_for("home"))

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        confirmacao = request.form.get("confirmacao", "")
        if not nome or "@" not in email or len(senha) < 6 or senha != confirmacao:
            flash("Informe um nome, um e-mail válido e uma senha de 6 caracteres ou mais. As senhas devem ser iguais.", "error")
        else:
            try:
                db = get_db()
                db.execute("INSERT INTO usuarios (nome, email, senha, data_cadastro) VALUES (?, ?, ?, ?)",
                           (nome, email, generate_password_hash(senha), date.today().isoformat()))
                db.commit()
            except sqlite3.IntegrityError:
                flash("Este e-mail já está cadastrado.", "error")
            else:
                flash("Conta criada com sucesso. Agora entre para acessar o painel.", "success")
                return redirect(url_for("login"))
    return render_template("cadastro.html")

@app.route("/index")
@login_required
def index():
    db = get_db()
    tanques = db.execute("SELECT * FROM tanques ORDER BY id DESC").fetchall()
    mortes = db.execute("""SELECT m.*, t.nome AS tanque_nome FROM mortalidades m
        JOIN tanques t ON t.id = m.tanque_id ORDER BY m.data DESC, m.id DESC LIMIT 5""").fetchall()
    estatisticas = {"tanques": len(tanques), "peixes": sum(t["quantidade_atual"] for t in tanques),
                    "capacidade": sum(t["capacidade"] for t in tanques),
                    "mortes": db.execute("SELECT COALESCE(SUM(quantidade), 0) FROM mortalidades").fetchone()[0]}
    return render_template("index.html", tanques=tanques, mortes=mortes, estatisticas=estatisticas, parametros=PARAMETROS)

@app.get("/api/clima")
@login_required
def clima():
    try:
        latitude = float(request.args["latitude"])
        longitude = float(request.args["longitude"])
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify(erro="Localização inválida."), 400

    parametros = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
        "timezone": "auto",
    })
    requisicao = Request(
        f"https://api.open-meteo.com/v1/forecast?{parametros}",
        headers={"User-Agent": "TambaTanqui/1.0"},
    )
    try:
        with urlopen(requisicao, timeout=8) as resposta:
            dados = json.load(resposta)
    except (OSError, URLError, json.JSONDecodeError):
        return jsonify(erro="Não foi possível consultar os dados meteorológicos agora."), 502

    atual = dados.get("current", {})
    return jsonify({
        "latitude": dados.get("latitude", latitude),
        "longitude": dados.get("longitude", longitude),
        "timezone": dados.get("timezone", "local"),
        "atualizado_em": atual.get("time"),
        "temperatura": atual.get("temperature_2m"),
        "sensacao": atual.get("apparent_temperature"),
        "umidade": atual.get("relative_humidity_2m"),
        "vento": atual.get("wind_speed_10m"),
        "codigo_tempo": atual.get("weather_code"),
    })

@app.route("/comparar")
@login_required
def comparar():
    tanques = get_db().execute("SELECT * FROM tanques ORDER BY nome").fetchall()
    return render_template("comparar.html", tanques=tanques, parametros=PARAMETROS)

@app.route("/relatorio", methods=["GET", "POST"])
@login_required
def relatorio():
    db = get_db()
    tanques = db.execute("SELECT id, nome, especie FROM tanques ORDER BY nome").fetchall()
    relatorio_atual = None
    if request.method == "POST":
        try:
            tanque_id = int(request.form["tanque_id"])
        except (KeyError, TypeError, ValueError):
            tanque_id = None
        valores = validar_parametros(request.form)
        tanque = db.execute("SELECT id, nome, especie FROM tanques WHERE id = ?", (tanque_id,)).fetchone()
        if tanque is None or valores is None:
            flash("Selecione um tanque e informe todos os parâmetros com valores válidos.", "error")
        else:
            data_gerado = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = db.execute("""INSERT INTO relatorios
                (tanque_id, data_gerado, temperatura, ph, oxigenio, amonia, nitrito)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tanque_id, data_gerado, *(valores[chave] for chave in PARAMETROS)))
            db.commit()
            flash("Relatório gerado e salvo no histórico.", "success")
            return redirect(url_for("relatorio", id=cursor.lastrowid))
    relatorios = db.execute("""SELECT r.*, t.nome AS tanque_nome
        FROM relatorios r JOIN tanques t ON t.id = r.tanque_id
        ORDER BY r.data_gerado DESC, r.id DESC""").fetchall()
    relatorio_id = request.args.get("id", type=int)
    if relatorio_id is not None:
        relatorio_atual = db.execute("""SELECT r.*, t.nome AS tanque_nome, t.especie
            FROM relatorios r JOIN tanques t ON t.id = r.tanque_id WHERE r.id = ?""",
            (relatorio_id,)).fetchone()
    return render_template("relatorio.html", tanques=tanques, relatorios=relatorios,
                           relatorio_atual=relatorio_atual, parametros=PARAMETROS,
                           avaliar_parametro=avaliar_parametro)

@app.route("/cadastrar_tanque", methods=["GET", "POST"])
@login_required
def cadastrar_tanque():
    if request.method == "POST":
        dados = validar_tanque(request.form)
        if not dados:
            flash("Preencha os campos com valores válidos.", "error")
        else:
            get_db().execute("""INSERT INTO tanques (nome, capacidade, especie, data_cadastro,
                quantidade_inicial, quantidade_atual, temperatura, ph, oxigenio, amonia, nitrito)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*dados, dados[4]))
            get_db().commit()
            flash("Tanque cadastrado com sucesso!", "success")
            return redirect(url_for("index"))
    return render_template("cadastro_tanque.html")

@app.route("/editar_tanque/<int:id>", methods=["GET", "POST"])
@login_required
def editar_tanque(id):
    db = get_db()
    tanque = db.execute("SELECT * FROM tanques WHERE id = ?", (id,)).fetchone()
    if tanque is None:
        flash("Tanque não encontrado.", "error")
        return redirect(url_for("index"))
    if request.method == "POST":
        dados = validar_tanque(request.form, atual=True)
        if not dados:
            flash("Preencha os campos com valores válidos.", "error")
        else:
            db.execute("""UPDATE tanques SET nome=?, capacidade=?, especie=?, data_cadastro=?,
                quantidade_atual=?, temperatura=?, ph=?, oxigenio=?, amonia=?, nitrito=? WHERE id=?""",
                        (*dados[:4], dados[4], *dados[5:], id))
            db.commit()
            flash("Tanque atualizado com sucesso!", "success")
            return redirect(url_for("index"))
    return render_template("editar_tanque.html", tanque=tanque)

@app.post("/excluir_tanque/<int:id>")
@login_required
def excluir_tanque(id):
    db = get_db()
    db.execute("DELETE FROM mortalidades WHERE tanque_id = ?", (id,))
    cursor = db.execute("DELETE FROM tanques WHERE id = ?", (id,))
    db.commit()
    flash("Tanque excluído." if cursor.rowcount else "Tanque não encontrado.", "success" if cursor.rowcount else "error")
    return redirect(url_for("index"))

@app.route("/mortalidade", methods=["GET", "POST"])
@login_required
def mortalidade():
    db = get_db()
    tanques = db.execute("SELECT id, nome FROM tanques ORDER BY nome").fetchall()
    if request.method == "POST":
        try:
            tanque_id, quantidade = int(request.form["tanque_id"]), int(request.form["quantidade"])
            data, observacao = request.form["data"], request.form.get("observacao", "").strip()
            tanque = db.execute("SELECT * FROM tanques WHERE id = ?", (tanque_id,)).fetchone()
            if not tanque or quantidade <= 0 or quantidade > tanque["quantidade_atual"] or not data:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            flash("Confira o tanque, a data e a quantidade informada.", "error")
        else:
            db.execute("INSERT INTO mortalidades (tanque_id, data, quantidade, observacao) VALUES (?, ?, ?, ?)", (tanque_id, data, quantidade, observacao))
            db.execute("UPDATE tanques SET quantidade_atual = quantidade_atual - ? WHERE id = ?", (quantidade, tanque_id))
            db.commit()
            flash("Mortalidade registrada e estoque atualizado.", "success")
            return redirect(url_for("mortalidade"))
    registros = db.execute("""SELECT m.*, t.nome AS tanque_nome FROM mortalidades m
        JOIN tanques t ON t.id = m.tanque_id ORDER BY m.data DESC, m.id DESC""").fetchall()
    return render_template("mortalidade.html", tanques=tanques, registros=registros)

@app.errorhandler(404)
def pagina_nao_encontrada(_erro):
    return render_template("errors/404.html"), 404

@app.errorhandler(500)
def erro_interno(_erro):
    return render_template("errors/500.html"), 500

if __name__ == "__main__":
    criar_banco()
    app.run(debug=True)
