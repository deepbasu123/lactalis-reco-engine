import { useEffect, useState } from 'react';
import { api } from '../api';
import type { GenieAnswer } from '../types';
import { Spinner, EmptyState } from './Common';
import { IconSearch, IconArrow } from './Icons';

const SUGGESTIONS = [
  'Which segment has the highest cross-sell conversion?',
  'Top recommended products by revenue this quarter',
  'How many recommendations were held back by stock this month?',
  'Fulfillment rate by distribution center',
];

export function GeniePanel() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<GenieAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .genieStatus()
      .then((s) => alive && setEnabled(s.enabled))
      .catch(() => alive && setEnabled(false));
    return () => {
      alive = false;
    };
  }, []);

  async function ask(q: string) {
    const query = q.trim();
    if (!query || asking) return;
    setAsking(true);
    setError(null);
    setAnswer(null);
    try {
      const a = await api.genieAsk(query);
      setAnswer(a);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Genie request failed');
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="genie">
      <div className="genie__bar">
        <div style={{ display: 'grid', placeItems: 'center', width: 38, height: 38, borderRadius: 10, background: 'rgba(255,255,255,0.14)' }}>
          <IconSearch size={18} />
        </div>
        <div>
          <div className="kicker">Databricks Genie</div>
          <h4>Ask the reco data in plain language</h4>
        </div>
        {enabled === false && (
          <span className="pill" style={{ marginLeft: 'auto', background: 'rgba(255,255,255,0.16)', color: '#fff', borderColor: 'rgba(255,255,255,0.25)' }}>
            Not configured
          </span>
        )}
      </div>

      <div className="genie__body">
        {enabled === false ? (
          <EmptyState title="Genie is not wired up yet">
            Set the <code>GENIE_SPACE_ID</code> environment variable (and grant the app's service principal CAN RUN on
            the space) to enable natural-language analytics here. The rest of the console works without it.
          </EmptyState>
        ) : (
          <>
            <form
              className="genie__form"
              onSubmit={(e) => {
                e.preventDefault();
                ask(question);
              }}
            >
              <input
                id="genie-question"
                name="genie-question"
                className="genie__input"
                placeholder="e.g. Which segment converts best on cross-sell?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                disabled={asking || enabled === null}
              />
              <button className="btn btn--primary" disabled={asking || enabled === null || !question.trim()}>
                {asking ? <Spinner /> : <IconArrow size={18} />}
                {asking ? 'Asking' : 'Ask'}
              </button>
            </form>

            <div className="genie__suggest">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="genie__chip" onClick={() => { setQuestion(s); ask(s); }} disabled={asking}>
                  {s}
                </button>
              ))}
            </div>

            {error && (
              <div className="pill pill--warn" style={{ marginTop: 18 }}>
                {error}
              </div>
            )}

            {asking && !answer && (
              <div style={{ marginTop: 20, color: 'var(--text-soft)', fontSize: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
                <Spinner /> Genie is querying the gold layer…
              </div>
            )}

            {answer && <GenieResult answer={answer} />}
          </>
        )}
      </div>
    </div>
  );
}

function GenieResult({ answer }: { answer: GenieAnswer }) {
  return (
    <div className="genie__answer">
      {answer.answer && <div className="genie__text">{answer.answer}</div>}

      {answer.table && answer.table.rows.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 16 }}>
          <table className="minitable">
            <thead>
              <tr>
                {answer.table.columns.map((c) => (
                  <th key={c.name}>{c.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {answer.table.rows.slice(0, 25).map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} className={answer.table!.columns[j]?.type?.match(/INT|DOUBLE|DECIMAL|FLOAT|LONG/) ? 'num mono' : ''}>
                      {cell ?? '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {answer.table.row_count > 25 && (
            <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
              Showing 25 of {answer.table.row_count.toLocaleString()} rows.
            </div>
          )}
        </div>
      )}

      {answer.sql && (
        <details style={{ marginTop: 14 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12.5, color: 'var(--text-soft)', fontWeight: 500 }}>
            View generated SQL
          </summary>
          <pre className="genie__sql">{answer.sql}</pre>
        </details>
      )}

      {!answer.answer && !answer.table && (
        <div className="muted" style={{ fontSize: 14 }}>Genie responded without a tabular result.</div>
      )}
    </div>
  );
}
