// Databricks SQL access via the SQL Statements REST API.
//
// Why REST (and not @databricks/sql): the REST path is dependency-free (Node 18+
// global fetch), works identically behind the Databricks Apps proxy, and needs no
// native/thrift build. The only runtime npm dependency for the whole server is express.
//
// Every table read tolerates a not-yet-created schema/table by returning [] so the
// UI still renders while the data workstream lands the gold layer.

import { config } from './config.js';
import { getToken, authHeaders } from './auth.js';

const STATEMENTS_URL = () => `${config.host}/api/2.0/sql/statements/`;

// Error signatures that mean "the object isn't there yet" rather than a real failure.
const MISSING_OBJECT_CODES = [
  'SCHEMA_NOT_FOUND',
  'TABLE_OR_VIEW_NOT_FOUND',
  'TABLE_OR_VIEW_NOT_FOUND_WITH_SUGGESTION',
];

export class MissingObjectError extends Error {
  constructor(message) {
    super(message);
    this.name = 'MissingObjectError';
    this.missingObject = true;
  }
}

function isMissingObjectMessage(msg = '') {
  if (MISSING_OBJECT_CODES.some((c) => msg.includes(c))) return true;
  // Belt-and-braces: catch the human-readable form too.
  return /cannot be found|does not exist|Table or view not found/i.test(msg);
}

function rowsToObjects(result) {
  const schema = result?.manifest?.schema?.columns || [];
  const cols = schema.map((c) => c.name);
  const dataArray = result?.result?.data_array || [];
  return dataArray.map((row) => {
    const obj = {};
    cols.forEach((name, i) => {
      obj[name] = coerce(row[i], schema[i]?.type_name);
    });
    return obj;
  });
}

function coerce(value, typeName) {
  if (value === null || value === undefined) return null;
  switch (typeName) {
    case 'INT':
    case 'BIGINT':
    case 'SHORT':
    case 'LONG':
      return Number.parseInt(value, 10);
    case 'FLOAT':
    case 'DOUBLE':
    case 'DECIMAL':
      return Number.parseFloat(value);
    case 'BOOLEAN':
      return value === true || value === 'true';
    default:
      return value; // STRING, DATE, TIMESTAMP left as-is (ISO strings)
  }
}

/**
 * Execute a SQL statement and return an array of row objects.
 * @param {string} statement  SQL text (use parameters for anything user-derived).
 * @param {Array}  parameters  Named parameters [{name, value, type?}].
 * @param {object} opts        { tolerateMissing: boolean }
 */
export async function runQuery(statement, parameters = [], opts = {}) {
  const token = await getToken();
  const body = {
    warehouse_id: config.warehouseId,
    statement,
    wait_timeout: '30s',
    on_wait_timeout: 'CONTINUE',
    format: 'JSON_ARRAY',
    disposition: 'INLINE',
  };
  if (parameters.length) body.parameters = parameters;

  let res = await fetch(STATEMENTS_URL(), {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  let payload = await res.json();

  // Poll if the statement is still running after the initial wait window.
  let statementId = payload?.statement_id;
  let state = payload?.status?.state;
  const deadline = Date.now() + 55 * 1000;
  while ((state === 'PENDING' || state === 'RUNNING') && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1200));
    const pollToken = await getToken();
    res = await fetch(`${STATEMENTS_URL()}${statementId}`, {
      method: 'GET',
      headers: authHeaders(pollToken),
    });
    payload = await res.json();
    state = payload?.status?.state;
  }

  if (state === 'SUCCEEDED') {
    return rowsToObjects(payload);
  }

  const errMsg = payload?.status?.error?.message || `SQL statement ended in state ${state}`;
  if (isMissingObjectMessage(errMsg)) {
    if (opts.tolerateMissing) return [];
    throw new MissingObjectError(errMsg);
  }
  throw new Error(errMsg);
}

/**
 * Convenience wrapper for gold-layer reads: returns [] instead of throwing when the
 * table/view/schema is not yet created by the data workstream.
 */
export async function safeQuery(statement, parameters = []) {
  return runQuery(statement, parameters, { tolerateMissing: true });
}

/** Lightweight connectivity probe for /api/health. */
export async function ping() {
  const rows = await runQuery('SELECT 1 AS ok');
  return rows?.[0]?.ok === 1;
}
