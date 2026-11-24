"use client";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
import { ArrowUp, Paperclip, Plus, RotateCcw, Square, Trash2 } from "lucide-react";
import { useDocumentSession } from "./shell/session-boundary";
import type { PagePayload } from "@/contracts/page";
import type { AvailableModel, AvailableModels, ModelEvent } from "@/contracts/models";

const ERROR_LABEL: Record<string, string> = {
  model_authentication_failed: "模型认证失败，请检查 API Key 与接入权限。",
  model_rate_limited: "调用过于频繁，请稍后重试。",
  model_timeout: "本次调用已超时，请检查服务状态或稍后重试。",
  model_unavailable_upstream: "供应商暂时不可用，请稍后重试。",
  stream_interrupted: "输出未完成。你可以检查当前结果后重新发送。",
  output_truncated: "输出被截断，结果未完整返回。",
  model_refused: "模型拒绝了本次请求。",
  empty_output: "模型没有返回内容。",
  invalid_json: "输出不是有效 JSON。",
  schema_validation_failed: "输出不符合指定结构。",
  capability_not_supported: "当前模型不支持所选能力或参数组合。",
  provider_protocol_error: "供应商返回了无法识别的响应。",
  validation_failed: "请检查输入内容。",
  internal_error: "调用未能完成，请稍后重试。",
  record_write_failed: "调用结果可能已产生，请按调用 ID 核对。",
};

type Turn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  alias?: string;
  status?: "running" | "completed" | "failed" | "canceled" | "incomplete";
  error?: string;
  usage?: { input_tokens?: number | null; output_tokens?: number | null } | null;
};

