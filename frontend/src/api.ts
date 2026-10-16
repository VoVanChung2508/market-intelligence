export type Freshness = "fresh" | "stale" | "unavailable";
export type ServiceState = "healthy" | "degraded" | "unavailable";

export interface AssetSummary {
  symbol: string;
  name: string;
  asset_class: "crypto" | "equity";
  price: number | null;
  change_pct: number | null;
  volume: number | null;
  currency: string;
  source: string;
  timestamp: string | null;
  freshness: Freshness;
}

export interface ServiceHealth {
  name: string;
  status: ServiceState;
  detail: string;
  updated_at: string | null;
}

export interface OverviewResponse {
  generated_at: string;
  data_status: Freshness;
  assets: AssetSummary[];
  services: ServiceHealth[];
}

export interface PriceBar {
  timestamp: string;
  close: number;
}

export interface PriceBarsResponse {
  symbol: string;
  interval: string;
  bars: PriceBar[];
}

export interface SplitMetrics {
  accuracy: number;
  balanced_accuracy: number;
  brier_score: number;
}

export interface AnalysisReport {
  model_version: string;
  problem: string;
  symbol: string;
  observations: number;
  labeled_samples: number;
  trained_at: string;
  source: string;
  split: {
    train: [string, string];
    validation: [string, string];
    test: [string, string];
  };
  metrics: { validation: SplitMetrics; test: SplitMetrics };
  majority_baseline: { validation: SplitMetrics; test: SplitMetrics };
  minimum_required_lift: number;
  gate_passed: boolean;
  latest_direction: "up" | "down" | null;
  confidence_probability: null;
}

export type AnalysisResponse =
  | { available: false; symbol: string; detail: string }
  | { available: true; report: AnalysisReport };

export interface AlertEvent {
  symbol: string;
  interval: string;
  rule: string;
  timestamp: string;
  change_pct: number;
  delivery_status: "sent" | "failed" | "not_configured";
  delivery_error: string | null;
}

export interface AlertsResponse {
  items: AlertEvent[];
  threshold_pct: number;
  ntfy_configured: boolean;
}

export class ApiError extends Error {
  constructor(readonly statusCode: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, `Market service returned ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getOverview(signal?: AbortSignal): Promise<OverviewResponse> {
  return request<OverviewResponse>("/api/v1/overview", signal);
}

export function getPriceBars(
  symbol: string,
  interval: "1h" | "1d",
  signal?: AbortSignal,
): Promise<PriceBarsResponse> {
  const query = new URLSearchParams({ interval, limit: "96" });
  return request<PriceBarsResponse>(
    `/api/v1/assets/${encodeURIComponent(symbol)}/bars?${query}`,
    signal,
  );
}

export function getAnalysisReport(
  symbol: string,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  return request<AnalysisResponse>(
    `/api/v1/analysis/${encodeURIComponent(symbol)}`,
    signal,
  );
}

export function getAlerts(signal?: AbortSignal): Promise<AlertsResponse> {
  return request<AlertsResponse>("/api/v1/alerts?limit=100", signal);
}