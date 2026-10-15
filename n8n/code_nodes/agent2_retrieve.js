// Agent 2, node "Retrieve passages" (prefetch mode, Code node source). Owner: B2. Port of gather() in agent2_diagnosis/agent2.py.
// Used with small local models (Ollama): they cannot drive a tool-calling loop reliably, so the search runs here, in the
// workflow, and the model answers once (JSON mode) with the passages in its message. Same /search contract, same guardrails.
// Test: node n8n/code_nodes/test_agent2_nodes.js
//
// retrieve(prep, search, maxPassages)
//   prep   : output of "Prepare input" (queries, filters_json, system_prompt, user_message, top_k)
//   search : async (query, filters, top_k) => response of POST /search (contracts/rag_search.schema.json)
// returns  : prep + { full_prompt, observations, retrieval_level }; `observations` has the same shape as the agent's tool
//            results, so "Ground and decide" works unchanged (a cause or check can only cite a passage returned here).

async function retrieve(prep, search, maxPassages) {
  const LEVELS = ['exact_model', 'make', 'generic', 'none'];
  const filters = JSON.parse(prep.filters_json);
  const seen = new Set();
  const found = [];
  let level = 'exact_model';
  for (const q of prep.queries) {
    const res = await search(q, filters, prep.top_k || 3);
    if (LEVELS.indexOf(res.fallback_level) > LEVELS.indexOf(level)) level = res.fallback_level;
    for (const p of res.passages || []) {
      if (!seen.has(p.chunk_id)) {
        seen.add(p.chunk_id);
        found.push(p);
      }
    }
  }
  const passages = found.sort((a, b) => b.score - a.score).slice(0, maxPassages || 6);
  const context = passages.map((p) => `[${p.chunk_id}] (${p.source} p.${p.page}) ${p.text}`).join('\n\n');
  const full_prompt =
    prep.system_prompt + '\n\n' + prep.user_message + '\nRetrieval level: ' + level + '\nPassages:\n' + (context || '(none found)');
  return { ...prep, full_prompt, observations: [{ observation: { fallback_level: level, passages } }], retrieval_level: level };
}