const suggestions = ["上周经营表现", "解释指标变化", "预测未来销量", "分析一份文件"];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function AnalysisComposer({ payload }: { payload: PagePayload }) {
  const api = useDocumentSession();
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [models, setModels] = useState<AvailableModels | null>(null);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [modelId, setModelId] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [contextStart, setContextStart] = useState(0);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const streamHandle = useRef<{ abort(): void } | null>(null);
  const composing = useRef(false);
  const canInvoke = payload.session.capabilities.includes("models.invoke");
  const scope = payload.session.scope;

  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    setModelsError("");
    try {
      const params = new URLSearchParams();
      if (scope.tenantId) params.set("tenantId", scope.tenantId);
      if (scope.workspaceId) params.set("workspaceId", scope.workspaceId);
      const value = await api.request<AvailableModels>(`models/available?${params}`);
      setModels(value);
    } catch (cause) {
      setModelsError(cause instanceof Error ? cause.message : "模型清单加载失败，请重试。");
    } finally {
      setModelsLoading(false);
    }
  }, [api, scope.tenantId, scope.workspaceId]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadModels(), 0);
    return () => window.clearTimeout(timer);
  }, [loadModels]);
  useEffect(
    () => () => {
      streamHandle.current?.abort();
    },
    [],
  );

  const selected: AvailableModel | null = useMemo(
    () => models?.items.find((item) => item.id === modelId) ?? null,
    [modelId, models],
  );
  const running = sending;

  function selectModel(value: string) {
    if (value === modelId) return;
    if (
      turns.length > contextStart &&
      !window.confirm("更换主模型将开始新对话，当前结果保留在本页。")
    )
      return;
    setError("");
    setModelId(value);
    if (turns.length > contextStart) {
      setContextStart(turns.length);
      setNotice("已更换主模型，新的消息将使用所选模型重新开始上下文。");
    } else {
      setNotice("");
    }
  }

  function newConversation() {
    if (running) return;
    if (turns.length && !window.confirm("开始新对话？当前对话仍会保留在本页，刷新后不会保存。"))
      return;
    setTurns([]);
    setContextStart(0);
    setQuestion("");
    setError("");
    setNotice("");
    setFiles([]);
    setAnnouncement("已开始新对话。");
    input.current?.focus();
  }

  function addFiles(event: ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!chosen.length) return;
    setError("");
    setFiles((current) => {
      const next = [...current, ...chosen];
      if (next.length > 10) {
        setError("最多添加 10 个附件。");
        return current;
      }
      return next;
    });
  }

  async function send() {
    if (running || !selected) return;
    const content = question.trim();
    if (!content) return;
    if (files.length) {
      setError("附件暂不支持发送给模型，请移除附件后发送文字。");
      return;
    }
    setError("");
    setNotice("");
    const history = turns
      .slice(contextStart)
      .filter((turn) => turn.text.trim())
      .map((turn) => ({ role: turn.role, content: turn.text }));
    const messages = [...history, { role: "user" as const, content }];
    const assistantId = crypto.randomUUID();
    const callId = crypto.randomUUID();
    const supportsThinking = selected.capabilities.thinking.state === "supported";
    setTurns((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", text: content },
      { id: assistantId, role: "assistant", text: "", alias: selected.alias, status: "running" },
    ]);
    setQuestion("");
    setSending(true);
    setAnnouncement(`正在调用 ${selected.alias}…`);
    const update = (patch: Partial<Turn>) =>
      setTurns((current) =>
        current.map((turn) => (turn.id === assistantId ? { ...turn, ...patch } : turn)),
      );
    const params = new URLSearchParams();
    if (scope.tenantId) params.set("tenantId", scope.tenantId);
    if (scope.workspaceId) params.set("workspaceId", scope.workspaceId);
    const body = {
      call_id: callId,
      provider_id: selected.id,
      input: {
        messages,
        stream: true,
        thinking: supportsThinking,
        reasoning_effort: supportsThinking ? "high" : null,
        max_tokens: 8192,
      },
    };
    let received = "";
    try {
      const handle = api.modelRequest(
        `models/calls?${params}`,
        body,
        callId,
        (event: ModelEvent) => {
          if (event.type === "text_delta") {
            received += event.text;
            update({ text: received });
          }
          if (event.type === "start") setAnnouncement(`正在调用 ${selected.alias}…`);
          if (event.type === "reasoning_meta")
            setAnnouncement("模型已启用 Thinking，仅展示公开执行信息。");
          if (event.type === "final") {
            update({
              status: event.status,
              error: event.error_code
                ? (ERROR_LABEL[event.error_code] ?? "调用未能完成。")
                : undefined,
              usage: event.usage,
            });
            if (event.status === "incomplete")
              setNotice("输出未完成。你可以检查当前结果后重新发送。");
            if (event.status === "failed" && event.error_code)
              setError(ERROR_LABEL[event.error_code] ?? "调用未能完成，请稍后重试。");
            setAnnouncement(event.status === "completed" ? "本轮回答已完成。" : "本轮调用已结束。");
          }
        },
      );
      streamHandle.current = handle;
      await handle.done;
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        update({ status: "canceled" });
        setNotice("已停止本次调用，已产生的用量仍可能计费。");
        setAnnouncement("已停止本次调用。");
      } else {
        const message = cause instanceof Error ? cause.message : "调用未能完成，请稍后重试。";
        update({ status: "failed", error: message });
        setError(message);
      }
    } finally {
      setSending(false);
      streamHandle.current = null;
    }
  }

  function stop() {
    streamHandle.current?.abort();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (composing.current || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!sending) void send();
  }

  const sendDisabled = sending || !selected || !canInvoke || !question.trim() || files.length > 0;
  const disabledReason = !canInvoke
    ? "你暂无使用模型的权限，请联系租户管理员。"
    : modelsLoading
      ? "正在加载可选模型…"
      : !models?.items.length
        ? "暂无可用模型，请联系管理员。"
        : !selected
          ? "请选择主模型"
          : files.length
            ? "附件暂不支持发送给模型，请移除后发送文字。"
            : "";

  const composer = (
    <div className="analysis-composer" data-editor>
      {files.length > 0 && (
        <div className="attachment-row" aria-label="待发送附件">
          {files.map((file, index) => (
            <span className="attachment-chip" key={`${file.name}-${index}`}>
              <Paperclip size={13} />
              <span title={file.name}>{file.name}</span>
              <small>
                {file.type || "未知类型"} · {formatSize(file.size)}
              </small>
              <button
                type="button"
                className="icon-button"
                aria-label={`移除附件 ${file.name}`}
                onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
              >
                <Trash2 size={13} />
              </button>
            </span>
          ))}
        </div>
      )}
      <textarea
        ref={input}
        aria-label="分析问题"
        placeholder="本月销售增长了，为什么渠道贡献没有跟上？"
        value={question}
        onChange={(event) => {
          setQuestion(event.target.value);
          api.setDirty(true);
        }}
        onKeyDown={onKeyDown}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        maxLength={8000}
        disabled={!canInvoke}
      />
      <div className="composer-toolbar">
        <label className="chat-model">
          <span className="visually-hidden">主模型</span>
          <select
            aria-label="主模型"
            value={modelId}
            onChange={(event) => selectModel(event.target.value)}
            disabled={running || modelsLoading}
          >
            <option value="">{modelsLoading ? "正在加载模型…" : "请选择主模型"}</option>
            {models?.items.map((model) => (
              <option key={model.id} value={model.id}>
                {model.alias} · {model.api_model} · {KIND_LABEL(model.kind)}
              </option>
            ))}
          </select>
        </label>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={addFiles}
          aria-label="添加附件"
        />
        <button
          type="button"
          className="icon-button"
          aria-label="添加文件"
          onClick={() => fileInput.current?.click()}
          disabled={running}
        >
          <Plus size={20} />
        </button>
        <span className="composer-scope">{selected ? KIND_LABEL(selected.kind) : "生成模型"}</span>
        <button
          type="button"
          className={running ? "composer-send stop" : "composer-send"}
          disabled={running ? false : sendDisabled}
          aria-label={running ? "停止调用" : "发送分析"}
          title={disabledReason || (running ? "停止调用" : "发送分析")}
          onClick={running ? stop : () => void send()}
        >
          {running ? <Square size={18} /> : <ArrowUp size={21} />}
        </button>
      </div>
      {!running && disabledReason && <p className="composer-hint">{disabledReason}</p>}
      {running && <p className="composer-hint">正在调用 {selected?.alias}… 可随时停止。</p>}
    </div>
  );

  return (
    <section className={turns.length ? "chat-main" : "analysis-welcome"} aria-label="分析对话">
      <div className="chat-head">
        <div>
          <h2 className="visually-hidden">分析对话</h2>
          {turns.length > 0 && (
            <span className="muted">本页对话不写入服务端，刷新后不会保留。</span>
          )}
        </div>
        <button className="secondary compact" onClick={newConversation} disabled={running}>
          <RotateCcw size={14} /> 新建对话
        </button>
      </div>
      {turns.length === 0 && (
        <div className="analysis-intro">
          <h2>
            每个好问题，<em>都有迹可循。</em>
          </h2>
          <p>选择主模型后发送问题，获得所选模型的真实回答。</p>
        </div>
      )}
      {turns.length > 0 && (
        <div className="chat-log" role="log" aria-live="polite">
          {turns.map((turn) =>
            turn.role === "user" ? (
              <article className="chat-message user" key={turn.id}>
                <div className="chat-bubble">{turn.text}</div>
              </article>
            ) : (
              <article className="chat-message assistant" key={turn.id}>
                <header>
                  <span className="model-badge">{turn.alias ?? "模型"}</span>
                  <span className="muted">
                    {
                      {
                        running: "正在生成",
                        completed: "已完成",
                        failed: "失败",
                        canceled: "已停止",
                        incomplete: "未完成",
                      }[turn.status ?? "running"]
                    }
                  </span>
                </header>
                <div className="chat-bubble">
                  {turn.text ||
                    (turn.status === "running" ? <span className="typing">…</span> : null)}
                </div>
                {turn.error && (
                  <p role="alert" className="error">
                    {turn.error}
                  </p>
                )}
                {turn.usage && (
                  <p className="muted chat-usage">
                    输入 {turn.usage.input_tokens ?? "未知"} · 输出{" "}
                    {turn.usage.output_tokens ?? "未知"}
                  </p>
                )}
              </article>
            ),
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p className="notice">{notice}</p>}
      {modelsError && (
        <p role="alert" className="error">
          模型清单加载失败，请重试。{" "}
          <button className="secondary compact" onClick={() => void loadModels()}>
            重新加载
          </button>
        </p>
      )}
      {composer}
      {turns.length === 0 && (
        <div className="analysis-suggestions">
          {suggestions.map((suggestion) => (
            <button
              className="secondary"
              key={suggestion}
              onClick={() => {
                setQuestion(suggestion);
                api.setDirty(true);
                input.current?.focus();
              }}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
    </section>
  );
}

function KIND_LABEL(kind: string): string {
  return kind === "decision" ? "决策模型" : "生成模型";
}
