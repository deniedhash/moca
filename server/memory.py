import os
import pickle
from datetime import datetime

import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class MemoryRecord(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    category = Column(String, default="general")


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory", "moca.db")


class MOCAMemory:
    def __init__(self):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.engine = create_engine(f"sqlite:///{DB_PATH}")
        Base.metadata.create_all(self.engine)

    def add(self, text: str, category: str = "general"):
        embedding = self.model.encode(text)
        with Session(self.engine) as session:
            record = MemoryRecord(
                text=text,
                embedding=pickle.dumps(embedding),
                category=category,
            )
            session.add(record)
            session.commit()

    def search(self, query: str, limit: int = 5) -> list[str]:
        query_embedding = self.model.encode(query)
        with Session(self.engine) as session:
            records = session.query(MemoryRecord).all()
            if not records:
                return []
            scored = []
            for r in records:
                stored = pickle.loads(r.embedding)
                similarity = np.dot(query_embedding, stored) / (
                    np.linalg.norm(query_embedding) * np.linalg.norm(stored)
                )
                scored.append((similarity, r.text))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [text for _, text in scored[:limit]]

    def get_all(self) -> list[str]:
        with Session(self.engine) as session:
            records = session.query(MemoryRecord).order_by(MemoryRecord.timestamp.desc()).all()
            return [r.text for r in records]

    def clear(self):
        with Session(self.engine) as session:
            session.query(MemoryRecord).delete()
            session.commit()
