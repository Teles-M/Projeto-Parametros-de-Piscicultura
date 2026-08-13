from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def mortalidade():
    return render_template('mortalidade.html')

@app.route('/registrar', methods=['POST'])
def registrar():
    return "Registro de mortalidade recebido!"

if __name__ == '__main__':
    app.run(debug=True)