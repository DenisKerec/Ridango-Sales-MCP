"""SQLAlchemy ORM for the kb2 schema.

Mirrors knowledge-base-2/SCHEMA.sql exactly. Embeddings are TEXT (JSON-encoded
list[float]) until pgvector is enabled — a non-breaking ALTER COLUMN TYPE
migration when v1.1 lands.

Convention:
- `kind` columns are TEXT (no DB enum); validated via StrEnums below.
- The Python attribute `metadata_` maps to the DB column `metadata` (the bare name
  collides with SQLAlchemy's `MetaData` attribute on `DeclarativeBase`).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

KB2_SCHEMA = "kb2"


class Base(DeclarativeBase):
    pass


# ── App-layer vocabularies ────────────────────────────────────────────────────


class EntityKind(StrEnum):
    PRODUCT = "product"
    CAPABILITY = "capability"
    STANDARD = "standard"
    LEGAL_REQUIREMENT = "legal_requirement"
    COMPANY = "company"
    PERSON = "person"
    TENDER = "tender"
    DOCUMENT = "document"
    CLAUSE = "clause"
    HARDWARE = "hardware"
    MODULE = "module"
    TOPIC = "topic"


class RelationKind(StrEnum):
    MEETS_STANDARD = "meets_standard"
    COMPLIES_WITH = "complies_with"
    SATISFIES_LEGAL = "satisfies_legal"
    HAS_CUSTOMER = "has_customer"
    WON_BY = "won_by"
    ISSUED_BY = "issued_by"
    GAP_FOR = "gap_for"
    CITES = "cites"
    PART_OF = "part_of"
    DEPENDS_ON = "depends_on"
    COMPETITOR_OF = "competitor_of"
    SUBSIDIARY_OF = "subsidiary_of"
    ACQUIRED_BY = "acquired_by"
    DEFINED_IN = "defined_in"
    SUPERSEDES = "supersedes"


class Classification(StrEnum):
    FIT = "FIT"
    PARTIAL_FIT = "PARTIAL_FIT"
    GAP = "GAP"
    UNCONFIRMED = "UNCONFIRMED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ── Tables ────────────────────────────────────────────────────────────────────


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_kb2_kb_slug"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    entities: Mapped[list["Entity"]] = relationship(
        back_populates="kb", cascade="all, delete-orphan"
    )


class Entity(Base):
    __tablename__ = "entity"
    __table_args__ = (
        UniqueConstraint("kb_id", "slug", name="uq_kb2_entity_kb_slug"),
        Index("ix_kb2_entity_kb", "kb_id"),
        Index("ix_kb2_entity_kb_kind", "kb_id", "kind"),
        Index("ix_kb2_entity_classification", "classification"),
        Index("ix_kb2_entity_body_tsv", "body_tsv", postgresql_using="gin"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{KB2_SCHEMA}.knowledge_base.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    classification: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[str | None] = mapped_column(String(10))
    body_md: Mapped[str | None] = mapped_column(Text)
    body_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(body_md, ''))",
            persisted=True,
        ),
    )
    properties: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    kb: Mapped[KnowledgeBase] = relationship(back_populates="entities")
    out_relations: Mapped[list["Relation"]] = relationship(
        "Relation",
        foreign_keys="Relation.src_id",
        back_populates="src",
        cascade="all, delete-orphan",
    )
    in_relations: Mapped[list["Relation"]] = relationship(
        "Relation",
        foreign_keys="Relation.dst_id",
        back_populates="dst",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    aliases: Mapped[list["Alias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class Relation(Base):
    __tablename__ = "relation"
    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "kind", name="uq_kb2_relation_triple"),
        Index("ix_kb2_relation_src_kind", "src_id", "kind"),
        Index("ix_kb2_relation_dst_kind", "dst_id", "kind"),
        CheckConstraint("src_id <> dst_id", name="ck_kb2_relation_no_self_loop"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    src_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.entity.id", ondelete="CASCADE"), nullable=False
    )
    dst_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.entity.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    weight: Mapped[float | None] = mapped_column(Numeric(6, 3))
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.entity.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    src: Mapped[Entity] = relationship(
        Entity, foreign_keys=[src_id], back_populates="out_relations"
    )
    dst: Mapped[Entity] = relationship(
        Entity, foreign_keys=[dst_id], back_populates="in_relations"
    )
    source_doc: Mapped[Entity | None] = relationship(Entity, foreign_keys=[source_id])


class Chunk(Base):
    __tablename__ = "chunk"
    __table_args__ = (
        Index("ix_kb2_chunk_entity_ord", "entity_id", "ord"),
        Index("ix_kb2_chunk_parent", "parent_id"),
        Index("ix_kb2_chunk_body_tsv", "body_tsv", postgresql_using="gin"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.entity.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.chunk.id", ondelete="CASCADE")
    )
    level: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_name: Mapped[str | None] = mapped_column(String(500))
    document_type: Mapped[str | None] = mapped_column(String(60))
    source_filename: Mapped[str | None] = mapped_column(String(1000))
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    body_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(body_md, ''))", persisted=True),
    )
    embedding: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[Entity] = relationship(back_populates="chunks")


class Alias(Base):
    __tablename__ = "alias"
    __table_args__ = (
        UniqueConstraint("kb_id", "alias", name="uq_kb2_alias_kb_text"),
        Index("ix_kb2_alias_entity", "entity_id"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{KB2_SCHEMA}.knowledge_base.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.entity.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(300), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(4, 3), default=1.0, nullable=False)

    entity: Mapped[Entity] = relationship(back_populates="aliases")


class BlobMeta(Base):
    __tablename__ = "blob_meta"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_kb2_blob_meta_sha256"),
        Index("ix_kb2_blob_meta_entity", "entity_id"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{KB2_SCHEMA}.entity.id", ondelete="CASCADE"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_run"
    __table_args__ = (
        Index("ix_kb2_ingestion_run_kb_started", "kb_id", "started_at"),
        {"schema": KB2_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{KB2_SCHEMA}.knowledge_base.id", ondelete="CASCADE"),
        nullable=False,
    )
    adapter: Mapped[str] = mapped_column(String(80), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    rows_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    log: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
