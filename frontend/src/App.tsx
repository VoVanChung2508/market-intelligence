import { useEffect, useState, type FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  Bell,
  ChartNoAxesCombined,
  CircleHelp,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Newspaper,
  Search,
  Settings2,
  ShieldAlert,
  Signal,
  Wallet,
} from "lucide-react";

import {
  getAlerts,
  getOverview,
  getAnalysisReport,
  getNews,
  getPriceBars,
  getSocialHealth,
  createSocialAlertRule,
  createSocialSource,
  deactivateSocialSource,
  getSocialAlertRules,
  getSocialEvents,
  getSocialNotificationHistory,
  getSocialPosts,
  getSocialSources,
  getSocialStatistics,
  login as loginApi,
  logout as logoutApi,
  restoreSession,
  type AuthUser,
  type AnalysisReport,
  type AnalysisResponse,
  type AlertEvent,
  type AlertsResponse,
  type AssetSummary,
  type OverviewResponse,
  type PriceBar,
  type NewsArticle,
  type NewsResponse,
  type SocialPost,
  type SocialEvent,
  type SocialSource,
  type SocialAlertRule,
  type SocialDelivery,
  type SocialStatistics,
  type ServiceHealth,
} from "./api";
import PriceChart from "./PriceChart";

const navigation = [
  { label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { label: "Market explorer", icon: ChartNoAxesCombined, group: "Workspace" },
  { label: "AI analysis", icon: Activity, group: "Research" },
  { label: "Opportunities", icon: Gauge, group: "Research", planned: true },
  { label: "News intelligence", icon: Newspaper, group: "Research" },
  { label: "Social intelligence", icon: Signal, group: "Research" },
  { label: "Portfolio & risk", icon: Wallet, group: "Simulations", planned: true },
  { label: "Backtesting", icon: FlaskConical, group: "Simulations", planned: true },
  { label: "Alerts", icon: Bell, group: "Operations" },
  { label: "System health", icon: Database, group: "Operations" },
  { label: "Settings", icon: Settings2, group: "Operations", planned: true },
];

type LoadState = "loading" | "ready" | "error";
type AuthState = "checking" | "authenticated" | "unauthenticated";

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
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authError, setAuthError] = useState("");
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
  const [socialState, setSocialState] = useState<LoadState>("loading");
  const [socialPosts, setSocialPosts] = useState<SocialPost[]>([]);
  const [socialStats, setSocialStats] = useState<SocialStatistics | null>(null);
  const [socialHealth, setSocialHealth] = useState<Awaited<ReturnType<typeof getSocialHealth>> | null>(null);
  const [socialError, setSocialError] = useState("");
  const [socialAsset, setSocialAsset] = useState("");
  const [socialEvents, setSocialEvents] = useState<SocialEvent[]>([]);
  const [socialSources, setSocialSources] = useState<SocialSource[]>([]);
  const [socialRules, setSocialRules] = useState<SocialAlertRule[]>([]);
  const [socialDeliveries, setSocialDeliveries] = useState<SocialDelivery[]>([]);
  const [socialRefreshKey, setSocialRefreshKey] = useState(0);
  const assets = overview?.assets ?? [];
  const selectedAsset = assets.find((asset) => asset.symbol === selectedSymbol) ?? assets[0];

  useEffect(() => {
    let active = true;
    restoreSession()
      .then((user) => {
        if (!active) return;
        setCurrentUser(user);
        setAuthState(user ? "authenticated" : "unauthenticated");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setAuthError(error instanceof Error ? error.message : "Could not restore the session.");
        setAuthState("unauthenticated");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    function expireSession() {
      setCurrentUser(null);
      setAuthState("unauthenticated");
    }
    window.addEventListener("auth-expired", expireSession);
    return () => window.removeEventListener("auth-expired", expireSession);
  }, []);

  useEffect(() => {
    if (authState !== "authenticated") return;
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
  }, [authState, refreshKey]);

  useEffect(() => {
    if (authState !== "authenticated" || !selectedSymbol || loadState !== "ready") return;
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
  }, [authState, loadState, selectedAsset?.asset_class, selectedSymbol]);

  useEffect(() => {
    if (authState !== "authenticated" || activePage !== "AI analysis" || !selectedSymbol) return;
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
  }, [activePage, authState, selectedSymbol]);

  useEffect(() => {
    if (authState !== "authenticated" || activePage !== "Alerts") return;
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
  }, [activePage, authState]);

  useEffect(() => {
    if (authState !== "authenticated" || activePage !== "Social intelligence") return;
    const controller = new AbortController();
    setSocialState("loading");
    setSocialError("");
    Promise.all([
      getSocialPosts({ asset: socialAsset.trim().toUpperCase() || undefined, limit: 50 }, controller.signal),
      getSocialStatistics(controller.signal),
      getSocialHealth(controller.signal),
      getSocialEvents(controller.signal),
      getSocialSources(controller.signal),
      getSocialAlertRules(controller.signal),
      getSocialNotificationHistory(controller.signal),
    ])
      .then(([postResponse, statistics, health, eventResponse, sourceResponse, ruleResponse, deliveryResponse]) => {
        setSocialPosts(postResponse.items);
        setSocialStats(statistics);
        setSocialHealth(health);
        setSocialEvents(eventResponse.items);
        setSocialSources(sourceResponse.items);
        setSocialRules(ruleResponse.items);
        setSocialDeliveries(deliveryResponse.items);
        setSocialState("ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setSocialState("error");
        setSocialError(error instanceof Error ? error.message : "Social intelligence is unavailable.");
      });
    return () => controller.abort();
  }, [activePage, authState, socialAsset, socialRefreshKey]);

  async function handleLogin(username: string, password: string): Promise<void> {
    setAuthError("");
    const user = await loginApi(username, password);
    setCurrentUser(user);
    setAuthState("authenticated");
  }

  async function handleLogout(): Promise<void> {
    setAuthError("");
    try {
      await logoutApi();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Could not complete sign out.");
    } finally {
      setCurrentUser(null);
      setAuthState("unauthenticated");
    }
  }

  const servicesAvailable = overview?.services.filter(
    (service) => service.status === "healthy",
  ).length ?? 0;
  const plannedPages = navigation.filter((item) => item.planned).map((item) => item.label);

  if (authState !== "authenticated" || !currentUser) {
    return (
      <AuthenticationScreen
        checking={authState === "checking"}
        error={authError}
        onLogin={handleLogin}
      />
    );
  }

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
            <button className="user-button" type="button" onClick={() => void handleLogout()}>
              <span className="avatar">{currentUser.username.slice(0, 2).toUpperCase()}</span>
              <span>{currentUser.username}</span>
              <span className="sign-out-label">Sign out</span>
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
          {activePage === "News intelligence" && <NewsPage />}
          {activePage === "Social intelligence" && (
            <SocialIntelligencePage
              asset={socialAsset}
              error={socialError}
              health={socialHealth}
              loadState={socialState}
              onAssetChange={setSocialAsset}
              posts={socialPosts}
              events={socialEvents}
              sources={socialSources}
              rules={socialRules}
              deliveries={socialDeliveries}
              statistics={socialStats}
              onDataChanged={() => setSocialRefreshKey((key) => key + 1)}
            />
          )}
          {plannedPages.includes(activePage) && <UnavailablePage title={activePage} />}
        </div>
      </main>
    </div>
  );
}

function AuthenticationScreen({
  checking,
  error,
  onLogin,
}: {
  checking: boolean;
  error: string;
  onLogin: (username: string, password: string) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setSubmitting(true);
    try {
      await onLogin(username, password);
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : "Sign in failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="brand-mark" aria-hidden="true">MI</div>
        <p className="eyebrow">Market Intelligence</p>
        <h1 id="auth-title">Sign in</h1>
        <p className="auth-description">
          {checking ? "Checking your local session..." : "Sign in to access your local market workspace."}
        </p>
        {!checking && (
          <form className="auth-form" onSubmit={(event) => void submit(event)}>
            <label>
              Username
              <input
                autoComplete="username"
                required
                minLength={3}
                maxLength={48}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            {(formError || error) && (
              <p className="auth-error" role="alert">{formError || error}</p>
            )}
            <button className="auth-submit" type="submit" disabled={submitting}>
              {submitting ? "Signing in..." : "Sign in"}
            </button>
          </form>
        )}
      </section>
    </main>
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

function SocialIntelligencePage({
  asset,
  error,
  health,
  loadState,
  onAssetChange,
  posts,
  statistics,
  events,
  sources,
  rules,
  deliveries,
  onDataChanged,
}: {
  asset: string;
  error: string;
  health: Awaited<ReturnType<typeof getSocialHealth>> | null;
  loadState: LoadState;
  onAssetChange: (value: string) => void;
  posts: SocialPost[];
  statistics: SocialStatistics | null;
  events: SocialEvent[];
  sources: SocialSource[];
  rules: SocialAlertRule[];
  deliveries: SocialDelivery[];
  onDataChanged: () => void;
}) {
  const [section, setSection] = useState("Feed");
  const [actionError, setActionError] = useState("");
  const [sourceSaving, setSourceSaving] = useState(false);
  const [ruleSaving, setRuleSaving] = useState(false);
  const [sourceAccountId, setSourceAccountId] = useState("");
  const [sourceUsername, setSourceUsername] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [sourceCategory, setSourceCategory] = useState("unknown");
  const [ruleName, setRuleName] = useState("");
  const [ruleAssets, setRuleAssets] = useState("");
  const [ruleCategory, setRuleCategory] = useState("");
  const [ruleScore, setRuleScore] = useState("60");
  const [ruleCooldown, setRuleCooldown] = useState("900");
  const sections = ["Feed", "Events", "Sources", "Alert rules", "Deliveries"];

  async function addSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSourceSaving(true);
    setActionError("");
    try {
      await createSocialSource({
        account_id: sourceAccountId.trim(),
        username: sourceUsername.trim(),
        display_name: sourceName.trim(),
        category: sourceCategory,
      });
      setSourceAccountId("");
      setSourceUsername("");
      setSourceName("");
      onDataChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not add social source.");
    } finally {
      setSourceSaving(false);
    }
  }

  async function addRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRuleSaving(true);
    setActionError("");
    try {
      await createSocialAlertRule({
        name: ruleName.trim(),
        minimum_score: Number(ruleScore),
        assets: ruleAssets.split(",").map((value) => value.trim().toUpperCase()).filter(Boolean),
        categories: ruleCategory ? [ruleCategory] : [],
        cooldown_seconds: Number(ruleCooldown),
        timezone: "UTC",
      });
      setRuleName("");
      onDataChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not add alert rule.");
    } finally {
      setRuleSaving(false);
    }
  }

  async function deactivateSource(id: number) {
    setActionError("");
    try {
      await deactivateSocialSource(id);
      onDataChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not deactivate social source.");
    }
  }

  return (
    <div className="social-page">
      <section className="metric-grid" aria-label="Social intelligence summary">
        <Metric label="Collected posts" value={statistics ? String(statistics.posts) : "--"} detail="Stored X posts" />
        <Metric label="Classified events" value={statistics ? String(statistics.events) : "--"} detail="Rule-based heuristic" />
        <Metric label="Active sources" value={statistics ? String(statistics.active_sources) : "--"} detail="Configured source registry" />
        <Metric label="AI analysis" value={health?.ai_status ?? "unknown"} detail="No generated summaries" />
      </section>
      <section className="panel social-panel">
        <div className="social-section-tabs" role="tablist" aria-label="Social intelligence sections">
          {sections.map((name) => (
            <button
              aria-selected={section === name}
              className={section === name ? "is-selected" : ""}
              key={name}
              onClick={() => setSection(name)}
              role="tab"
              type="button"
            >
              {name}
            </button>
          ))}
        </div>
        {actionError && <p className="social-action-error" role="alert">{actionError}</p>}
        {section === "Feed" && (
          <>
        <div className="social-toolbar">
          <label className="news-filter">
            <span>Asset</span>
            <input
              value={asset}
              onChange={(event) => onAssetChange(event.target.value)}
              maxLength={24}
              placeholder="All tracked assets"
            />
          </label>
          <span className="result-count">
            X collection: {health?.status ?? "loading"} · Last success: {formatTime(health?.last_success_at ?? null)}
          </span>
        </div>
        <p className="social-disclaimer">
          Posts and event labels are not independently verified or investment advice. Whale activity is unverified;
          AI analysis is {health?.ai_status ?? "not reported"}.
          {health?.notifications && ` Notifications — ntfy: ${health.notifications.ntfy ?? "unknown"}, Telegram: ${health.notifications.telegram ?? "unknown"}.`}
        </p>
        {loadState === "loading" && <div className="panel-empty">Loading social posts and status.</div>}
        {loadState === "error" && (
          <div className="notice notice-error news-error" role="alert">
            <AlertTriangle size={18} aria-hidden="true" />
            <div><strong>Social service unavailable</strong><p>{error}</p></div>
          </div>
        )}
        {loadState === "ready" && posts.length === 0 && (
          <div className="panel-empty">No collected posts match this filter. X collection may be disabled or not configured.</div>
        )}
        {loadState === "ready" && posts.length > 0 && (
          <div className="social-post-list">
            {posts.map((post) => {
              const postUrl = safeHttpUrl(post.post_url);
              return (
                <article className="social-post" key={post.id}>
                  <div className="social-post-meta">
                    <span>@{post.username ?? "unknown source"}</span>
                    <span>{post.source_category?.replaceAll("_", " ") ?? "unclassified source"}</span>
                    <time dateTime={post.published_at}>{formatTime(post.published_at)}</time>
                    <span className="unverified-tag">unverified</span>
                  </div>
                  <p className="social-post-text">{post.text}</p>
                  {post.events.length > 0 && (
                    <div className="social-event-tags">
                      {post.events.map((event) => (
                        <span className={`social-event-tag ${event.priority}`} key={`${post.id}-${event.category}`}>
                          {event.category.replaceAll("_", " ")} · {event.sentiment} · {event.priority}
                          {event.unverified_claim ? " · claim unverified" : ""}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="social-post-footer">
                    <span>{post.assets.length ? post.assets.join(", ") : "No tracked asset matched"}</span>
                    {postUrl && <a href={postUrl} target="_blank" rel="noreferrer">Open post</a>}
                  </div>
                </article>
              );
            })}
          </div>
        )}
          </>
        )}
        {section === "Events" && (
          <div className="social-post-list">
            {loadState === "loading" && <div className="panel-empty">Loading event timeline.</div>}
            {loadState === "ready" && events.length === 0 && <div className="panel-empty">No classified events are stored.</div>}
            {loadState === "ready" && events.map((event) => (
              <article className="social-post" key={event.id}>
                <div className="social-post-meta">
                  <span>{event.symbol ?? "CRYPTO"}</span>
                  <span className={`social-event-tag ${event.priority}`}>{event.category.replaceAll("_", " ")}</span>
                  <span>{event.sentiment} · {event.priority} · {event.priority_score}/100</span>
                  <time>{formatTime(event.created_at)}</time>
                </div>
                <p className="social-post-text">{event.text}</p>
                <div className="social-post-footer">
                  <span>{event.unverified_claim ? "Claim unverified" : "Not independently verified"}</span>
                  {safeHttpUrl(event.post_url) && <a href={safeHttpUrl(event.post_url) ?? undefined} target="_blank" rel="noreferrer">Open post</a>}
                </div>
              </article>
            ))}
          </div>
        )}
        {section === "Sources" && (
          <>
            <form className="social-management-form" onSubmit={(event) => void addSource(event)}>
              <label>Account ID<input required value={sourceAccountId} onChange={(event) => setSourceAccountId(event.target.value)} maxLength={120} /></label>
              <label>Username<input required value={sourceUsername} onChange={(event) => setSourceUsername(event.target.value)} maxLength={80} /></label>
              <label>Display name<input required value={sourceName} onChange={(event) => setSourceName(event.target.value)} maxLength={120} /></label>
              <label>Source type<select value={sourceCategory} onChange={(event) => setSourceCategory(event.target.value)}>
                {["official_project", "official_exchange", "regulator", "news", "analyst", "influencer", "community", "unknown"].map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}
              </select></label>
              <button className="text-button" disabled={sourceSaving} type="submit">{sourceSaving ? "Adding..." : "Add source"}</button>
            </form>
            <div className="social-post-list">
              {sources.length === 0 && <div className="panel-empty">No sources have been registered.</div>}
              {sources.map((source) => (
                <article className="social-post social-source-row" key={source.id}>
                  <div><strong>@{source.username}</strong><span>{source.display_name} · {source.category.replaceAll("_", " ")} · reputation {source.reputation_score}</span></div>
                  <span className="unverified-tag">{source.verified ? "platform verified; claim unverified" : "unverified"}</span>
                  {source.active && <button className="text-button" type="button" onClick={() => void deactivateSource(source.id)}>Deactivate</button>}
                  {!source.active && <span className="result-count">inactive</span>}
                </article>
              ))}
            </div>
          </>
        )}
        {section === "Alert rules" && (
          <>
            <form className="social-management-form" onSubmit={(event) => void addRule(event)}>
              <label>Rule name<input required value={ruleName} onChange={(event) => setRuleName(event.target.value)} maxLength={80} /></label>
              <label>Assets, comma-separated<input value={ruleAssets} onChange={(event) => setRuleAssets(event.target.value)} placeholder="BTC, ETH" /></label>
              <label>Event category<select value={ruleCategory} onChange={(event) => setRuleCategory(event.target.value)}>
                <option value="">All categories</option>
                {["EXCHANGE_LISTING", "TOKEN_UNLOCK", "WHALE_ACTIVITY", "PARTNERSHIP", "NETWORK_UPGRADE", "ETF_NEWS", "REGULATORY_NEWS", "SECURITY_HACK", "EXPLOIT", "SCAM_WARNING", "EXCHANGE_OUTAGE", "AIRDROP", "GENERAL_CRYPTO_NEWS"].map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}
              </select></label>
              <label>Minimum score<input required type="number" min="0" max="100" value={ruleScore} onChange={(event) => setRuleScore(event.target.value)} /></label>
              <label>Cooldown seconds<input required type="number" min="0" max="604800" value={ruleCooldown} onChange={(event) => setRuleCooldown(event.target.value)} /></label>
              <button className="text-button" disabled={ruleSaving} type="submit">{ruleSaving ? "Adding..." : "Add rule"}</button>
            </form>
            <div className="social-post-list">
              {rules.length === 0 && <div className="panel-empty">No alert rules are configured.</div>}
              {rules.map((rule) => (
                <article className="social-post" key={rule.id}>
                  <strong>{rule.name}</strong>
                  <p className="social-post-text">Minimum score {rule.minimum_score} · assets {rule.assets.join(", ") || "all"} · categories {rule.categories.join(", ") || "all"}</p>
                  <span className="result-count">{rule.enabled ? "enabled" : "disabled"} · cooldown {rule.cooldown_seconds}s · {rule.timezone}</span>
                </article>
              ))}
            </div>
          </>
        )}
        {section === "Deliveries" && (
          <div className="table-wrap">
            <table className="asset-table">
              <thead><tr><th>Asset</th><th>Event</th><th>Channel</th><th>Status</th><th>Attempts</th><th>Updated</th></tr></thead>
              <tbody>
                {deliveries.map((delivery) => (
                  <tr key={delivery.id}>
                    <td>{delivery.symbol ?? "CRYPTO"}</td>
                    <td>{delivery.category.replaceAll("_", " ")}</td>
                    <td>{delivery.channel}</td>
                    <td>{delivery.status.replaceAll("_", " ")}</td>
                    <td>{delivery.attempts}</td>
                    <td>{formatTime(delivery.delivered_at ?? delivery.updated_at)}</td>
                  </tr>
                ))}
                {deliveries.length === 0 && <tr><td colSpan={6}>No notification deliveries have been recorded.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function NewsPage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [asset, setAsset] = useState("");
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [response, setResponse] = useState<NewsResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setLoadState("loading");
      setError("");
      getNews(
        {
          q: query.trim(),
          category,
          asset: asset.trim(),
          limit: 50,
        },
        controller.signal,
      )
        .then((result) => {
          setResponse(result);
          setLoadState("ready");
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return;
          setLoadState("error");
          setError(cause instanceof Error ? cause.message : "News feed is unavailable.");
        });
    }, 200);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [asset, category, query]);

  const items = response?.items ?? [];
  return (
    <section className="panel news-panel">
      <div className="news-toolbar">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <span className="visually-hidden">Search news</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search headlines and summaries"
          />
        </label>
        <label className="news-filter">
          <span>Category</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">All categories</option>
            {(response?.categories ?? []).map((value) => (
              <option value={value} key={value}>{value.replaceAll("_", " ")}</option>
            ))}
          </select>
        </label>
        <label className="news-filter news-asset-filter">
          <span>Asset</span>
          <input
            value={asset}
            onChange={(event) => setAsset(event.target.value)}
            maxLength={24}
            placeholder="e.g. AAPL"
          />
        </label>
        <span className="result-count">
          {response ? `${response.count} articles` : "News feed"}
        </span>
      </div>
      <p className="news-disclaimer">
        Source-provided RSS/Atom content only. Categories are rule-based; no AI summary or sentiment is generated.
      </p>
      {loadState === "loading" && <div className="panel-empty">Loading collected articles.</div>}
      {loadState === "error" && (
        <div className="notice notice-error news-error" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <div><strong>News service unavailable</strong><p>{error}</p></div>
        </div>
      )}
      {loadState === "ready" && items.length === 0 && (
        <div className="panel-empty">No collected articles match these filters.</div>
      )}
      {loadState === "ready" && items.length > 0 && (
        <div className="news-list">
          {items.map((article) => <NewsCard article={article} key={article.id} onSelectAsset={setAsset} />)}
        </div>
      )}
    </section>
  );
}

function NewsCard({
  article,
  onSelectAsset,
}: {
  article: NewsArticle;
  onSelectAsset: (symbol: string) => void;
}) {
  const articleUrl = safeHttpUrl(article.url);
  return (
    <article className="news-card">
      <div className="news-card-meta">
        <span className="news-category">{article.category.replaceAll("_", " ")}</span>
        <span>{article.source}</span>
        <time dateTime={article.published_at ?? article.collected_at}>
          {formatTime(article.published_at ?? article.collected_at)}
        </time>
      </div>
      <h2>
        {articleUrl
          ? <a href={articleUrl} target="_blank" rel="noreferrer">{article.title}</a>
          : article.title}
      </h2>
      {article.summary && <p className="news-summary">{article.summary}</p>}
      {article.content && (
        <details className="news-details">
          <summary>Source content</summary>
          <p>{article.content}</p>
        </details>
      )}
      <div className="news-card-footer">
        {article.related_assets.length > 0 ? (
          <div className="news-assets" aria-label="Related assets">
            {article.related_assets.map((symbol) => (
              <button type="button" onClick={() => onSelectAsset(symbol)} key={symbol}>{symbol}</button>
            ))}
          </div>
        ) : <span className="news-no-assets">No tracked asset matched</span>}
        <span>{article.language ?? "Language not reported"}</span>
      </div>
    </article>
  );
}

function safeHttpUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
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