// Live data relay for the NSE Trade Desk app (Cloudflare Worker, free tier).
// Phones cannot read NSE or Yahoo directly (browser CORS rules), so this worker
// fetches them and returns small JSON the app can read every ~20 seconds.
//
//   GET /quotes?s=^NSEI,^NSEBANK,^INDIAVIX,RELIANCE.NS   -> last price, change, day high/low
//   GET /chain?symbol=NIFTY&expiry=27-Oct-2026             -> option chain near the money (LTP, OI)
//
// Only the app's own site may call it (ALLOWED_ORIGINS). Responses are cached ~15 s.

const ALLOWED_ORIGINS = ['https://rishiraj210984.github.io', 'http://localhost:8765', 'http://localhost:8766'];
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36';
const CACHE_SECONDS = 15;

function cors(origin) {
  const allow = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return { 'Access-Control-Allow-Origin': allow, 'Access-Control-Allow-Methods': 'GET, OPTIONS', 'Vary': 'Origin' };
}
function json(body, origin, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', 'Cache-Control': `public, max-age=${CACHE_SECONDS}`, ...cors(origin) },
  });
}

async function quote(sym) {
  const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?interval=1m&range=1d`, { headers: { 'User-Agent': UA } });
  const res = (await r.json()).chart.result[0];
  const m = res.meta, q = res.indicators.quote[0] || {};
  const hi = (q.high || []).filter((x) => x != null), lo = (q.low || []).filter((x) => x != null);
  const last = m.regularMarketPrice, prev = m.chartPreviousClose || m.previousClose || last;
  return { last, prev_close: prev, chg_pct: prev ? +((last / prev - 1) * 100).toFixed(2) : 0,
    high: hi.length ? Math.max(...hi) : null, low: lo.length ? Math.min(...lo) : null, time: m.regularMarketTime };
}

async function nseChain(symbol, expiry) {
  const home = await fetch('https://www.nseindia.com/option-chain', { headers: { 'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9' } });
  const cookies = (home.headers.getSetCookie ? home.headers.getSetCookie() : [home.headers.get('set-cookie') || ''])
    .map((c) => c.split(';')[0]).filter(Boolean).join('; ');
  const h = { 'User-Agent': UA, 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com/option-chain', 'Cookie': cookies };
  if (!expiry) {
    const info = await (await fetch(`https://www.nseindia.com/api/option-chain-contract-info?symbol=${symbol}`, { headers: h })).json();
    expiry = info.expiryDates[0];
  }
  const r = await fetch(`https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=${symbol}&expiry=${expiry}`, { headers: h });
  if (!r.ok) throw new Error(`NSE HTTP ${r.status}`);
  const rec = (await r.json()).records;
  const spot = rec.underlyingValue;
  const step = symbol === 'BANKNIFTY' ? 100 : 50;
  const strikes = rec.data
    .filter((x) => Math.abs(x.strikePrice - spot) <= 20 * step)
    .map((x) => ({ strike: x.strikePrice,
      ce_ltp: x.CE?.lastPrice ?? null, ce_oi: x.CE?.openInterest ?? 0, ce_chg_oi: x.CE?.changeinOpenInterest ?? 0,
      pe_ltp: x.PE?.lastPrice ?? null, pe_oi: x.PE?.openInterest ?? 0, pe_chg_oi: x.PE?.changeinOpenInterest ?? 0 }))
    .sort((a, b) => a.strike - b.strike);
  return { symbol, expiry, spot, timestamp: rec.timestamp, strikes };
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get('Origin') || '';
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors(origin) });
    const url = new URL(request.url);
    const cache = typeof caches !== 'undefined' ? caches.default : null;
    const hit = cache ? await cache.match(request) : null;
    if (hit) return hit;
    let res;
    try {
      if (url.pathname === '/quotes') {
        const syms = (url.searchParams.get('s') || '^NSEI').split(',').slice(0, 25);
        const out = {};
        await Promise.all(syms.map(async (s) => { try { out[s] = await quote(s); } catch (e) { out[s] = null; } }));
        res = json({ source: 'Yahoo', at: Date.now(), quotes: out }, origin);
      } else if (url.pathname === '/chain') {
        const sym = (url.searchParams.get('symbol') || 'NIFTY').toUpperCase();
        if (!['NIFTY', 'BANKNIFTY'].includes(sym)) return json({ error: 'symbol must be NIFTY or BANKNIFTY' }, origin, 400);
        res = json({ source: 'NSE', at: Date.now(), ...(await nseChain(sym, url.searchParams.get('expiry'))) }, origin);
      } else {
        res = json({ ok: true, routes: ['/quotes?s=^NSEI,^NSEBANK', '/chain?symbol=NIFTY&expiry=27-Oct-2026'] }, origin);
      }
    } catch (e) {
      return json({ error: String(e.message || e) }, origin, 502);
    }
    if (cache && res.ok) ctx.waitUntil(cache.put(request, res.clone()));
    return res;
  },
};
