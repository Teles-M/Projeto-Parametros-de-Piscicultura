-- Backup completo (tabelas, dados, rotinas e triggers):
-- mysqldump -u admin_db -p --routines --triggers --events --databases tambatanqui_db > backup_tambatanqui_db.sql
--
-- Execute este script com uma conta administrativa do MySQL.
-- Antes de usar, substitua as senhas abaixo por valores fortes e exclusivos.
-- Configure MYSQL_PASSWORD com a senha correspondente, MYSQL_DATABASE=tambatanqui_db
-- e MYSQL_HOST no arquivo .env do Flask. A aplicação fixa MYSQL_USER=app_user.

CREATE DATABASE IF NOT EXISTS tambatanqui_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'admin_db'@'localhost'
    IDENTIFIED BY 'SUBSTITUA_POR_SENHA_FORTE_DO_ADMIN';
CREATE USER IF NOT EXISTS 'app_user'@'localhost'
    IDENTIFIED BY 'SUBSTITUA_POR_SENHA_FORTE_DA_APLICACAO';

-- A conta administrativa recebe todos os privilégios no servidor MySQL.
GRANT ALL PRIVILEGES ON *.* TO 'admin_db'@'localhost' WITH GRANT OPTION;

-- O Flask pode apenas ler e manipular dados deste banco, sem DDL ou gestão de contas.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON tambatanqui_db.* TO 'app_user'@'localhost';

FLUSH PRIVILEGES;

USE tambatanqui_db;

-- Tabela de usuários. A coluna senha armazena somente hashes Werkzeug.
CREATE TABLE IF NOT EXISTS usuarios (
    id INT NOT NULL AUTO_INCREMENT,
    nome VARCHAR(120) NOT NULL,
    email VARCHAR(254) NOT NULL,
    senha VARCHAR(255) NOT NULL,
    data_cadastro VARCHAR(32) NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (id),
    UNIQUE KEY uq_usuarios_email (email)
) ENGINE=InnoDB;

-- Cada tanque pertence a um usuário; a FK protege a integridade referencial.
CREATE TABLE IF NOT EXISTS tanques (
    id INT NOT NULL AUTO_INCREMENT,
    nome VARCHAR(120) NOT NULL,
    capacidade DOUBLE NOT NULL,
    especie VARCHAR(120) NOT NULL,
    data_cadastro VARCHAR(32) NOT NULL,
    quantidade_inicial INT NOT NULL,
    quantidade_atual INT NOT NULL,
    temperatura DOUBLE NOT NULL DEFAULT 0,
    ph DOUBLE NOT NULL DEFAULT 0,
    oxigenio DOUBLE NOT NULL DEFAULT 0,
    amonia DOUBLE NOT NULL DEFAULT 0,
    nitrito DOUBLE NOT NULL DEFAULT 0,
    usuario_id INT NULL,
    PRIMARY KEY (id),
    KEY idx_tanques_usuario (usuario_id),
    CONSTRAINT fk_tanques_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

-- Mortes e relatórios mantêm relacionamento obrigatório com o tanque.
CREATE TABLE IF NOT EXISTS mortalidades (
    id INT NOT NULL AUTO_INCREMENT,
    tanque_id INT NOT NULL,
    data VARCHAR(32) NOT NULL,
    quantidade INT NOT NULL,
    observacao TEXT NULL,
    PRIMARY KEY (id),
    KEY idx_mortalidades_tanque (tanque_id),
    CONSTRAINT fk_mortalidades_tanque
        FOREIGN KEY (tanque_id) REFERENCES tanques (id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS relatorios (
    id INT NOT NULL AUTO_INCREMENT,
    tanque_id INT NOT NULL,
    data_gerado VARCHAR(32) NOT NULL,
    temperatura DOUBLE NOT NULL,
    ph DOUBLE NOT NULL,
    oxigenio DOUBLE NOT NULL,
    amonia DOUBLE NOT NULL,
    nitrito DOUBLE NOT NULL,
    PRIMARY KEY (id),
    KEY idx_relatorios_tanque (tanque_id),
    CONSTRAINT fk_relatorios_tanque
        FOREIGN KEY (tanque_id) REFERENCES tanques (id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;

-- A tabela mantém os valores antigos para análise posterior de alterações sensíveis.
CREATE TABLE IF NOT EXISTS auditoria_logs (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tabela VARCHAR(64) NOT NULL,
    registro_id INT NOT NULL,
    acao ENUM('UPDATE', 'DELETE') NOT NULL,
    dados_antigos JSON NOT NULL,
    data_evento TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_auditoria_tabela_registro (tabela, registro_id),
    KEY idx_auditoria_data_evento (data_evento)
) ENGINE=InnoDB;

DROP PROCEDURE IF EXISTS SP_CadastrarUsuario;
DROP TRIGGER IF EXISTS trg_usuarios_auditoria_update;
DROP TRIGGER IF EXISTS trg_usuarios_auditoria_delete;

DELIMITER //

-- Recebe explicitamente um hash (nunca a senha em texto puro) e rejeita
-- dados inválidos ou e-mails duplicados antes de inserir a conta.
CREATE PROCEDURE SP_CadastrarUsuario(
    IN p_nome VARCHAR(120),
    IN p_email VARCHAR(254),
    IN p_senha_hash VARCHAR(255),
    IN p_data_cadastro VARCHAR(32)
)
BEGIN
    IF p_nome IS NULL OR CHAR_LENGTH(TRIM(p_nome)) = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nome obrigatorio';
    END IF;

    IF p_email IS NULL OR LOCATE('@', p_email) < 2 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Email invalido';
    END IF;

    IF p_senha_hash IS NULL
        OR (
            p_senha_hash NOT LIKE 'scrypt:%'
            AND p_senha_hash NOT LIKE 'pbkdf2:%'
        ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Hash de senha invalido';
    END IF;

    IF p_data_cadastro IS NULL OR CHAR_LENGTH(p_data_cadastro) = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Data de cadastro obrigatoria';
    END IF;

    IF EXISTS (SELECT 1 FROM usuarios WHERE email = LOWER(TRIM(p_email))) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Email ja cadastrado';
    END IF;

    INSERT INTO usuarios (nome, email, senha, data_cadastro, is_admin)
    VALUES (TRIM(p_nome), LOWER(TRIM(p_email)), p_senha_hash, p_data_cadastro, FALSE);
END//

-- Os triggers guardam os dados antigos, omitindo o hash de senha para reduzir
-- o risco de exposição de credenciais em arquivos de auditoria.
CREATE TRIGGER trg_usuarios_auditoria_update
AFTER UPDATE ON usuarios
FOR EACH ROW
BEGIN
    INSERT INTO auditoria_logs (tabela, registro_id, acao, dados_antigos)
    VALUES (
        'usuarios',
        OLD.id,
        'UPDATE',
        JSON_OBJECT(
            'nome', OLD.nome,
            'email', OLD.email,
            'data_cadastro', OLD.data_cadastro,
            'is_admin', OLD.is_admin
        )
    );
END//

CREATE TRIGGER trg_usuarios_auditoria_delete
AFTER DELETE ON usuarios
FOR EACH ROW
BEGIN
    INSERT INTO auditoria_logs (tabela, registro_id, acao, dados_antigos)
    VALUES (
        'usuarios',
        OLD.id,
        'DELETE',
        JSON_OBJECT(
            'nome', OLD.nome,
            'email', OLD.email,
            'data_cadastro', OLD.data_cadastro,
            'is_admin', OLD.is_admin
        )
    );
END//

DELIMITER ;
