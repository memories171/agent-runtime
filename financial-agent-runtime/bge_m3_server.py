"""本地 BGE-M3 推理服务（宿主机运行，容器经 host.docker.internal 调用）。

启动：python bge_m3_server.py
端口：8095
接口：
  POST /embed   {"texts": [...]} -> {"vectors": [[...], ...], "dimension": 1024}
  POST /rerank  {"query": "...", "texts": [...]} -> {"scores": [...], "indices": [...]}
rerank 使用 BGE-M3 token embedding 的 ColBERT MaxSim 打分，无需额外模型文件。
"""
from __future__ import annotations

import os

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_PATH = os.environ.get(
    "BGE_M3_MODEL_PATH",
    os.path.expanduser(r"~\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots"),
)
# sentence-transformers 可直接接受快照目录的父路径；若指定 snapshots 则取唯一子目录。
if os.path.isdir(MODEL_PATH):
    children = [os.path.join(MODEL_PATH, name) for name in os.listdir(MODEL_PATH)]
    snapshots = [child for child in children if os.path.isdir(child)]
    if snapshots and os.path.basename(MODEL_PATH) == "snapshots":
        MODEL_PATH = snapshots[0]

app = FastAPI(title="BGE-M3 Embedding & Rerank Service")
model = SentenceTransformer(MODEL_PATH)
_MAX_SEQ_LENGTH = getattr(model, "max_seq_length", 512)


def _token_embeddings(texts: list[str]):
    embeddings = model.encode(
        texts,
        output_value="token_embeddings",
        convert_to_tensor=True,
        batch_size=16,
        show_progress_bar=False,
    )
    if not isinstance(embeddings, list):
        embeddings = [embeddings]
    masks = []
    special_ids = set(model.tokenizer.all_special_ids)
    for text in texts:
        ids = model.tokenizer.encode(
            text,
            add_special_tokens=True,
            truncation=True,
            max_length=_MAX_SEQ_LENGTH,
        )
        mask = torch.tensor(
            [token_id not in special_ids for token_id in ids],
            dtype=torch.bool,
        )
        if mask.shape[0] != embeddings[len(masks)].shape[0]:
            raise RuntimeError("token embedding and token id lengths do not match")
        masks.append(mask)
    return embeddings, masks


def _maxsim_score(query_embedding, doc_embedding, query_mask, doc_mask) -> float:
    query = torch.nn.functional.normalize(query_embedding[query_mask], dim=-1)
    doc = torch.nn.functional.normalize(doc_embedding[doc_mask], dim=-1)
    if query.shape[0] == 0 or doc.shape[0] == 0:
        return 0.0
    return float((query @ doc.T).max(dim=1).values.mean())


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    if not texts:
        return []
    embeddings, masks = _token_embeddings([query, *texts])
    with torch.no_grad():
        return [
            _maxsim_score(embeddings[0], embeddings[index + 1], masks[0], masks[index + 1])
            for index in range(len(texts))
        ]


class EmbedRequest(BaseModel):
    texts: list[str]


class RerankRequest(BaseModel):
    query: str
    texts: list[str]


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_PATH, "dimension": model.get_sentence_embedding_dimension()}


@app.post("/embed")
def embed(request: EmbedRequest):
    vectors = model.encode(request.texts, normalize_embeddings=True, batch_size=16, show_progress_bar=False)
    return {
        "vectors": [vector.tolist() for vector in vectors],
        "dimension": model.get_sentence_embedding_dimension(),
    }


@app.post("/rerank")
def rerank(request: RerankRequest):
    scores = rerank_scores(request.query, request.texts)
    return {
        "scores": scores,
        "indices": sorted(range(len(scores)), key=lambda index: scores[index], reverse=True),
        "model": MODEL_PATH,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8095)
