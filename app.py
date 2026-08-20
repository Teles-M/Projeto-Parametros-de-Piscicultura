from flask import Flask, render_template, request, redirect, url_for
import sqlite3

app = Flask(__name__)

@app.route('/')
def inicio():
    return render_template('login.html')

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/cadastro')
def cadastro():
    return render_template('cadastro.html')

@app.route('/index')
def index():
    return render_template('index.html')

# Criar banco
def criar_banco():

    banco = sqlite3.connect("banco.db")

    banco.execute("""
        CREATE TABLE IF NOT EXISTS tanques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            capacidade REAL NOT NULL,
            especie TEXT NOT NULL,
            data_cadastro TEXT NOT NULL,
            quantidade_inicial INTEGER NOT NULL,
            quantidade_atual INTEGER NOT NULL
        )
    """)

    banco.commit()
    banco.close()


# ==========================
# CADASTRAR TANQUE
# ==========================

@app.route("/cadastrar_tanque", methods=["GET", "POST"])
def cadastrar_tanque():

    if request.method == "POST":

        nome = request.form["nome"]
        capacidade = request.form["capacidade"]
        especie = request.form["especie"]
        data = request.form["data_cadastro"]
        quantidade = request.form["quantidade_inicial"]

        banco = sqlite3.connect("banco.db")

        banco.execute("""
            INSERT INTO tanques
            (nome, capacidade, especie, data_cadastro,
             quantidade_inicial, quantidade_atual)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            nome,
            capacidade,
            especie,
            data,
            quantidade,
            quantidade
        ))

        banco.commit()
        banco.close()

        # Depois de cadastrar, volta para a página inicial
        return redirect("/")

    return render_template("cadastro_tanque.html")


# ==========================
# EDITAR TANQUE
# ==========================

@app.route("/editar_tanque/<int:id>", methods=["GET", "POST"])
def editar_tanque(id):

    banco = sqlite3.connect("banco.db")
    banco.row_factory = sqlite3.Row

    if request.method == "POST":

        nome = request.form["nome"]
        capacidade = request.form["capacidade"]
        especie = request.form["especie"]
        data = request.form["data_cadastro"]
        quantidade = request.form["quantidade_atual"]

        banco.execute("""
            UPDATE tanques
            SET nome = ?,
                capacidade = ?,
                especie = ?,
                data_cadastro = ?,
                quantidade_atual = ?
            WHERE id = ?
        """, (
            nome,
            capacidade,
            especie,
            data,
            quantidade,
            id
        ))

        banco.commit()
        banco.close()

        # Depois de editar, volta para a página inicial
        return redirect("/")

    tanque = banco.execute(
        "SELECT * FROM tanques WHERE id = ?",
        (id,)
    ).fetchone()

    banco.close()

    return render_template(
        "editar_tanque.html",
        tanque=tanque
    )


# Criar banco quando iniciar o sistema
if __name__ == "__main__":
    criar_banco()
    app.run(debug=True)