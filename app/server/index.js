// Lactalis B2B Personalized Recommendation Engine — Express server.
// Serves the built React app and proxies all Databricks access (SQL + Genie)
// so credentials stay server-side. Dual-mode auth is handled in auth.js.

import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config, configProblems } from './config.js';
import { authMode } from './auth.js';
import { ping } from './db.js';
import dataRoutes from './routes/data.js';
import consoleRoutes from './routes/console.js';
import genieRoutes from './routes/genie.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(__dirname, '..', 'frontend', 'dist');

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '256kb' }));

// Small request log (stdout is captured by the Databricks Apps log viewer).
// Capture the original URL up front; Express rewrites req.url during router
// mounting, so reading it at finish time would drop the /api prefix.
app.use((req, res, next) => {
  const originalUrl = req.originalUrl || req.url;
  if (originalUrl.startsWith('/api/')) {
    const started = Date.now();
    res.on('finish', () => {
      console.log(`${req.method} ${originalUrl} -> ${res.statusCode} (${Date.now() - started}ms)`);
    });
  }
  next();
});

// --- Health -------------------------------------------------------------------
// Reports process liveness always; warehouse reachability best-effort so the app
// is considered healthy for routing even if the warehouse is briefly cold.
app.get('/api/health', async (req, res) => {
  const problems = configProblems();
  let warehouse = 'unknown';
  if (problems.length) {
    warehouse = 'not configured';
  } else {
    try {
      warehouse = (await ping()) ? 'reachable' : 'unreachable';
    } catch (err) {
      warehouse = `error: ${err.message?.slice(0, 160) || 'unknown'}`;
    }
  }
  res.status(200).json({
    status: 'healthy',
    auth_mode: authMode(),
    warehouse,
    catalog: config.catalog,
    schema: config.schema,
    genie_enabled: Boolean(config.genieSpaceId),
    dashboard_configured: Boolean(config.dashboardId),
    config_problems: problems,
    time: new Date().toISOString(),
  });
});

// --- Client runtime config ----------------------------------------------------
// Non-secret values the frontend needs (feature flags + deep links).
app.get('/api/config', (req, res) => {
  res.json({
    catalog: config.catalog,
    schema: config.schema,
    warehouse_id: config.warehouseId,
    genie_enabled: Boolean(config.genieSpaceId),
    dashboard_id: config.dashboardId || null,
    dashboard_url: config.dashboardId
      ? `${config.host}/embed/dashboardsv3/${config.dashboardId}`
      : null,
    workspace_host: config.host,
  });
});

// --- API routes ---------------------------------------------------------------
app.use('/api', dataRoutes);
app.use('/api/console', consoleRoutes);
app.use('/api/genie', genieRoutes);

// --- Static frontend + SPA fallback ------------------------------------------
app.use(express.static(distDir));
app.get('*', (req, res, next) => {
  if (req.path.startsWith('/api/')) return next();
  res.sendFile(path.join(distDir, 'index.html'), (err) => {
    if (err) {
      res
        .status(200)
        .type('html')
        .send('<h1>Lactalis Reco Engine</h1><p>Frontend build not found. Run <code>npm run build</code>.</p>');
    }
  });
});

// --- API error + 404 handlers -------------------------------------------------
app.use('/api', (req, res) => res.status(404).json({ error: 'Not found' }));
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({ error: err.message || 'Internal server error' });
});

app.listen(config.port, () => {
  console.log(
    `Lactalis Reco Engine listening on :${config.port} ` +
      `[auth=${authMode()}, catalog=${config.catalog}.${config.schema}, ` +
      `genie=${config.genieSpaceId ? 'on' : 'off'}, dashboard=${config.dashboardId ? 'on' : 'off'}]`
  );
  for (const problem of configProblems()) {
    console.error(`CONFIG PROBLEM: ${problem}`);
  }
});
