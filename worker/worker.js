/* tacticus-agent proxy (Cloudflare Worker, no deps).
 *
 *   POST /player {apiKey}  -> Tacticus API (CORS fix + pass-through; never stored)
 *   POST /chat {brief, messages} -> LLM over the precomputed brief
 *                                 (game key NEVER reaches the LLM)
 *
 * Secrets:  wrangler secret put LLM_KEY
 *           wrangler secret put LLM_MODEL
 *           wrangler secret put LLM_BASE_URL   # optional, OpenAI-compatible,
 *                                              # default https://api.openai.com/v1
 *
 * ponytail: in-memory per-IP rate limit (resets on cold start) — use
 * Cloudflare dashboard rate rules if it ever matters.
 */
const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'POST, OPTIONS',
  'access-control-allow-headers': 'content-type',
};

const hits = new Map();
function limited(ip) {
  const now = Date.now();
  const a = (hits.get(ip) || []).filter(t => now - t < 60000);
  if (a.length > 30) return true;
  a.push(now);
  hits.set(ip, a);
  return false;
}

const text = (body, status = 200, type = 'text/plain; charset=utf-8') =>
  new Response(body, { status, headers: { ...CORS, 'content-type': type } });

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { headers: CORS });
    if (limited(req.headers.get('cf-connecting-ip') || '?'))
      return text('rate limited', 429);

    if (url.pathname === '/player' && req.method === 'POST') {
      const { apiKey } = await req.json().catch(() => ({}));
      const r = await fetch('https://api.tacticusgame.com/api/v1/player', {
        headers: { 'X-API-KEY': apiKey || '' },
      });
      return text(await r.text(), r.status, 'application/json');
    }

    if (url.pathname === '/chat' && req.method === 'POST') {
      const { brief = '', messages = [] } = await req.json().catch(() => ({}));
      const system =
        "You are tacticus-agent, a Tacticus progression advisor. The user's " +
        'situation was computed by deterministic tools and is given below as a ' +
        'brief. Ground every recommendation in it; when asked about something ' +
        'the brief does not cover, say so plainly instead of guessing. Be ' +
        'concise and actionable. Never ask for or repeat API keys.\n\n' +
        '--- BRIEF ---\n' + String(brief).slice(0, 40000);
      const r = await fetch(
        (env.LLM_BASE_URL || 'https://api.openai.com/v1') + '/chat/completions',
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            authorization: `Bearer ${env.LLM_KEY}`,
          },
          body: JSON.stringify({
            model: env.LLM_MODEL || 'gpt-4o-mini',
            messages: [{ role: 'system', content: system }, ...messages.slice(-20)],
          }),
        },
      );
      if (!r.ok) return text('LLM HTTP ' + r.status, 502);
      const data = await r.json().catch(() => null);
      const reply = data && data.choices && data.choices[0]
        && data.choices[0].message && data.choices[0].message.content;
      return text(reply || '(empty reply from LLM)');
    }

    if (url.pathname === '/') return text('tacticus-agent proxy\n');
    return text('not found', 404);
  },
};
