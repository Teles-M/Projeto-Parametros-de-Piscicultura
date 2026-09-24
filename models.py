from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship


db = SQLAlchemy()


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    senha: Mapped[str] = mapped_column(nullable=False)
    data_cadastro: Mapped[str] = mapped_column(nullable=False)

    tanques: Mapped[list["Tanque"]] = relationship(
        back_populates="usuario",
        passive_deletes=True,
    )


class Tanque(db.Model):
    __tablename__ = "tanques"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(nullable=False)
    capacidade: Mapped[float] = mapped_column(nullable=False)
    especie: Mapped[str] = mapped_column(nullable=False)
    data_cadastro: Mapped[str] = mapped_column(nullable=False)
    quantidade_inicial: Mapped[int] = mapped_column(nullable=False)
    quantidade_atual: Mapped[int] = mapped_column(nullable=False)
    temperatura: Mapped[float] = mapped_column(nullable=False, default=0)
    ph: Mapped[float] = mapped_column(nullable=False, default=0)
    oxigenio: Mapped[float] = mapped_column(nullable=False, default=0)
    amonia: Mapped[float] = mapped_column(nullable=False, default=0)
    nitrito: Mapped[float] = mapped_column(nullable=False, default=0)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))

    usuario: Mapped[Usuario | None] = relationship(back_populates="tanques")
    mortalidades: Mapped[list["Mortalidade"]] = relationship(
        back_populates="tanque",
        cascade="all, delete-orphan",
    )
    relatorios: Mapped[list["Relatorio"]] = relationship(
        back_populates="tanque",
        cascade="all, delete-orphan",
    )


class Mortalidade(db.Model):
    __tablename__ = "mortalidades"

    id: Mapped[int] = mapped_column(primary_key=True)
    tanque_id: Mapped[int] = mapped_column(ForeignKey("tanques.id"), nullable=False)
    data: Mapped[str] = mapped_column(nullable=False)
    quantidade: Mapped[int] = mapped_column(nullable=False)
    observacao: Mapped[str] = mapped_column(default="")

    tanque: Mapped[Tanque] = relationship(back_populates="mortalidades")


class Relatorio(db.Model):
    __tablename__ = "relatorios"

    id: Mapped[int] = mapped_column(primary_key=True)
    tanque_id: Mapped[int] = mapped_column(ForeignKey("tanques.id"), nullable=False)
    data_gerado: Mapped[str] = mapped_column(nullable=False)
    temperatura: Mapped[float] = mapped_column(nullable=False)
    ph: Mapped[float] = mapped_column(nullable=False)
    oxigenio: Mapped[float] = mapped_column(nullable=False)
    amonia: Mapped[float] = mapped_column(nullable=False)
    nitrito: Mapped[float] = mapped_column(nullable=False)

    tanque: Mapped[Tanque] = relationship(back_populates="relatorios")
