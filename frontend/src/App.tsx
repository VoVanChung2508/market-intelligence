import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bell,
  ChartNoAxesCombined,
  ChevronDown,
  CircleHelp,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Newspaper,
  Search,
  Settings2,
  ShieldAlert,
  Wallet,
} from "lucide-react";

import {
  getAlerts,
  getOverview,
  getAnalysisReport,
  getPriceBars,
  type AnalysisReport,
  type AnalysisResponse,
  type AlertEvent,
  type AlertsResponse,
  type AssetSummary,
  type OverviewResponse,
  type PriceBar,
  type ServiceHealth,
} from "./api";
import PriceChart from "./PriceChart";

const navigation = [
  { label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { label: "Market explorer", icon: ChartNoAxesCombined, group: "Workspace" },
  { label: "AI analysis", icon: Activity, group: "Research" },
  { label: "Opportunities", icon: Gauge, group: "Research", planned: true },
  { label: "News intelligence", icon: Newspaper, group: "Research", planned: true },
  { label: "Portfolio & risk", icon: Wallet, group: "Simulations", planned: true },
  { label: "Backtesting", icon: FlaskConical, group: "Simulations", planned: true },
  { label: "Alerts", icon: Bell, group: "Operations" },
  { label: "System health", icon: Database, group: "Operations" },
  { label: "Settings", icon: Settings2, group: "Operations", planned: true },
];

type LoadState = "loading" | "ready" | "error";

function formatPrice(price: number | null, currency: string): string {
  if (price === null || !Number.isFinite(price)) return "--";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: price < 1 ? 6 : 2,
    }).format(price);
  } catch {
    return `${price.toLocaleString("en-US", { maximumFractionDigits: 6 })} ${currency}`;
  }
}

function formatTime(value: string | null): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return `${new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date)} UTC`;
}

function changeLabel(change: number | null): string {
  if (change === null || !Number.isFinite(change)) return "--";
  return `${change > 0 ? "+" : ""}${change.toFixed(2)}%`;
}

