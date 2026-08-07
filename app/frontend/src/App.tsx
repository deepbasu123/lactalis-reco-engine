import { useEffect, useState } from 'react';
import { api } from './api';
import type { AppConfig, Customer } from './types';
import { Storefront } from './views/Storefront';
import { Console } from './views/Console';
import { IconStore, IconConsole } from './components/Icons';

type Mode = 'store' | 'console';

export default function App() {
  const [mode, setMode] = useState<Mode>('store');
  const [config, setConfig] = useState<AppConfig | null>(null);

  // Customer list is loaded once and shared with the storefront (account switcher).
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customersLoading, setCustomersLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
    api
      .customers()
      .then((res) => {
        setCustomers(res.customers || []);
        if (res.customers?.length) setSelectedId(res.customers[0].customer_id);
      })
      .catch(() => setCustomers([]))
      .finally(() => setCustomersLoading(false));
  }, []);

  return (
    <>
      <header className="appbar">
        <div className="wrap appbar__inner">
          <div className="brand">
            <img src="/lactalis-logo.svg" alt="Lactalis" />
            <span className="brand__divider" />
            <span className="brand__tag">{mode === 'store' ? 'MyLactalis' : 'Recommendation Engine'}</span>
          </div>

          <nav className="modeswitch" aria-label="View switcher">
            <button className={mode === 'store' ? 'active' : ''} onClick={() => setMode('store')}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <IconStore size={16} /> Storefront
              </span>
            </button>
            <button className={mode === 'console' ? 'active' : ''} onClick={() => setMode('console')}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <IconConsole size={16} /> Engine Console
              </span>
            </button>
          </nav>
        </div>
      </header>

      <main>
        {mode === 'store' ? (
          <Storefront
            customers={customers}
            customersLoading={customersLoading}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        ) : (
          <Console config={config} />
        )}
      </main>

      <footer style={{ borderTop: '1px solid var(--line)', background: '#fff' }}>
        <div
          className="wrap"
          style={{
            padding: '20px 32px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <span className="faint" style={{ fontSize: 12.5 }}>
            Lactalis B2B Personalized Recommendation Engine · demo
          </span>
          <span className="poweredby">
            <span className="dot" /> Powered by Databricks
            {config?.catalog && (
              <span className="faint" style={{ marginLeft: 10 }}>
                · {config.catalog}.{config.schema}
              </span>
            )}
          </span>
        </div>
      </footer>
    </>
  );
}
