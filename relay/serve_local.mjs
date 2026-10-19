// Serve the worker on http://localhost:8787 for local testing (no Cloudflare needed).
import http from 'node:http';
import worker from './worker.js';
http.createServer(async (req, res) => {
  const r = await worker.fetch(new Request('http://localhost:8787' + req.url, { headers: { Origin: req.headers.origin || '' } }), {}, { waitUntil() {} });
  res.writeHead(r.status, Object.fromEntries(r.headers)); res.end(await r.text());
}).listen(8787, () => console.log('relay on 8787'));
