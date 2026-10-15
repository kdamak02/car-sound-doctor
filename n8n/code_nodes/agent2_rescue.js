// Agent 2, node "Rescue prompt" (Code node source). Owner: B2.
// Safety net for small local models: the AI Agent sometimes searches again and again and ends with an EMPTY answer
// (seen with qwen2.5:7b in pass 2: 3 identical searches, then nothing). The passages it retrieved are still in its
// intermediate steps, so one extra JSON-mode call can answer from them. The grounding rules after it are unchanged.
// Test: node n8n/code_nodes/test_agent2_nodes.js
//
// rescue(prep, steps, maxPassages)
//   prep  : output of "Prepare input" (system_prompt, user_message)
//   steps : the agent's intermediateSteps (tool results: {observation: JSON text or object})
// returns : { full_prompt, rescued_passages } for the "Rescue answer" chain

function rescue(prep, steps, maxPassages) {
  const byId = {};
  for (const step of steps || []) {
    let obs = step.observation;
    if (typeof obs === 'string') {
      try { obs = JSON.parse(obs); } catch (e) { continue; }
    }
    for (const p of (obs && obs.passages) || []) byId[p.chunk_id] = p;
  }
  const passages = Object.values(byId).sort((a, b) => b.score - a.score).slice(0, maxPassages || 6);
  const context = passages.map((p) => `[${p.chunk_id}] (${p.source} p.${p.page}) ${p.text}`).join('\n\n');
  const message = prep.user_message.replace(/Use the rag_search tool[^\n]*\n?/, ''); // the passages are given here instead
  const full_prompt = prep.system_prompt + '\n\n' + message + '\nPassages:\n' + (context || '(none found)') +
    '\n\nAnswer now with the JSON object only.';
  return { full_prompt, rescued_passages: passages.length };
}
