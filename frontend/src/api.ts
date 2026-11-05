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

export interface NewsArticle {
  id: number;
  url: string;
  title: string;
  summary: string | null;
  content: string | null;
  published_at: string | null;
  source: string;
  language: string | null;
  category: string;
  related_assets: string[];
  collected_at: string;
}

export interface NewsResponse {
  items: NewsArticle[];
  count: number;
  limit: number;
  offset: number;
  categories: string[];
}

export interface SocialPost {
  id: number;
  text: string;
  post_url: string;
  published_at: string;
  username: string | null;
  source_category: string | null;
  verification_status: "unverified";
  assets: string[];
  events: Array<{
    category: string;
    sentiment: string;
    priority: string;
    priority_score: number;
    unverified_claim: boolean;
  }>;
}

export interface SocialStatistics {
  posts: number;
  events: number;
  active_sources: number;
  pending_analysis: number;
  categories: Record<string, number>;
  sentiments: Record<string, number>;
  priorities: Record<string, number>;
  ai_status: "disabled" | "not_configured" | "configured";
}

export interface SocialEvent {
  id: number;
  symbol: string | null;
  category: string;
  sentiment: string;
  priority: string;
  priority_score: number;
  unverified_claim: boolean;
  created_at: string;
  text: string;
  post_url: string;
  username: string | null;
  verification_status: "unverified";
}

export interface SocialSource {
  id: number;
  account_id: string;
  username: string;
  display_name: string;
  category: string;
  verified: boolean;
  reputation_score: number;
  supported_assets: string[];
  active: boolean;
}

export interface SocialAlertRule {
  id: number;
  name: string;
  enabled: boolean;
  minimum_score: number;
  assets: string[];
  categories: string[];
  cooldown_seconds: number;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  timezone: string;
}

export interface SocialDelivery {
  id: number;
  channel: string;
  status: string;
  attempts: number;
  error_code: string | null;
  delivered_at: string | null;
  updated_at: string;
  symbol: string | null;
  category: string;
  priority: string;
  post_url: string;
}

export class ApiError extends Error {
  constructor(readonly statusCode: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const REFRESH_TOKEN_KEY = "market-intelligence-refresh-token";
let accessToken: string | null = null;
let refreshInFlight: Promise<AuthUser | null> | null = null;

export interface AuthUser {
  id: number;
  username: string;
  role: "ADMIN" | "USER" | "READ_ONLY";
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

function saveTokens(tokens: TokenResponse): AuthUser {
  accessToken = tokens.access_token;
  window.sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  return tokens.user;
}

function clearTokens(): void {
  accessToken = null;
  window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
}

async function responseError(response: Response): Promise<ApiError> {
  let message = `Market service returned ${response.status}`;
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // Keep the status-based message for non-JSON responses.
  }
  return new ApiError(response.status, message);
}

async function refreshTokens(): Promise<AuthUser | null> {
  const refreshToken = window.sessionStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) return null;
  const response = await fetch(`${API_BASE}/api/auth/refresh`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) {
    clearTokens();
    if (response.status === 401) return null;
    throw await responseError(response);
  }
  return saveTokens(await response.json() as TokenResponse);
}

async function authenticatedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  let response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    if (!refreshInFlight) {
      refreshInFlight = refreshTokens().finally(() => {
        refreshInFlight = null;
      });
    }
    const refreshedUser = await refreshInFlight;
    if (refreshedUser && accessToken) {
      headers.set("Authorization", `Bearer ${accessToken}`);
      response = await fetch(`${API_BASE}${path}`, { ...init, headers });
    } else {
      clearTokens();
      window.dispatchEvent(new Event("auth-expired"));
    }
    if (response.status === 401) window.dispatchEvent(new Event("auth-expired"));
  }
  return response;
}

export async function restoreSession(): Promise<AuthUser | null> {
  if (!window.sessionStorage.getItem(REFRESH_TOKEN_KEY)) return null;
  if (!refreshInFlight) {
    refreshInFlight = refreshTokens().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

export async function login(
  username: string,
  password: string,
): Promise<AuthUser> {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw await responseError(response);
  return saveTokens(await response.json() as TokenResponse);
}

export async function logout(): Promise<void> {
  try {
    const response = await authenticatedFetch("/api/auth/logout", { method: "POST" });
    if (!response.ok) throw await responseError(response);
  } finally {
    clearTokens();
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(path, {
    signal,
  });

  if (!response.ok) {
    throw await responseError(response);
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

export function getNews(
  filters: {
    q?: string;
    category?: string;
    asset?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<NewsResponse> {
  const query = new URLSearchParams();
  if (filters.q) query.set("q", filters.q);
  if (filters.category) query.set("category", filters.category);
  if (filters.asset) query.set("asset", filters.asset);
  query.set("limit", String(filters.limit ?? 50));
  query.set("offset", String(filters.offset ?? 0));
  return request<NewsResponse>(`/api/v1/news?${query}`, signal);
}

export function getSocialPosts(
  filters: { asset?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<{ items: SocialPost[]; count: number }> {
  const query = new URLSearchParams({ limit: String(filters.limit ?? 50) });
  if (filters.asset) query.set("asset", filters.asset);
  return request<{ items: SocialPost[]; count: number }>(`/api/social/posts?${query}`, signal);
}

export function getSocialStatistics(signal?: AbortSignal): Promise<SocialStatistics> {
  return request<SocialStatistics>("/api/social/statistics", signal);
}

export function getSocialEvents(signal?: AbortSignal): Promise<{ items: SocialEvent[]; count: number }> {
  return request("/api/social/events?limit=50", signal);
}

export function getSocialSources(signal?: AbortSignal): Promise<{ items: SocialSource[]; count: number }> {
  return request("/api/social/sources?limit=100", signal);
}

export function getSocialAlertRules(signal?: AbortSignal): Promise<{ items: SocialAlertRule[] }> {
  return request("/api/social/alert-rules", signal);
}

export function getSocialNotificationHistory(
  signal?: AbortSignal,
): Promise<{ items: SocialDelivery[]; count: number }> {
  return request("/api/social/notification-history?limit=100", signal);
}

async function mutate<T>(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  data?: object,
): Promise<T> {
  const response = await authenticatedFetch(path, {
    method,
    headers: data ? { "Content-Type": "application/json" } : undefined,
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}

export function createSocialSource(source: {
  account_id: string;
  username: string;
  display_name: string;
  category: string;
}): Promise<SocialSource> {
  return mutate<SocialSource>("/api/social/sources", "POST", source);
}

export function deactivateSocialSource(id: number): Promise<{ status: string }> {
  return mutate<{ status: string }>(`/api/social/sources/${id}`, "DELETE");
}

export function createSocialAlertRule(rule: {
  name: string;
  minimum_score: number;
  assets: string[];
  categories: string[];
  cooldown_seconds: number;
  timezone: string;
}): Promise<SocialAlertRule> {
  return mutate<SocialAlertRule>("/api/social/alert-rules", "POST", rule);
}

export function getSocialHealth(signal?: AbortSignal) {
  return request<{
  status: string;
  enabled: boolean;
  configured: boolean;
  last_success_at: string | null;
  ai_status: "disabled" | "not_configured" | "configured";
  notifications: Record<string, string>;
  }>("/api/social/health", signal);
}