"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { FlaskConical, Pencil, Play, Plus, RefreshCw, Search, Square } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { Modal } from "@/components/ui/dialog";
import type { PagePayload } from "@/contracts/page";
import type {
  AvailableModel,
  AvailableModels,
  CallResponse,
  CatalogItem,
  CatalogPage,
  ModelEvent,
  ProviderCapabilities,
  ProviderPage,
  ProviderView,
  TestSummary,
} from "@/contracts/models";
import * as dto from "@/contracts/models";

const KIND_LABEL: Record<string, string> = { llm: "生成模型", decision: "决策模型" };
const CAPABILITY_LABEL: Record<string, string> = {
  sync: "同步",
  stream: "流式",
  tools: "工具调用",
  thinking: "Thinking",
  json_output: "JSON",
  schema_output: "Schema",
};
const ERROR_LABEL: Record<string, string> = {
  model_authentication_failed: "模型认证失败，请检查 API Key 与接入权限。",
  model_rate_limited: "调用过于频繁，请稍后重试。",
  model_timeout: "本次调用已超时，请检查服务状态或稍后重试。",
  model_unavailable_upstream: "供应商暂时不可用，请稍后重试。",
  model_request_rejected: "供应商拒绝了本次请求，请检查模型名称与接入地址。",
  stream_interrupted: "输出未完成。你可以检查当前结果后重新发送。",
  output_truncated: "输出被截断，结果未完整返回。",
  model_refused: "模型拒绝了本次请求。",
  empty_output: "模型没有返回内容。",
  invalid_json: "输出不是有效 JSON，请调整输出要求。",
  schema_validation_failed: "输出不符合指定结构，请检查结果或调整输出要求。",
  invalid_tool_arguments: "工具参数不完整，未作为可执行结果返回。",
  continuation_invalid: "上一轮工具状态已失效，请重新开始测试。",
  continuation_too_large: "工具续接内容过大，请减少输入后重试。",
  response_too_large: "返回内容超过上限。",
  capability_not_supported: "当前模型不支持所选能力或参数组合。",
  provider_protocol_error: "供应商返回了无法识别的响应。",
  validation_failed: "请检查输入与参数组合。",
  internal_error: "调用未能完成，请稍后重试。",
  record_write_failed: "调用结果可能已产生，请按调用 ID 核对。",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function apiError(code?: string | null): string {
  return (code && ERROR_LABEL[code]) || "调用未能完成，请稍后重试。";
}

function capabilityText(capability: { state: string; reason: string }): string {
  if (capability.state === "supported") return "支持";
  if (capability.state === "unknown") return "待核验";
  return capability.reason || "不支持";
}

function summarizeTest(summary: TestSummary | null | undefined): string {
  if (!summary) return "未测试";
  const label =
    { completed: "成功", failed: "失败", canceled: "已停止", incomplete: "未完成" }[
      summary.status
    ] ?? summary.status;
  const capabilities = summary.capabilities ?? [];
  const parts = capabilities.length ? ` · ${capabilities.join(" / ")}` : "";
  return `${label}${parts}`;
}

export function ModelsPages({ payload }: { payload: PagePayload }) {
  // "Agent 与能力 → 模型路由" is the single entry point. Platform administrators manage
  // providers there; other tenant administrators only see the read-only summary.
  return payload.session.isPlatformAdmin ? (
    <PlatformModels payload={payload} />
  ) : (
    <TenantModels payload={payload} />
  );
}

function TenantModels({ payload }: { payload: PagePayload }) {
  const api = useDocumentSession();
  const [models, setModels] = useState<AvailableModels | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const canInvoke = payload.session.capabilities.includes("models.invoke");
  const scope = payload.session.scope;
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (scope.tenantId) params.set("tenantId", scope.tenantId);
      if (scope.workspaceId) params.set("workspaceId", scope.workspaceId);
      setModels(await api.request<AvailableModels>(`models/available?${params}`));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, [api, scope.tenantId, scope.workspaceId]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>模型路由</h2>
          <p className="muted">
            平台统一维护模型接入。这里仅展示当前授权的生成模型摘要，不包含端点与凭据。
          </p>
        </div>
      </div>
      {!canInvoke && (
        <p className="notice">
          你暂无使用模型的权限，请联系租户管理员。进入页面不自动获得调用权限。
        </p>
      )}
      {loading && <p className="muted">正在加载可选模型…</p>}
      {error && (
        <p role="alert" className="error">
          {error}{" "}
          <button className="secondary compact" onClick={() => void load()}>
            重新加载
          </button>
        </p>
      )}
      {!loading && !error && models?.items.length === 0 && (
        <div className="empty-state">
          <div className="empty-symbol" aria-hidden>
            ◇
          </div>
          <h3>暂无可用模型，请联系管理员。</h3>
          <p>平台启用并开放到 Chat 后，模型会出现在分析页的主模型选择中。</p>
        </div>
      )}
      <div className="model-cards">
        {models?.items.map((model) => (
          <article className="model-card" key={model.id}>
            <header>
              <div>
                <h3>{model.alias}</h3>
                <p className="muted mono">{model.api_model}</p>
              </div>
              <span className="status">{KIND_LABEL[model.kind]}</span>
            </header>
            <CapabilityGrid capabilities={model.capabilities} compact />
          </article>
        ))}
      </div>
      <p className="muted">
        决策模型仅在管理端配置与测试，不出现在 Chat 列表；直接提交其标识也会在出站前被拒绝。
      </p>
    </section>
  );
}

