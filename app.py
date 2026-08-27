from datetime import date
from functools import wraps
from pathlib import Path
import sqlite3

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "banco.db"
app = Flask(__name__)
app.config["SECRET_KEY"] = "tamba-tanqui-dev-key"

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
        quantidade_atual INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS mortalidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tanque_id INTEGER NOT NULL,
        data TEXT NOT NULL, quantidade INTEGER NOT NULL,
        observacao TEXT DEFAULT '',
        FOREIGN KEY (tanque_id) REFERENCES tanques(id) ON DELETE CASCADE)""")
    db.commit()
    db.close()

criar_banco()

def validar_tanque(form, atual=False):
    try:
        quantidade = int(form["quantidade_atual" if atual else "quantidade_inicial"])
        dados = (form["nome"].strip(), float(form["capacidade"]), form["especie"].strip(), form["data_cadastro"], quantidade)
        if not dados[0] or not dados[2] or not dados[3] or dados[1] <= 0 or quantidade < 0:
            raise ValueError
        return dados
    except (KeyError, TypeError, ValueError):
        return None

@app.context_processor
def template_globals():
    return {"today": date.today().isoformat(), "usuario": session.get("usuario")}

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
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        usuario = get_db().execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
        if usuario is None or not check_password_hash(usuario["senha"], senha):
            flash("E-mail ou senha inválidos.", "error")
        else:
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["usuario"] = usuario["nome"]
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
    return render_template("index.html", tanques=tanques, mortes=mortes, estatisticas=estatisticas)

@app.route("/cadastrar_tanque", methods=["GET", "POST"])
@login_required
def cadastrar_tanque():
    if request.method == "POST":
        dados = validar_tanque(request.form)
        if not dados:
            flash("Preencha os campos com valores válidos.", "error")
        else:
            get_db().execute("""INSERT INTO tanques (nome, capacidade, especie, data_cadastro,
                quantidade_inicial, quantidade_atual) VALUES (?, ?, ?, ?, ?, ?)""", (*dados, dados[4]))
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
                quantidade_atual=? WHERE id=?""", (*dados[:4], dados[4], id))
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
