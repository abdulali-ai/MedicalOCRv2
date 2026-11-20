/**
 * Production Static + Reverse-Proxy Server (HTTP & WebSocket) for MedOCR Studio.
 * Serves ./dist on PORT (default 3000) and forwards API/WebSocket routes to API_TARGET.
 */
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.join(__dirname, 'dist');

const PORT = Number(process.env.PORT || 3000);
const API_TARGET = process.env.API_TARGET || 'http://10.43.29.141:5000';
const DEFAULT_API_KEY =
  process.env.API_KEY || 'ocr-dev-api-key-change-in-production';

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

function isProxiedPath(urlPath = '') {
  return (
    urlPath.startsWith('/api/') ||
    urlPath === '/api' ||
    urlPath.startsWith('/process-pdf') ||
    urlPath.startsWith('/batch-process-pdf') ||
    urlPath.startsWith('/ws') ||
    urlPath.startsWith('/docs') ||
    urlPath === '/openapi.json'
  );
}

function parseTarget(targetStr) {
  const u = new URL(targetStr);
  return {
    hostname: u.hostname,
    port: Number(u.port || (u.protocol === 'https:' ? 443 : 80)),
  };
}

const primaryTarget = parseTarget(API_TARGET);
const fallbackTarget = { hostname: '127.0.0.1', port: 5000 };

function proxyHttpRequest(req, res, target) {
  const headers = { ...req.headers };
  headers.host = `${target.hostname}:${target.port}`;
  if (!headers['x-api-key'] && !headers['authorization']) {
    headers['x-api-key'] = DEFAULT_API_KEY;
  }

  const options = {
    hostname: target.hostname,
    port: target.port,
    path: req.url,
    method: req.method,
    headers,
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });

  proxyReq.on('error', (err) => {
    if (
      target.hostname !== fallbackTarget.hostname &&
      !req.readableEnded &&
      !res.headersSent
    ) {
      proxyHttpRequest(req, res, fallbackTarget);
      return;
    }
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          error: 'Bad Gateway',
          detail: `Unable to reach OCR API backend: ${err.message}`,
        })
      );
    }
  });

  req.pipe(proxyReq, { end: true });
}

const server = http.createServer((req, res) => {
  const urlObj = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  const pathname = decodeURIComponent(urlObj.pathname);

  if (isProxiedPath(pathname)) {
    proxyHttpRequest(req, res, primaryTarget);
    return;
  }

  // Serve static files from dist/
  let filePath = path.join(DIST_DIR, pathname === '/' ? 'index.html' : pathname);
  if (!filePath.startsWith(DIST_DIR)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    filePath = path.join(DIST_DIR, 'index.html');
  }

  if (!fs.existsSync(filePath)) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Frontend build not found. Run `npm run build` in /home/utec/ocr/frontend.');
    return;
  }

  const ext = path.extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';
  res.writeHead(200, { 'Content-Type': contentType });
  fs.createReadStream(filePath).pipe(res);
});

// WebSocket Upgrade Proxy
server.on('upgrade', (req, clientSocket, head) => {
  const connectToBackend = (target, allowFallback) => {
    const backendSocket = net.connect(target.port, target.hostname, () => {
      const reqLines = [`${req.method} ${req.url} HTTP/${req.httpVersion}`];
      for (let i = 0; i < req.rawHeaders.length; i += 2) {
        const k = req.rawHeaders[i];
        const v = req.rawHeaders[i + 1];
        if (k.toLowerCase() === 'host') {
          reqLines.push(`Host: ${target.hostname}:${target.port}`);
        } else {
          reqLines.push(`${k}: ${v}`);
        }
      }
      if (!req.headers['x-api-key']) {
        reqLines.push(`X-API-Key: ${DEFAULT_API_KEY}`);
      }
      reqLines.push('', '');
      backendSocket.write(reqLines.join('\r\n'));
      if (head && head.length > 0) {
        backendSocket.write(head);
      }
      clientSocket.pipe(backendSocket);
      backendSocket.pipe(clientSocket);
    });

    backendSocket.on('error', () => {
      if (allowFallback && target.hostname !== fallbackTarget.hostname) {
        connectToBackend(fallbackTarget, false);
      } else {
        clientSocket.destroy();
      }
    });

    clientSocket.on('error', () => {
      backendSocket.destroy();
    });
  };

  connectToBackend(primaryTarget, true);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(
    `MedOCR Studio Frontend deployed on http://0.0.0.0:${PORT} -> API ${API_TARGET}`
  );
});

// Also listen on port 300 in case the router forwards 3000 -> 300
if (PORT !== 300) {
  const altServer = http.createServer(server.listeners('request')[0]);
  for (const fn of server.listeners('upgrade')) {
    altServer.on('upgrade', fn);
  }
  altServer.on('error', () => {});
  altServer.listen(300, '0.0.0.0', () => {
    console.log(`Also listening on http://0.0.0.0:300 -> API ${API_TARGET}`);
  });
}

process.on('SIGTERM', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));

