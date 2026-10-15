"""Real retrieval, used when MOCK_MODE=false. Owner: B1.

Planned pipeline (ARCHITECTURE.md section 4):
  1. metadata filter (make, model, year, system, section, language)
  2. dense search BGE-M3 in Qdrant (optionally hybrid with BM25, fused with RRF), top 20
  3. bge-reranker, keep top_k
  4. fallback if fewer than 2 good passages: drop model -> drop make -> generic.
     Report it in `fallback_level` so the agents can tell the user.

Must return a dict valid against contracts/rag_search.schema.json#/$defs/response.
"""


def search(query, filters, top_k=5, rerank=True):
    raise NotImplementedError("Keep MOCK_MODE=true until the Qdrant index is built.")