function App() {
  const [activePage, setActivePage] = useState("Overview");
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [bars, setBars] = useState<PriceBar[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [analysis, setAnalysis] = useState<AnalysisReport | null>(null);
  const [analysisState, setAnalysisState] = useState<LoadState>("loading");
  const [analysisError, setAnalysisError] = useState("");
  const [alertsState, setAlertsState] = useState<LoadState>("loading");
  const [alertsData, setAlertsData] = useState<AlertsResponse | null>(null);
  const [alertsError, setAlertsError] = useState("");
  const assets = overview?.assets ?? [];
  const selectedAsset = assets.find((asset) => asset.symbol === selectedSymbol) ?? assets[0];

  useEffect(() => {
    const controller = new AbortController();
    setLoadState("loading");
    setErrorMessage("");

    getOverview(controller.signal)
      .then((result) => {
        setOverview(result);
        setLoadState("ready");
        setSelectedSymbol((current) => current || result.assets[0]?.symbol || "");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoadState("error");
        setErrorMessage(
          error instanceof Error ? error.message : "Could not reach the market service.",
        );
      });

    return () => controller.abort();
  }, [refreshKey]);

  useEffect(() => {
    if (!selectedSymbol || loadState !== "ready") return;
    const controller = new AbortController();

    getPriceBars(
      selectedSymbol,
      selectedAsset?.asset_class === "equity" ? "1d" : "1h",
      controller.signal,
    )
      .then((result) => setBars(result.bars))
      .catch(() => {
        if (!controller.signal.aborted) setBars([]);
      });

    return () => controller.abort();
  }, [loadState, selectedAsset?.asset_class, selectedSymbol]);

  useEffect(() => {
    if (activePage !== "AI analysis" || !selectedSymbol) return;
    const controller = new AbortController();
    setAnalysisState("loading");
    setAnalysisError("");

    getAnalysisReport(selectedSymbol, controller.signal)
      .then((result: AnalysisResponse) => {
        if (!result.available) {
          setAnalysis(null);
          setAnalysisError(result.detail);
          setAnalysisState("error");
          return;
        }
        setAnalysis(result.report);
        setAnalysisState("ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setAnalysisState("error");
        setAnalysisError(error instanceof Error ? error.message : "No evaluation report is available.");
      });

    return () => controller.abort();
  }, [activePage, selectedSymbol]);

  useEffect(() => {
    if (activePage !== "Alerts") return;
    const controller = new AbortController();
    setAlertsState("loading");
    getAlerts(controller.signal)
      .then((result) => {
        setAlertsData(result);
        setAlertsState("ready");
        setAlertsError("");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setAlertsState("error");
        setAlertsError(error instanceof Error ? error.message : "Alert history is unavailable.");
      });
    return () => controller.abort();
  }, [activePage]);

  const servicesAvailable = overview?.services.filter(
    (service) => service.status === "healthy",
  ).length ?? 0;
  const plannedPages = navigation.filter((item) => item.planned).map((item) => item.label);

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <a className="brand-lockup" href="#overview" onClick={() => setActivePage("Overview")}>
          <span className="brand-mark" aria-hidden="true">MI</span>
          <span className="brand-copy">
            <strong>Market Intelligence</strong>
            <small>RESEARCH DESK</small>
          </span>
        </a>

        <nav className="primary-nav" aria-label="Main navigation">
          {Array.from(new Set(navigation.map((item) => item.group))).map((group) => (
            <div className="nav-group" key={group}>
              <p className="nav-heading">{group}</p>
              {navigation.filter((item) => item.group === group).map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    className={`nav-item ${activePage === item.label ? "is-active" : ""} ${item.planned ? "is-planned" : ""}`}
                    key={item.label}
                    onClick={() => setActivePage(item.label)}
                    title={item.planned ? `${item.label}: not available yet` : item.label}
                    type="button"
                    aria-current={activePage === item.label ? "page" : undefined}
                  >
                    <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
                    <span>{item.label}</span>
                    {item.planned && <span className="nav-soon">Later</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="connection-led" data-state={loadState} />
          <span>{loadState === "ready" ? "Local API connected" : loadState === "loading" ? "Connecting" : "API unavailable"}</span>
          <button className="icon-button help-button" title="About this workspace" type="button" aria-label="About this workspace">
            <CircleHelp size={16} />
          </button>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-separator">/</span><strong>{activePage}</strong></div>
          <div className="topbar-actions">
            <span className="local-badge"><span />Local research</span>
            <button className="icon-button" title="Refresh market data" aria-label="Refresh market data" type="button" onClick={() => setRefreshKey((key) => key + 1)}>
              <Activity size={17} />
            </button>
            <button className="user-button" type="button" aria-label="Workspace menu">
              <span className="avatar">MI</span><ChevronDown size={14} />
            </button>
          </div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">Market monitor</p>
              <h1>{activePage}</h1>
              <p className="page-subtitle">Research data with source and freshness visible.</p>
            </div>
            <div className="updated-label">
              <span className={`status-dot ${loadState}`} />
              <span>{overview ? `Updated ${formatTime(overview.generated_at)}` : "No update received"}</span>
            </div>
          </div>

          {loadState === "error" && (
            <section className="notice notice-error" role="alert">
              <AlertTriangle size={18} aria-hidden="true" />
              <div><strong>Market service unavailable</strong><p>{errorMessage}</p></div>
              <button className="text-button" type="button" onClick={() => setRefreshKey((key) => key + 1)}>Retry</button>
            </section>
          )}

          {overview?.data_status === "stale" && (
            <section className="notice notice-warning" role="status">
              <ShieldAlert size={18} aria-hidden="true" />
              <div><strong>Showing stale market data</strong><p>Values are retained for reference and are not live.</p></div>
            </section>
          )}

          {activePage === "Overview" && (
            <OverviewPage
              assets={assets}
              bars={bars}
              loadState={loadState}
              onNavigate={setActivePage}
              onSelectAsset={setSelectedSymbol}
              selectedAsset={selectedAsset}
              services={overview?.services ?? []}
              servicesAvailable={servicesAvailable}
            />
          )}
          {activePage === "Market explorer" && (
            <MarketExplorer
              assets={assets}
              bars={bars}
              loadState={loadState}
              onSelectAsset={setSelectedSymbol}
              selectedSymbol={selectedSymbol}
            />
          )}
          {activePage === "System health" && (
            <SystemHealth loadState={loadState} services={overview?.services ?? []} />
          )}
          {activePage === "AI analysis" && (
            <AnalysisPage
              assets={assets}
              error={analysisError}
              loadState={analysisState}
              onSelectAsset={setSelectedSymbol}
              report={analysis}
              symbol={selectedSymbol}
            />
          )}
          {activePage === "Alerts" && (
            <AlertsPage error={alertsError} loadState={alertsState} response={alertsData} />
          )}
          {plannedPages.includes(activePage) && <UnavailablePage title={activePage} />}
        </div>
      </main>
    </div>
  );
}

interface OverviewPageProps {
  assets: AssetSummary[];
  bars: PriceBar[];
  loadState: LoadState;
  onNavigate: (page: string) => void;
  onSelectAsset: (symbol: string) => void;
  selectedAsset?: AssetSummary;
  services: ServiceHealth[];
  servicesAvailable: number;
}

function OverviewPage({
  assets,
  bars,
  loadState,
  onNavigate,
  onSelectAsset,
  selectedAsset,
  services,
  servicesAvailable,
}: OverviewPageProps) {
  const positiveCount = assets.filter(
    (asset) => asset.change_pct !== null && asset.change_pct > 0,
  ).length;

  return (
    <>
      <section className="metric-grid" aria-label="Market summary">
        <Metric label="Assets tracked" value={loadState === "ready" ? String(assets.length) : "--"} detail="Configured watchlist" />
        <Metric label="Advancing assets" value={loadState === "ready" ? `${positiveCount} / ${assets.length}` : "--"} detail="Compared with prior source interval" />
        <Metric label="Services healthy" value={loadState === "ready" ? `${servicesAvailable} / ${services.length}` : "--"} detail="Collector and data store" />
        <Metric label="Selected market" value={selectedAsset?.symbol ?? "--"} detail={selectedAsset?.freshness === "fresh" ? "Fresh source data" : selectedAsset?.freshness === "stale" ? "Stale source data" : "No current reading"} />
      </section>

      <div className="content-grid">
        <section className="panel chart-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Price history</p>
              <h2>{selectedAsset ? `${selectedAsset.name} · ${selectedAsset.symbol}` : "No asset selected"}</h2>
            </div>
            {selectedAsset && <span className={`freshness-tag ${selectedAsset.freshness}`}>{selectedAsset.freshness}</span>}
          </div>
          {bars.length > 1 ? (
            <PriceChart bars={bars} label={selectedAsset?.symbol ?? "Selected asset"} />
          ) : (
            <div className={`chart-empty ${loadState === "loading" ? "is-loading" : ""}`} aria-live="polite">
              {loadState === "loading" ? "Loading market history" : loadState === "error" ? "Market history unavailable" : "No historical bars returned for this asset"}
            </div>
          )}
          <div className="chart-footnote">
            <span>{selectedAsset?.source ?? "Source not reported"}</span>
            <span>{formatTime(selectedAsset?.timestamp ?? null)}</span>
          </div>
        </section>

        <section className="panel services-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Pipeline</p><h2>Service status</h2></div>
            <Database size={18} className="panel-icon" aria-hidden="true" />
          </div>
          {loadState === "ready" && services.length > 0 ? (
            <div className="service-list">
              {services.map((service) => (
                <div className="service-row" key={service.name}>
                  <span className={`status-dot ${service.status}`} />
                  <div><strong>{service.name}</strong><small>{service.detail}</small></div>
                  <span className="service-state">{service.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="panel-empty">Service health will be reported by the local API.</div>
          )}
        </section>

        <section className="panel market-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Watchlist</p><h2>Market snapshot</h2></div>
            <button className="text-button" type="button" onClick={() => onNavigate("Market explorer")}>Explore market</button>
          </div>
          {loadState === "loading" ? (
            <div className="panel-empty">Loading assets from the market service.</div>
          ) : assets.length > 0 ? (
            <AssetTable assets={assets.slice(0, 8)} onSelectAsset={onSelectAsset} />
          ) : (
            <div className="panel-empty">No assets are available. Configure a watchlist or check the collector status.</div>
          )}
        </section>
      </div>
    </>
  );
}

interface MetricProps {
  label: string;
  value: string;
  detail: string;
}

function Metric({ label, value, detail }: MetricProps) {
  return (
    <article className="metric-panel">
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      <p className="metric-detail">{detail}</p>
    </article>
  );
}

interface AssetTableProps {
  assets: AssetSummary[];
  onSelectAsset: (symbol: string) => void;
}

function AssetTable({ assets, onSelectAsset }: AssetTableProps) {
  return (
    <div className="table-wrap">
      <table className="asset-table">
        <thead><tr><th>Asset</th><th>Class</th><th>Last price</th><th>Change</th><th>Source</th><th>Updated</th></tr></thead>
        <tbody>
          {assets.map((asset) => (
            <tr key={asset.symbol}>
              <td><button className="asset-link" type="button" onClick={() => onSelectAsset(asset.symbol)}><strong>{asset.symbol}</strong><span>{asset.name}</span></button></td>
              <td><span className="asset-class">{asset.asset_class}</span></td>
              <td className="numeric-cell">{formatPrice(asset.price, asset.currency)}</td>
              <td className={`numeric-cell ${asset.change_pct === null ? "" : asset.change_pct > 0 ? "positive" : asset.change_pct < 0 ? "negative" : ""}`}>{changeLabel(asset.change_pct)}</td>
              <td><span className="source-cell">{asset.source}</span></td>
              <td><span className={`freshness-cell ${asset.freshness}`}>{asset.freshness}</span><small className="table-time">{formatTime(asset.timestamp)}</small></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface MarketExplorerProps {
  assets: AssetSummary[];
  bars: PriceBar[];
  loadState: LoadState;
  onSelectAsset: (symbol: string) => void;
  selectedSymbol: string;
}

function MarketExplorer({ assets, bars, loadState, onSelectAsset, selectedSymbol }: MarketExplorerProps) {
  const [query, setQuery] = useState("");
  const selectedAsset = assets.find((asset) => asset.symbol === selectedSymbol) ?? assets[0];
  const filtered = assets.filter((asset) =>
    `${asset.symbol} ${asset.name} ${asset.asset_class}`.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <>
      <section className="panel explorer-chart-panel">
        <div className="panel-heading">
          <div><p className="eyebrow">Selected asset</p><h2>{selectedAsset ? `${selectedAsset.name} · ${selectedAsset.symbol}` : "No asset selected"}</h2></div>
          {selectedAsset && <span className={`freshness-tag ${selectedAsset.freshness}`}>{selectedAsset.freshness}</span>}
        </div>
        {bars.length > 1 ? <PriceChart bars={bars} label={selectedAsset?.symbol ?? "Selected asset"} /> : (
          <div className="chart-empty">{loadState === "loading" ? "Loading market history" : "No historical bars returned for this asset"}</div>
        )}
        <div className="chart-footnote"><span>{selectedAsset?.source ?? "Source not reported"}</span><span>{formatTime(selectedAsset?.timestamp ?? null)}</span></div>
      </section>
      <section className="panel market-explorer-panel">
        <div className="explorer-toolbar">
          <label className="search-field">
            <Search size={16} aria-hidden="true" />
            <span className="visually-hidden">Search assets</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols or assets" />
          </label>
          <span className="result-count">{loadState === "ready" ? `${filtered.length} assets` : "Waiting for API"}</span>
        </div>
        {loadState === "loading" ? <div className="panel-empty">Loading market data.</div> : filtered.length ? (
          <AssetTable assets={filtered} onSelectAsset={onSelectAsset} />
        ) : (
          <div className="panel-empty">{assets.length ? "No assets match this search." : "No market data is available."}</div>
        )}
        {selectedSymbol && <p className="selection-note">Selected asset: <strong>{selectedSymbol}</strong></p>}
      </section>
    </>
  );
}

interface SystemHealthProps {
  loadState: LoadState;
  services: OverviewResponse["services"];
}

function SystemHealth({ loadState, services }: SystemHealthProps) {
  if (loadState !== "ready") {
    return <UnavailablePage title="System health" detail="The local API is required to report service health." />;
  }
  if (services.length === 0) {
    return <UnavailablePage title="System health" detail="The API is connected but has not reported service checks." />;
  }
  return (
    <section className="panel health-panel">
      <div className="panel-heading"><div><p className="eyebrow">Operations</p><h2>Pipeline checks</h2></div></div>
      <div className="health-list">
        {services.map((service) => (
          <article className="health-row" key={service.name}>
            <span className={`status-dot ${service.status}`} />
            <div className="health-description"><strong>{service.name}</strong><p>{service.detail}</p><small>Checked {formatTime(service.updated_at)}</small></div>
            <span className={`health-status ${service.status}`}>{service.status}</span>
          </article>
        ))}
      </div>
    </section>
  );
}

interface AnalysisPageProps {
  assets: AssetSummary[];
  error: string;
  loadState: LoadState;
  onSelectAsset: (symbol: string) => void;
  report: AnalysisReport | null;
  symbol: string;
}

function AnalysisPage({ assets, error, loadState, onSelectAsset, report, symbol }: AnalysisPageProps) {
  return (
    <>
      <div className="analysis-toolbar">
        <label htmlFor="analysis-asset">Asset</label>
        <select id="analysis-asset" value={symbol} onChange={(event) => onSelectAsset(event.target.value)}>
          {assets.map((asset) => <option key={asset.symbol} value={asset.symbol}>{asset.symbol} · {asset.name}</option>)}
        </select>
      </div>
      {loadState === "loading" && <div className="panel-empty">Loading the stored evaluation for {symbol || "the selected asset"}.</div>}
      {loadState === "error" && <UnavailablePage title="Model evaluation" detail={error || "No model evaluation is stored for this asset."} />}
      {loadState === "ready" && report && (
        <>
          <section className={`analysis-outcome ${report.gate_passed ? "passed" : "withheld"}`}>
            <div className="analysis-symbol">{report.symbol}</div>
            <div>
              <p className="eyebrow">Research gate</p>
              <h2>{report.gate_passed ? "Baseline passed evaluation" : "No validated signal"}</h2>
              <p>{report.gate_passed ? `Latest direction: ${report.latest_direction ?? "not generated"}` : "The model did not beat the naive baseline on both validation and holdout test. Direction is withheld."}</p>
            </div>
            <span className="model-version">{report.model_version}</span>
          </section>

          <section className="panel analysis-panel">
            <div className="panel-heading">
              <div><p className="eyebrow">Out-of-sample metrics</p><h2>Model vs majority baseline</h2></div>
              <span className="result-count">{report.labeled_samples} labeled observations</span>
            </div>
            <div className="table-wrap">
              <table className="asset-table analysis-table">
                <thead><tr><th>Split</th><th>Model accuracy</th><th>Baseline accuracy</th><th>Model balanced accuracy</th><th>Baseline balanced accuracy</th></tr></thead>
                <tbody>
                  {(["validation", "test"] as const).map((split) => (
                    <tr key={split}>
                      <td>{split}</td>
                      <td className="numeric-cell">{(report.metrics[split].accuracy * 100).toFixed(2)}%</td>
                      <td className="numeric-cell">{(report.majority_baseline[split].accuracy * 100).toFixed(2)}%</td>
                      <td className="numeric-cell">{(report.metrics[split].balanced_accuracy * 100).toFixed(2)}%</td>
                      <td className="numeric-cell">{(report.majority_baseline[split].balanced_accuracy * 100).toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="analysis-footnote">
              <span>Source: {report.source}</span>
              <span>Trained: {formatTime(report.trained_at)}</span>
              <span>Confidence probability: not calibrated</span>
            </div>
          </section>
        </>
      )}
    </>
  );
}

interface AlertsPageProps {
  error: string;
  loadState: LoadState;
  response: AlertsResponse | null;
}

function AlertsPage({ error, loadState, response }: AlertsPageProps) {
  if (loadState === "loading") {
    return <div className="panel-empty">Loading alert history.</div>;
  }
  if (loadState === "error" || !response) {
    return <UnavailablePage title="Alert history" detail={error || "Alert history is unavailable."} />;
  }

  return (
    <section className="panel alerts-panel">
      <div className="alert-summary-row">
        <div><p className="eyebrow">Active rule</p><strong>Single-interval move threshold</strong></div>
        <span className="threshold-value">{response.threshold_pct}%</span>
        <span className={`ntfy-state ${response.ntfy_configured ? "configured" : "not-configured"}`}>
          {response.ntfy_configured ? "ntfy configured" : "ntfy not configured"}
        </span>
      </div>
      {response.items.length === 0 ? (
        <div className="panel-empty">No threshold alerts have been recorded.</div>
      ) : (
        <div className="table-wrap">
          <table className="asset-table">
            <thead><tr><th>Asset</th><th>Interval</th><th>Rule</th><th>Move</th><th>Delivery</th><th>Triggered</th></tr></thead>
            <tbody>
              {response.items.map((event: AlertEvent) => (
                <tr key={`${event.symbol}-${event.interval}-${event.timestamp}`}>
                  <td><strong>{event.symbol}</strong></td>
                  <td>{event.interval}</td>
                  <td>{event.rule.replaceAll("_", " ")}</td>
                  <td className={`numeric-cell ${event.change_pct > 0 ? "positive" : "negative"}`}>{changeLabel(event.change_pct)}</td>
                  <td><span className={`delivery-state ${event.delivery_status}`}>{event.delivery_status.replaceAll("_", " ")}</span></td>
                  <td>{formatTime(event.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface UnavailablePageProps {
  title: string;
  detail?: string;
}

function UnavailablePage({ title, detail }: UnavailablePageProps) {
  return (
    <section className="unavailable-page" aria-live="polite">
      <div className="unavailable-icon"><Search size={20} aria-hidden="true" /></div>
      <p className="eyebrow">Not available yet</p>
      <h2>{title}</h2>
      <p>{detail ?? "This module is not implemented. No sample or simulated results are shown."}</p>
    </section>
  );
}

export default App;