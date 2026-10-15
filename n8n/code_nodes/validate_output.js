// n8n Code node ("Run Once for Each Item"), placed right after Agent 2 or Agent 3.
// Same rules as agent3_repair/guardrails/checks.py (R2, R3, R4). Keep both in sync.
//
// Expects on the item:
//   $json.agent     : "agent2_output" | "agent3_output"
//   $json.output    : the LLM answer, already parsed as JSON
//   $json.retrieved : array of chunk_id returned by /search for this case
// Returns the item with { valid: bool, problems: [...] } so an IF node can retry once
// and then fall back to the "insufficient information" answer.

const SAFETY = /frein|brake|direction|steering|surchauffe|overheat|temp[ée]rature/i;
const KEEP_DRIVING = /continuer [àa] rouler|vous pouvez rouler|keep driving|safe to drive|pouvez continuer/i;

const agent = $json.agent;
const out = $json.output || {};
const retrieved = new Set($json.retrieved || []);
const problems = [];

const items = agent === 'agent2_output' ? (out.checks || []) : (out.steps || []);
for (const it of items) {
  const who = it.id || `step ${it.n}`;
  const c = it.citation || {};
  if (!c.chunk_id) problems.push(`R2 ${who}: no citation`);
  else if (!retrieved.has(c.chunk_id)) problems.push(`R2 ${who}: cites ${c.chunk_id}, not retrieved`);
}

const text = JSON.stringify(out);
const firstCause = (out.causes && out.causes[0] && out.causes[0].name) || '';
const safety = SAFETY.test(out.fault || '') || SAFETY.test(firstCause);
if (safety && KEEP_DRIVING.test(text)) problems.push('R3 safety fault: suggests the car can be driven');

if (agent === 'agent3_output') {
  if (safety && !(out.safety_warnings || []).length) problems.push('R3 safety fault without safety_warnings');
  if ((out.confidence === 'low' || out.difficulty === 'mechanic_only') && !out.see_mechanic)
    problems.push('R4 low confidence but see_mechanic is false');
  if (out.status === 'ok' && !(out.steps || []).length) problems.push('R1 status ok but no steps');
}

return { json: { ...$json, valid: problems.length === 0, problems } };