function PlatformModels({ payload }: { payload: PagePayload }) {
  const api = useDocumentSession();
  const [page, setPage] = useState<ProviderPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [editor, setEditor] = useState<{ provider?: ProviderView } | null>(null);
  const [tester, setTester] = useState<ProviderView | null>(null);
  const [busyId, setBusyId] = useState("");
  const load = useCallback(
    async (targetPage = pageNumber) => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({
          q: query,
          page: String(targetPage),
          page_size: "20",
        });
        setPage(await api.request<ProviderPage>(`platform/models?${params}`));
      } catch (cause) {
        setError(errorMessage(cause));
      } finally {
        setLoading(false);
      }
    },
    [api, pageNumber, query],
  );
  useEffect(() => {
    const timer = window.setTimeout(() => void load(pageNumber), 0);
    return () => window.clearTimeout(timer);
  }, [load, pageNumber]);

  async function patch(
    provider: ProviderView,
    changes: Record<string, unknown>,
    confirmation?: string,
  ) {
    if (confirmation && !window.confirm(confirmation)) return;
    if (busyId) return;
    setBusyId(provider.id);
    setError("");
    setNotice("");
    try {
      await api.request(`platform/models/${provider.id}`, "PATCH", {
        operation_id: crypto.randomUUID(),
        expected_version: provider.version,
        ...changes,
      });
      setNotice("模型配置已保存。");
      await load(pageNumber);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusyId("");
    }
  }

  const total = page?.total ?? 0;
  const pageSize = page?.page_size ?? 20;
  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>模型路由</h2>
            <p className="muted">
              平台统一维护模型接入、能力测试与 Chat 开放范围。保存配置不会自动发起调用。
            </p>
          </div>
          <div className="actions">
            <button className="secondary" onClick={() => void load(pageNumber)} disabled={loading}>
              <RefreshCw size={15} /> 刷新
            </button>
            <button onClick={() => setEditor({})}>
              <Plus size={16} /> 添加 Provider
            </button>
          </div>
        </div>
        <form
          className="list-filter"
          onSubmit={(event) => {
            event.preventDefault();
            setPageNumber(1);
            setQuery(search.trim());
          }}
        >
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            maxLength={200}
            placeholder="搜索别称或模型名称"
            aria-label="搜索模型"
          />
          <button className="secondary" type="submit">
            <Search size={15} /> 搜索
          </button>
        </form>
        {notice && <p className="notice">{notice}</p>}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {loading ? (
          <p className="muted">正在加载模型配置…</p>
        ) : page?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>别称</th>
                  <th>模型</th>
                  <th>类型</th>
                  <th>接入 / Chat</th>
                  <th>凭据</th>
                  <th>最近测试</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((provider) => (
                  <tr key={provider.id}>
                    <td>
                      <strong>{provider.alias}</strong>
                      <div className="muted mono">v{provider.version}</div>
                    </td>
                    <td>
                      <span className="mono">{provider.api_model}</span>
                      <div className="muted">{provider.vendor}</div>
                    </td>
                    <td>
                      <span className="status">{KIND_LABEL[provider.kind]}</span>
                    </td>
                    <td>
                      <label className="inline-switch">
                        <input
                          type="checkbox"
                          checked={provider.enabled}
                          disabled={busyId === provider.id}
                          onChange={() =>
                            void patch(
                              provider,
                              { enabled: !provider.enabled },
                              !provider.enabled
                                ? undefined
                                : `停用“${provider.alias}”后，将拒绝新的 Chat 调用。已发出的调用继续按原配置处理。`,
                            )
                          }
                        />
                        启用
                      </label>
                      <label className="inline-switch">
                        <input
                          type="checkbox"
                          checked={provider.chat_visible}
                          disabled={
                            busyId === provider.id ||
                            provider.kind === "decision" ||
                            !provider.chat_supported
                          }
                          onChange={() =>
                            void patch(
                              provider,
                              { chat_visible: !provider.chat_visible },
                              !provider.chat_visible
                                ? undefined
                                : `从 Chat 中隐藏“${provider.alias}”后，用户将无法发起新的调用。管理员仍可测试。`,
                            )
                          }
                        />
                        展示
                      </label>
                      {provider.kind === "decision" && (
                        <div className="muted">决策模型仅支持管理端测试</div>
                      )}
                    </td>
                    <td>
                      <div>{provider.has_api_key ? "API Key 已配置" : "使用供应商原生认证"}</div>
                      <div className="muted">
                        {provider.has_connection_params ? "含连接参数" : "默认连接参数"}
                      </div>
                    </td>
                    <td>
                      <span className="muted">{summarizeTest(provider.latest_test)}</span>
                      {provider.latest_test?.stale && (
                        <div className="stale-label">配置已变化，请重新测试</div>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button className="secondary compact" onClick={() => setTester(provider)}>
                          <FlaskConical size={14} /> 测试
                        </button>
                        <button
                          className="secondary compact"
                          onClick={() => setEditor({ provider })}
                        >
                          <Pencil size={14} /> 编辑
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">
            <div className="empty-symbol" aria-hidden>
              ◇
            </div>
            <h3>{query ? "没有匹配的模型配置。" : "暂无模型 Provider。添加后可进行接入测试。"}</h3>
            <p>{query ? "尝试更换搜索词。" : "保存配置后默认停用且不在 Chat 展示。"}</p>
          </div>
        )}
        <div className="pagination">
          <span>
            共 {total} 条 · 第 {pageNumber} 页
          </span>
          <div className="actions">
            <button
              className="secondary compact"
              disabled={loading || pageNumber <= 1}
              onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
            >
              上一页
            </button>
            <button
              className="secondary compact"
              disabled={loading || pageNumber * pageSize >= total}
              onClick={() => setPageNumber((value) => value + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      </section>
      {editor && (
        <ProviderEditor
          provider={editor.provider}
          onClose={() => setEditor(null)}
          onSaved={(message) => {
            setEditor(null);
            setNotice(message);
            void load(pageNumber);
          }}
          onReload={() => {
            setEditor(null);
            void load(pageNumber);
          }}
        />
      )}
      {tester && <ModelTestPanel provider={tester} onClose={() => setTester(null)} />}
      <section className="panel">
        <h3>权限与边界</h3>
        <p className="muted">
          {payload.session.isPlatformAdmin
            ? "平台管理员可管理全部模型配置；业务调用仍需有效租户身份与“使用模型”权限。"
            : "当前账号没有平台模型管理权限。"}
        </p>
      </section>
    </>
  );
}

function CapabilityGrid({
  capabilities,
  compact = false,
}: {
  capabilities: ProviderCapabilities;
  compact?: boolean;
}) {
  const entries = (
    ["sync", "stream", "tools", "thinking", "json_output", "schema_output"] as const
  ).map((key) => ({ key, capability: capabilities[key] }));
  return (
    <div className={`capability-grid${compact ? " compact" : ""}`}>
      {entries.map(({ key, capability }) => (
        <span
          key={key}
          className={`capability ${capability.state}`}
          title={capability.reason || CAPABILITY_LABEL[key]}
        >
          {CAPABILITY_LABEL[key]} · {capabilityText(capability)}
        </span>
      ))}
      {(capabilities.reasoning_efforts ?? []).length > 0 && (
        <span className="capability supported">
          强度 · {(capabilities.reasoning_efforts ?? []).join(" / ")}
        </span>
      )}
    </div>
  );
}

type JsonObject = NonNullable<dto.ProviderCreateInput["connection_params"]>;

function parseJsonObject(text: string, label: string): JsonObject {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label}不是有效 JSON。`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label}必须是 JSON 对象。`);
  return value as JsonObject;
}

function ProviderEditor({
  provider,
  onClose,
  onSaved,
  onReload,
}: {
  provider?: ProviderView;
  onClose: () => void;
  onSaved: (message: string) => void;
  onReload: () => void;
}) {
  const api = useDocumentSession();
  const editing = Boolean(provider);
  const [catalog, setCatalog] = useState<CatalogPage | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [catalogOpen, setCatalogOpen] = useState(!editing);
  const [catalogId, setCatalogId] = useState(provider?.catalog_id ?? "");
  const [apiModel, setApiModel] = useState(provider?.api_model ?? "");
  const [alias, setAlias] = useState(provider?.alias ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [paramsText, setParamsText] = useState("");
  const [enabled, setEnabled] = useState(provider?.enabled ?? false);
  const [chatVisible, setChatVisible] = useState(provider?.chat_visible ?? false);
  const [kind, setKind] = useState<"llm" | "decision">(provider?.kind ?? "llm");
  const [capabilities, setCapabilities] = useState<ProviderCapabilities | null>(
    provider?.capabilities ?? null,
  );
  const [chatSupported, setChatSupported] = useState(provider?.chat_supported ?? true);
  const [selected, setSelected] = useState<CatalogItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);

  useEffect(() => {
    api.setDirty(true);
    return () => api.setDirty(false);
  }, [api]);

  const loadCatalog = useCallback(
    async (q: string) => {
      setCatalogLoading(true);
      setCatalogError("");
      try {
        const params = new URLSearchParams({ q, page: "1", page_size: "20" });
        setCatalog(await api.request<CatalogPage>(`platform/models/catalog?${params}`));
      } catch (cause) {
        setCatalogError(errorMessage(cause));
      } finally {
        setCatalogLoading(false);
      }
    },
    [api],
  );
  useEffect(() => {
    if (!catalogOpen) return;
    const timer = window.setTimeout(() => void loadCatalog(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [catalogOpen, loadCatalog, search]);

  function choose(item: CatalogItem) {
    setCatalogId(item.id);
    setApiModel(item.api_model);
    setSelected(item);
    setKind(item.kind === "decision" ? "decision" : "llm");
    setCapabilities(item.capabilities);
    setChatSupported(item.chat_supported);
    if (item.base_url) setBaseUrl(item.base_url);
    if (item.kind === "decision") setChatVisible(false);
    setCatalogOpen(false);
  }

  function onCatalogIdChange(value: string) {
    setCatalogId(value.trim());
    setSelected(null);
    const vendor = value.split("/")[0];
    const nextKind = ["aliyun", "typesafe", "systemone"].includes(vendor) ? "decision" : "llm";
    setKind(nextKind);
    setCapabilities(null);
    setChatSupported(nextKind === "llm");
    if (nextKind === "decision") setChatVisible(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setConflict(false);
    if (!catalogId.trim()) return setError("请选择模型或填写 provider/model 标识。");
    if (!apiModel.trim()) return setError("请填写模型名称。");
    if (!alias.trim()) return setError("请填写别称。");
    if (kind === "decision" && !baseUrl.trim())
      return setError("决策模型必须填写 Base URL，调用时追加 /systemone。");
    if (kind === "decision" && !editing && !apiKey) return setError("决策模型必须填写 API Key。");
    let connectionParams: JsonObject | undefined;
    if (paramsText.trim()) {
      try {
        connectionParams = parseJsonObject(paramsText, "供应商连接参数");
      } catch (cause) {
        return setError(errorMessage(cause));
      }
    }
    setBusy(true);
    try {
      if (editing && provider) {
        const body: dto.ProviderPatchInput = {
          operation_id: crypto.randomUUID(),
          expected_version: provider.version,
          catalog_id: catalogId.trim(),
          api_model: apiModel.trim(),
          alias: alias.trim(),
          base_url: baseUrl.trim(),
          enabled,
          chat_visible: chatVisible,
          ...(apiKey ? { api_key: apiKey } : {}),
          ...(paramsText.trim() ? { connection_params: connectionParams } : {}),
        };
        await api.request(`platform/models/${provider.id}`, "PATCH", dto.providerPatch.parse(body));
        api.setDirty(false);
        onSaved("模型配置已保存。");
      } else {
        const body: dto.ProviderCreateInput = {
          operation_id: crypto.randomUUID(),
          catalog_id: catalogId.trim(),
          api_model: apiModel.trim(),
          alias: alias.trim(),
          base_url: baseUrl.trim(),
          api_key: apiKey,
          enabled,
          chat_visible: chatVisible,
          ...(paramsText.trim() ? { connection_params: connectionParams } : {}),
        };
        await api.request("platform/models", "POST", dto.providerCreate.parse(body));
        api.setDirty(false);
        onSaved("模型配置已保存。");
      }
    } catch (cause) {
      const message = errorMessage(cause);
      setError(message);
      setConflict(message.includes("重新加载") || message.includes("已被更新"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      wide
      title={editing ? "编辑 Provider" : "添加 Provider"}
      description="保存配置不会自动发起付费调用；密钥与连接参数只写不回显。"
      onClose={() => {
        if (api.dirty && !window.confirm("放弃未保存的修改？")) return;
        api.setDirty(false);
        onClose();
      }}
    >
      <form data-editor onSubmit={submit}>
        <div className="model-editor">
          <div className="catalog-picker">
            <label>
              模型清单
              <span className="catalog-input">
                <input
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setCatalogOpen(true);
                  }}
                  onFocus={() => setCatalogOpen(true)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setCatalogOpen(false);
                  }}
                  placeholder="按供应商或名称搜索 SDK 生成模型"
                  aria-label="搜索模型清单"
                />
                <button
                  type="button"
                  className="secondary compact"
                  onClick={() => setCatalogOpen((v) => !v)}
                >
                  {catalogOpen ? "收起" : "选择"}
                </button>
              </span>
            </label>
            {catalogOpen && (
              <div className="catalog-list" role="listbox" aria-label="模型清单">
                {catalogLoading && <p className="muted">正在加载模型清单…</p>}
                {catalogError && (
                  <p role="alert" className="error">
                    模型清单加载失败，请重试。{" "}
                    <button
                      type="button"
                      className="secondary compact"
                      onClick={() => void loadCatalog(search.trim())}
                    >
                      重新加载
                    </button>
                  </p>
                )}
                {catalog?.items.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    role="option"
                    aria-selected={catalogId === item.id}
                    className={catalogId === item.id ? "selected" : undefined}
                    onClick={() => choose(item)}
                  >
                    <span>
                      <strong>{item.api_model}</strong>
                      <small className="mono">{item.id}</small>
                    </span>
                    <span className="catalog-meta">
                      {item.vendor} · {KIND_LABEL[item.kind]}
                    </span>
                  </button>
                ))}
                {!catalogLoading && !catalogError && catalog?.items.length === 0 && (
                  <p className="muted">没有匹配的目录项；可在下方直接填写 provider/model。</p>
                )}
                {catalog && catalog.total > catalog.items.length && (
                  <p className="muted">
                    仅显示前 {catalog.items.length} 项，共 {catalog.total}{" "}
                    项。请继续输入以缩小范围。
                  </p>
                )}
              </div>
            )}
            <label>
              模型标识
              <input
                value={catalogId}
                onChange={(event) => onCatalogIdChange(event.target.value)}
                maxLength={200}
                placeholder="provider/model；未收录型号可直接输入"
                required
              />
              <small>
                生成模型填写 provider/model；决策模型填写 typesafe/model、aliyun/model 或
                systemone/model。
              </small>
            </label>
            <label>
              模型名称
              <input
                value={apiModel}
                onChange={(event) => setApiModel(event.target.value)}
                maxLength={200}
                placeholder="实际请求使用的模型名称"
                required
              />
            </label>
            {selected && (
              <div className="catalog-preview">
                <span className="status">{KIND_LABEL[selected.kind]}</span>
                <span className="muted">
                  {selected.source === "systemone" ? "System One 预设" : "SDK 目录"}
                </span>
                <CapabilityGrid capabilities={selected.capabilities} compact />
              </div>
            )}
          </div>
          <div className="model-fields">
            <label>
              别称
              <input
                value={alias}
                onChange={(event) => setAlias(event.target.value)}
                maxLength={80}
                placeholder="用于列表与 Chat 主模型选择"
                required
              />
            </label>
            <label>
              Base URL
              <input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                maxLength={2048}
                placeholder={
                  kind === "decision"
                    ? "填写服务的 API Base URL，调用时追加 /systemone"
                    : "留空使用 SDK 默认端点"
                }
                required={kind === "decision"}
              />
              <small>只接受允许范围内的 HTTPS 端点；禁止把凭据写入 URL。</small>
            </label>
            <label>
              API Key
              <input
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                maxLength={4096}
                autoComplete="new-password"
                placeholder={
                  editing && provider?.has_api_key
                    ? "已配置。留空保持原密钥；替换时输入新密钥。"
                    : kind === "decision"
                      ? "决策服务必须填写 API Key"
                      : "可留空使用供应商原生认证"
                }
              />
            </label>
            <label>
              供应商连接参数（JSON）
              <textarea
                value={paramsText}
                onChange={(event) => setParamsText(event.target.value)}
                rows={4}
                spellCheck={false}
                placeholder={
                  editing && provider?.has_connection_params
                    ? "已配置。留空保持；填写 {} 清空。例如 Azure api_version 或 Bedrock aws_region_name。"
                    : `可留空。例如 {"api_version": "2024-05-01"}`
                }
              />
              <small>
                连接参数加密保存，只写不回显；不允许覆盖模型、消息、执行策略或重试配置。
              </small>
            </label>
            <div className="switch-row">
              <label className="inline-switch">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                />
                启用接入
              </label>
              <label className="inline-switch">
                <input
                  type="checkbox"
                  checked={chatVisible}
                  disabled={kind === "decision" || !chatSupported}
                  onChange={(event) => setChatVisible(event.target.checked)}
                />
                在 Chat 中展示
              </label>
            </div>
            {kind === "decision" && (
              <p className="muted">决策模型保持 Chat 隐藏，本期仅支持管理端测试。</p>
            )}
            {!chatSupported && kind === "llm" && (
              <p className="muted">当前模型协议不满足 Chat 准入条件，暂不能开放展示。</p>
            )}
            {capabilities && <CapabilityGrid capabilities={capabilities} />}
          </div>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}{" "}
            {conflict && (
              <button type="button" className="secondary compact" onClick={onReload}>
                重新加载
              </button>
            )}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>
            取消
          </button>
          <button type="submit" disabled={busy}>
            {busy ? "正在保存…" : "保存配置"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type ToolDraft = { id: string; name: string; arguments: string };

function ModelTestPanel({ provider, onClose }: { provider: ProviderView; onClose: () => void }) {
  const api = useDocumentSession();
  const decision = provider.kind === "decision";
  const [systemPrompt, setSystemPrompt] = useState("你是一名电商经营分析助手。");
  const [prompt, setPrompt] = useState("请用一句话说明本月经营表现。");
  const [stream, setStream] = useState(true);
  const [thinking, setThinking] = useState(false);
  const [effort, setEffort] = useState<"low" | "medium" | "high" | "max">("high");
  const [toolsText, setToolsText] = useState("");
  const [outputFormat, setOutputFormat] = useState<"text" | "json" | "schema">("text");
  const [schemaText, setSchemaText] = useState("");
  const [maxTokens, setMaxTokens] = useState(8192);
  const [stateText, setStateText] = useState('{"sales": 120, "orders": 42}');
  const [questionsText, setQuestionsText] = useState(
    JSON.stringify({ pick: { type: "noul", criteria: { true: "是", false: "否" } } }, null, 2),
  );
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");
  const [toolDrafts, setToolDrafts] = useState<ToolDraft[]>([]);
  const [usage, setUsage] = useState<CallResponse["usage"] | null>(null);
  const [validation, setValidation] = useState<CallResponse["validation"] | null>(null);
  const [responseModel, setResponseModel] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [answers, setAnswers] = useState<CallResponse["answers"]>(null);
  const [continuation, setContinuation] = useState<string | null>(null);
  const [toolResults, setToolResults] = useState<Record<string, string>>({});
  const [lastMessages, setLastMessages] = useState<dto.GenerateInput["messages"] | null>(null);
  const stopRef = useRef<{ abort(): void } | null>(null);

  const capabilityBlocked = useMemo(() => {
    if (!stream) return "";
    if (decision) return "决策调用固定为非流式。";
    return provider.capabilities.stream.state === "supported"
      ? ""
      : "当前模型不支持流式输出，请关闭流式再测试。";
  }, [decision, provider.capabilities.stream.state, stream]);

  function buildGenerate(): dto.GenerateInput {
    const messages: dto.GenerateInput["messages"] = [];
    if (systemPrompt.trim()) messages.push({ role: "system", content: systemPrompt });
    messages.push({ role: "user", content: prompt });
    let tools: dto.GenerateInput["tools"] = [];
    if (toolsText.trim()) {
      const parsed = JSON.parse(toolsText);
      if (!Array.isArray(parsed)) throw new Error("工具声明必须是 JSON 数组。");
      tools = parsed;
    }
    let outputSchema: Record<string, unknown> | null | undefined;
    if (outputFormat === "schema") {
      outputSchema = parseJsonObject(schemaText, "输出 Schema");
    }
    return dto.generateInput.parse({
      messages,
      stream,
      thinking,
      reasoning_effort: thinking ? effort : null,
      tools,
      output_format: outputFormat,
      output_schema: outputSchema ?? null,
      max_tokens: maxTokens,
    });
  }

  function buildDecision(): dto.DecisionInput {
    return dto.decisionInput.parse({
      state: JSON.parse(stateText),
      questions: JSON.parse(questionsText),
    });
  }

  function resetResult() {
    setText("");
    setToolDrafts([]);
    setAnswers(null);
    setUsage(null);
    setValidation(null);
    setResponseModel(null);
    setDuration(null);
    setError("");
    setStatus("running");
    setContinuation(null);
    setToolResults({});
    setNotice("");
  }

  function applyFinal(event: Extract<ModelEvent, { type: "final" }>) {
    setStatus(event.status);
    setUsage(event.usage);
    setValidation(event.validation);
    setResponseModel(event.response_model ?? null);
    setDuration(event.duration_ms);
    setContinuation(event.continuation_token ?? null);
    if (event.error_code) setError(apiError(event.error_code));
    if (event.status === "incomplete") setNotice("输出未完成。你可以检查当前结果后重新发送。");
    if (event.status === "canceled") setNotice("已停止本次调用，已产生的用量仍可能计费。");
  }

  async function run(input: dto.ModelInput, messages: dto.GenerateInput["messages"] | null) {
    if (running) return;
    resetResult();
    setRunning(true);
    const callId = crypto.randomUUID();
    const body = { call_id: callId, input };
    try {
      if (input.kind === "generate" && input.stream) {
        const drafts = new Map<number, ToolDraft>();
        const handle = api.modelRequest(
          `platform/models/${provider.id}/tests`,
          body,
          callId,
          (event) => {
            if (event.type === "text_delta") setText((value) => value + event.text);
            if (event.type === "tool_delta") {
              const draft = drafts.get(event.index) ?? {
                id: event.id ?? "",
                name: event.name ?? "",
                arguments: "",
              };
              draft.id = event.id ?? draft.id;
              draft.name = event.name ?? draft.name;
              draft.arguments += event.arguments;
              drafts.set(event.index, draft);
              setToolDrafts([...drafts.entries()].sort(([a], [b]) => a - b).map(([, v]) => v));
            }
            if (event.type === "reasoning_meta")
              setNotice("已启用 Thinking；仅展示公开的执行信息。");
            if (event.type === "final") applyFinal(event);
          },
        );
        stopRef.current = handle;
        await handle.done;
        setLastMessages(messages);
      } else {
        const response = await api.request<CallResponse>(
          `platform/models/${provider.id}/tests`,
          "POST",
          body,
          190_000,
        );
        setText(response.text);
        setToolDrafts(
          (response.tool_calls ?? []).map((call) => ({
            id: call.id,
            name: call.function.name,
            arguments: call.function.arguments,
          })),
        );
        setAnswers(response.answers);
        setUsage(response.usage);
        setValidation(response.validation);
        setResponseModel(response.response_model ?? null);
        setDuration(response.duration_ms);
        setStatus(response.status);
        setContinuation(response.continuation_token ?? null);
        if (response.error_code) setError(apiError(response.error_code));
        setLastMessages(messages);
      }
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        setStatus("canceled");
        setNotice("已停止本次调用，已产生的用量仍可能计费。");
      } else {
        setStatus("failed");
        setError(errorMessage(cause));
      }
    } finally {
      setRunning(false);
      stopRef.current = null;
    }
  }

  function start() {
    if (running) return;
    if (capabilityBlocked) return setError(capabilityBlocked);
    try {
      if (decision) void run(buildDecision(), null);
      else void run(buildGenerate(), null);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  function continueWithTools() {
    if (!lastMessages || !continuation) return;
    const complete = toolDrafts.every((draft) => toolResults[draft.id]?.trim());
    if (!complete) return setError("请为每个工具调用填写结果。");
    const messages: dto.GenerateInput["messages"] = [
      ...lastMessages,
      {
        role: "assistant",
        content: text || null,
        tool_calls: toolDrafts.map((draft) => ({
          id: draft.id,
          type: "function" as const,
          function: { name: draft.name, arguments: draft.arguments },
        })),
      },
      ...toolDrafts.map((draft) => ({
        role: "tool" as const,
        tool_call_id: draft.id,
        content: toolResults[draft.id],
      })),
    ];
    const input = dto.generateInput.parse({
      messages,
      stream,
      thinking,
      reasoning_effort: thinking ? effort : null,
      tools: toolsText.trim() ? JSON.parse(toolsText) : [],
      output_format: outputFormat,
      output_schema: outputFormat === "schema" ? parseJsonObject(schemaText, "输出 Schema") : null,
      max_tokens: maxTokens,
      continuation_token: continuation,
    });
    void run(input, messages);
  }

  const canContinue = Boolean(continuation && toolDrafts.length);
  return (
    <Modal
      open
      wide
      title={`模型能力测试 · ${provider.alias}`}
      description="测试使用已保存的配置。进入页面或修改参数不会发起调用，点击“开始测试”才出站一次。"
      onClose={() => {
        if (running && !window.confirm("正在测试，关闭将停止本次调用。确认关闭？")) return;
        stopRef.current?.abort();
        onClose();
      }}
    >
      <div className="test-layout">
        <div className="test-panel">
          {decision ? (
            <>
              <label>
                决策上下文（JSON）
                <textarea
                  rows={5}
                  value={stateText}
                  onChange={(event) => setStateText(event.target.value)}
                  spellCheck={false}
                />
              </label>
              <label>
                问题与约束（JSON）
                <textarea
                  rows={9}
                  value={questionsText}
                  onChange={(event) => setQuestionsText(event.target.value)}
                  spellCheck={false}
                />
                <small>支持 choice、noul、score；问题数上限 16。</small>
              </label>
            </>
          ) : (
            <>
              <label>
                系统指令
                <textarea
                  rows={2}
                  value={systemPrompt}
                  onChange={(event) => setSystemPrompt(event.target.value)}
                />
              </label>
              <label>
                测试消息
                <textarea
                  rows={4}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                />
              </label>
              <div className="form-grid">
                <label className="inline-switch">
                  <input
                    type="checkbox"
                    checked={stream}
                    onChange={(event) => setStream(event.target.checked)}
                  />
                  流式输出
                </label>
                <label className="inline-switch">
                  <input
                    type="checkbox"
                    checked={thinking}
                    disabled={provider.capabilities.thinking.state === "unsupported"}
                    onChange={(event) => setThinking(event.target.checked)}
                  />
                  Thinking
                </label>
                <label>
                  思考强度
                  <select
                    value={effort}
                    disabled={
                      !thinking || (provider.capabilities.reasoning_efforts ?? []).length === 0
                    }
                    onChange={(event) => setEffort(event.target.value as typeof effort)}
                  >
                    {["low", "medium", "high", "max"].map((value) => (
                      <option
                        key={value}
                        value={value}
                        disabled={!(provider.capabilities.reasoning_efforts ?? []).includes(value)}
                      >
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  输出方式
                  <select
                    value={outputFormat}
                    onChange={(event) => setOutputFormat(event.target.value as typeof outputFormat)}
                  >
                    <option value="text">文本</option>
                    <option value="json">JSON</option>
                    <option value="schema">JSON + Schema 校验</option>
                  </select>
                </label>
                <label>
                  最大输出 Token
                  <input
                    type="number"
                    min={1}
                    max={32768}
                    value={maxTokens}
                    onChange={(event) => setMaxTokens(Number(event.target.value))}
                  />
                </label>
              </div>
              {outputFormat === "schema" && (
                <label>
                  输出 Schema（JSON）
                  <textarea
                    rows={5}
                    value={schemaText}
                    onChange={(event) => setSchemaText(event.target.value)}
                    spellCheck={false}
                    placeholder='{"type":"object","properties":{...}}'
                  />
                </label>
              )}
              <label>
                工具声明（JSON 数组，可留空）
                <textarea
                  rows={4}
                  value={toolsText}
                  onChange={(event) => setToolsText(event.target.value)}
                  spellCheck={false}
                  placeholder='[{"type":"function","function":{"name":"lookup","description":"查询指标","parameters":{"type":"object","properties":{"metric":{"type":"string"}}}}}]'
                />
                <small>测试不会执行真实工具；返回调用后可为每个调用手工填写结果并继续。</small>
              </label>
              {capabilityBlocked && <p className="notice">{capabilityBlocked}</p>}
            </>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {notice && <p className="notice">{notice}</p>}
          <div className="dialog-actions">
            <button type="button" className="secondary" onClick={onClose}>
              关闭
            </button>
            {running ? (
              <button type="button" className="secondary" onClick={() => stopRef.current?.abort()}>
                <Square size={14} /> 停止调用
              </button>
            ) : (
              <button type="button" onClick={start}>
                <Play size={14} /> 开始测试
              </button>
            )}
          </div>
        </div>
        <div className="test-result" aria-live="polite">
          <header>
            <span className={`status ${status === "failed" ? "status-muted" : ""}`}>
              {{
                running: "调用中",
                completed: "成功",
                failed: "失败",
                canceled: "已停止",
                incomplete: "未完成",
              }[status] ?? "未开始"}
            </span>
            {responseModel && <span className="muted mono">{responseModel}</span>}
            {duration !== null && <span className="muted">{duration} ms</span>}
          </header>
          {usage && (
            <p className="muted">
              用量：输入 {usage.input_tokens ?? "未知"} · 输出 {usage.output_tokens ?? "未知"} ·
              Reasoning {usage.reasoning_tokens ?? "未知"}
            </p>
          )}
          {validation && (
            <p className="muted">
              校验：
              {
                { valid: "通过", invalid: "未通过", not_requested: "未请求" }[validation.status]
              } · {validation.mode}
              {(validation.paths ?? []).length ? ` · ${(validation.paths ?? []).join(", ")}` : ""}
            </p>
          )}
          {answers && (
            <div className="decision-answers">
              {Object.entries(answers).map(([key, answer]) => (
                <article key={key}>
                  <h3>{key}</h3>
                  <dl className="details">
                    <dt>类型</dt>
                    <dd>{answer.type}</dd>
                    {answer.choice && (
                      <>
                        <dt>选择</dt>
                        <dd>{answer.choice}</dd>
                      </>
                    )}
                    {answer.noul !== null && answer.noul !== undefined && (
                      <>
                        <dt>Noul</dt>
                        <dd>{answer.noul}</dd>
                      </>
                    )}
                    {answer.score !== null && answer.score !== undefined && (
                      <>
                        <dt>评分</dt>
                        <dd>{answer.score}</dd>
                      </>
                    )}
                    {answer.confidence !== null && answer.confidence !== undefined && (
                      <>
                        <dt>置信度</dt>
                        <dd>{answer.confidence}</dd>
                      </>
                    )}
                    {answer.probabilities && (
                      <>
                        <dt>概率</dt>
                        <dd className="mono">{JSON.stringify(answer.probabilities)}</dd>
                      </>
                    )}
                  </dl>
                </article>
              ))}
            </div>
          )}
          {text && <pre className="result-text">{text}</pre>}
          {toolDrafts.length > 0 && (
            <div className="tool-results">
              <h3>工具调用（未执行）</h3>
              {toolDrafts.map((draft) => (
                <article key={draft.id || draft.name}>
                  <div className="mono">
                    {draft.name} · {draft.id}
                  </div>
                  <pre className="result-text">{draft.arguments}</pre>
                  {canContinue && (
                    <label>
                      工具结果（手工回填）
                      <textarea
                        rows={2}
                        value={toolResults[draft.id] ?? ""}
                        onChange={(event) =>
                          setToolResults((current) => ({
                            ...current,
                            [draft.id]: event.target.value,
                          }))
                        }
                      />
                    </label>
                  )}
                </article>
              ))}
              {canContinue && (
                <button
                  type="button"
                  className="secondary"
                  onClick={continueWithTools}
                  disabled={running}
                >
                  带工具结果继续调用
                </button>
              )}
            </div>
          )}
          {!text && !answers && !toolDrafts.length && !running && (
            <p className="muted">结果会显示在这里，包括正文、工具调用、结构校验与用量。</p>
          )}
        </div>
      </div>
    </Modal>
  );
}

export type { AvailableModel };
