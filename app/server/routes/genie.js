// Embedded Genie — the internal natural-language analytics surface.
// Proxies the Genie Conversation API so the browser never holds a token.
//
// Flow: start-conversation -> poll message until COMPLETED -> collect text +
// (optionally) query attachment results. Degrades gracefully when GENIE_SPACE_ID
// is unset: /status reports disabled and /ask returns 503 with a clear reason.

import { Router } from 'express';
import { config } from '../config.js';
import { getToken, authHeaders } from '../auth.js';

const router = Router();

const TERMINAL_OK = new Set(['COMPLETED']);
const TERMINAL_BAD = new Set(['FAILED', 'CANCELLED', 'QUERY_RESULT_EXPIRED']);

function genieBase() {
  return `${config.host}/api/2.0/genie/spaces/${config.genieSpaceId}`;
}

async function gFetch(url, init = {}) {
  const token = await getToken();
  const res = await fetch(url, { ...init, headers: authHeaders(token) });
  const text = await res.text();
  let json;
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    json = { raw: text };
  }
  if (!res.ok) {
    const msg = json?.message || json?.error_code || `Genie API ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return json;
}

// GET /api/genie/status — lets the UI decide whether to show the panel live.
router.get('/status', (req, res) => {
  res.json({
    enabled: Boolean(config.genieSpaceId),
    space_id: config.genieSpaceId || null,
  });
});

// POST /api/genie/ask  { question }
router.post('/ask', async (req, res) => {
  if (!config.genieSpaceId) {
    return res.status(503).json({
      enabled: false,
      error: 'Genie is not configured. Set GENIE_SPACE_ID to enable natural-language analytics.',
    });
  }

  const question = (req.body?.question || '').toString().trim();
  if (!question) return res.status(400).json({ error: 'question is required' });

  try {
    // 1. Start a fresh conversation for each question (stateless demo surface).
    const started = await gFetch(`${genieBase()}/start-conversation`, {
      method: 'POST',
      body: JSON.stringify({ content: question }),
    });
    const conversationId = started.conversation_id || started.conversation?.id;
    const messageId = started.message_id || started.message?.id;
    if (!conversationId || !messageId) {
      return res.status(502).json({ error: 'Genie did not return a conversation/message id.' });
    }

    // 2. Poll the message to completion.
    const msgUrl = `${genieBase()}/conversations/${conversationId}/messages/${messageId}`;
    let message = null;
    const deadline = Date.now() + 90 * 1000;
    while (Date.now() < deadline) {
      message = await gFetch(msgUrl);
      const status = message.status;
      if (TERMINAL_OK.has(status) || TERMINAL_BAD.has(status)) break;
      await new Promise((r) => setTimeout(r, 1500));
    }

    if (!message || TERMINAL_BAD.has(message.status)) {
      return res.status(502).json({
        error: `Genie could not answer (status: ${message?.status || 'timeout'}).`,
        status: message?.status || 'TIMEOUT',
      });
    }

    // 3. Assemble answer text + any query attachment (SQL + result table).
    const attachments = message.attachments || [];
    let answerText = '';
    let sql = null;
    let queryDescription = null;
    let table = null;

    for (const att of attachments) {
      if (att.text?.content) {
        answerText += (answerText ? '\n\n' : '') + att.text.content;
      }
      if (att.query) {
        sql = att.query.query || att.query.query_text || null;
        queryDescription = att.query.description || null;
        const attachmentId = att.attachment_id || att.query.id;
        if (attachmentId) {
          // Documented path (AWS docs): .../messages/{id}/query-result/{attachment_id}.
          // Some workspace versions expose .../attachments/{attachment_id}/query-result;
          // try the documented form first, then fall back so this is version-robust.
          const resultUrls = [
            `${msgUrl}/query-result/${attachmentId}`,
            `${msgUrl}/attachments/${attachmentId}/query-result`,
          ];
          for (const url of resultUrls) {
            try {
              const result = await gFetch(url);
              table = parseStatementResponse(result?.statement_response);
              if (table) break;
            } catch {
              table = null; // try next form; result may also have expired
            }
          }
        }
      }
    }

    res.json({
      question,
      answer: answerText || queryDescription || null,
      sql,
      query_description: queryDescription,
      table,
      conversation_id: conversationId,
      message_id: messageId,
    });
  } catch (err) {
    res.status(err.status && err.status < 500 ? err.status : 502).json({ error: err.message });
  }
});

function parseStatementResponse(statementResponse) {
  if (!statementResponse) return null;
  const cols = statementResponse.manifest?.schema?.columns || [];
  const dataArray = statementResponse.result?.data_array || [];
  return {
    columns: cols.map((c) => ({ name: c.name, type: c.type_name })),
    rows: dataArray,
    truncated: Boolean(statementResponse.result?.truncated),
    row_count: dataArray.length,
  };
}

export default router;
