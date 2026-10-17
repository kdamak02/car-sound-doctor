// Agent 2 -> Agent 3 hand-over, node "To Agent 3" (Code node source). Owner: B2. Port of agent2_diagnosis/to_agent3.py (same tests).
// toAgent3(c, out): c = case record (with `diagnosis` = earlier Agent 2 outputs and `check_answers`), out = the last agent2_output.
// Returns the input of Agent 3 (agent3_repair/schemas/agent3_input.schema.json).
// Status: conclusive -> "confirmed"; no more checks and still not conclusive -> "inconclusive"; otherwise "probable".

function toAgent3(c, out) {
  const ui = c.user_input || {}, v = c.vehicle || {}, audio = c.audio || {};
  const top = Object.fromEntries((audio.top3 || []).map((t) => [t.label, t.prob]));
  const answers = Object.fromEntries((c.check_answers || []).map((a) => [a.check_id, a.answer || 'Not checked']));
  const asked = (c.diagnosis || []).flatMap((d) => d.checks || []);
  const status = out.conclusive ? 'confirmed' : (out.checks && out.checks.length ? 'probable' : 'inconclusive');
  const internal = /^((passage )?[\w.-]+_p\d+_c\d+|passage \S+|obdex_\S+)$/;
  return {
    case_id: c.case_id,
    language: ['fr', 'en'].includes(ui.language) ? ui.language : 'fr',
    vehicle: { make: v.make || '', model: v.model || '', year: parseInt(v.year || 0, 10), engine: v.fuel || null },
    audio: { class: audio.label || 'unknown', confidence: audio.status === 'ok' && top[audio.label] !== undefined ? Number(top[audio.label]) : 0 },
    diagnosis: {
      status,
      causes: (out.causes || []).map((x) => ({ name: x.name, prob: x.prob, evidence: (x.evidence || []).filter((e) => !internal.test(e)) })),
    },
    checks: asked.map((k) => ({ question: k.q, answer: answers[k.id] || 'Not checked' })),
  };
}
