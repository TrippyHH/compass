import { z } from "zod";
import type { components } from "../generated/api";

export type ProviderView = components["schemas"]["ProviderView"];
export type ProviderPage = components["schemas"]["ProviderPage"];
export type CatalogPage = components["schemas"]["CatalogPage"];
export type SavedProvider = components["schemas"]["SavedProvider"];
export type AvailableModels = components["schemas"]["AvailableModels"];
export type CallResponse = components["schemas"]["CallResponse"];
export type CallRecordView = components["schemas"]["CallRecordView"];
export type ModelChangePage = components["schemas"]["ModelChangePage"];
export type ProviderCapabilities = ProviderView["capabilities"];
export type CatalogItem = CatalogPage["items"][number];
export type AvailableModel = AvailableModels["items"][number];
export type TestSummary = NonNullable<ProviderView["latest_test"]>;

export type CallOutcome = Omit<
  CallResponse,
  "kind" | "text" | "tool_calls" | "answers" | "continuation_token"
> & { continuation_token?: string | null };

export type ModelEvent =
  | { type: "start"; call_id: string; provider_id: string; config_version: number }
  | { type: "text_delta"; call_id: string; text: string }
  | {
      type: "tool_delta";
      call_id: string;
      index: number;
      id?: string | null;
      name?: string | null;
      arguments: string;
    }
  | {
      type: "reasoning_meta";
      call_id: string;
      thinking: boolean;
      reasoning_effort?: string | null;
    }
  | ({ type: "final" } & CallOutcome);

export type AgentTrustedContext = {
  actor_user_id: string;
  purpose: "manage" | "test" | "chat" | "module_read";
  scope: { kind: "platform" } | { kind: "tenant"; tenant_id: string; workspace_id: string };
};

export const uuid = z.uuid();
const json = z.json();
const jsonObject = z.record(z.string(), json);

export const modelQuery = z.strictObject({
  q: z.string().trim().max(200).default(""),
  page: z.coerce.number().int().min(1).max(10_000).default(1),
  page_size: z.coerce
    .number()
    .pipe(z.union([z.literal(20), z.literal(50), z.literal(100)]))
    .default(20),
});

export const catalogQuery = modelQuery.extend({
  vendor: z.string().trim().max(100).optional(),
});

const toolFunction = z.strictObject({
  name: z
    .string()
    .regex(/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/, "工具名需以字母或下划线开头，最长 64 位。"),
  description: z.string().max(2000).default(""),
  parameters: jsonObject.default({ type: "object", properties: {} }),
});

export const toolDefinition = z.strictObject({
  type: z.literal("function").default("function"),
  function: toolFunction,
});

const toolCall = z.strictObject({
  id: z.string().min(1).max(200),
  type: z.literal("function").default("function"),
  function: z.strictObject({ name: z.string(), arguments: z.string() }),
});

const chatMessage = z.strictObject({
  role: z.enum(["system", "user", "assistant", "tool"]),
  content: z.string().max(65_536).nullable().optional(),
  tool_calls: z.array(toolCall).max(16).nullable().optional(),
  tool_call_id: z.string().max(200).nullable().optional(),
});

export const generateInput = z.strictObject({
  kind: z.literal("generate").default("generate"),
  messages: z.array(chatMessage).min(1).max(64),
  stream: z.boolean().default(false),
  thinking: z.boolean().default(false),
  reasoning_effort: z.enum(["low", "medium", "high", "max"]).nullable().optional(),
  tools: z.array(toolDefinition).max(16).default([]),
  output_format: z.enum(["text", "json", "schema"]).default("text"),
  output_schema: jsonObject.nullable().optional(),
  max_tokens: z.number().int().min(1).max(32_768).default(8192),
  continuation_token: z.string().max(1_048_576).nullable().optional(),
});

const decisionValue = z.union([z.string(), jsonObject, z.array(json)]);
const decisionQuestion = z.strictObject({
  type: z.enum(["choice", "noul", "score"]),
  instructions: z.union([z.string(), jsonObject, z.array(json)]).default(""),
  criteria: z
    .union([jsonObject, z.array(json)])
    .nullable()
    .optional(),
});

export const decisionInput = z.strictObject({
  kind: z.literal("decision").default("decision"),
  state: decisionValue,
  questions: z.record(z.string().max(200), decisionQuestion),
  stream: z.literal(false).default(false),
});

export const modelInput = z.discriminatedUnion("kind", [generateInput, decisionInput]);

export const testCallInput = z.strictObject({
  call_id: uuid,
  input: modelInput,
});

export const chatCallInput = z.strictObject({
  call_id: uuid,
  provider_id: uuid,
  input: generateInput,
});

export const providerCreate = z.strictObject({
  operation_id: uuid,
  catalog_id: z.string().min(1).max(200),
  api_model: z.string().min(1).max(200),
  alias: z.string().min(1).max(80),
  base_url: z.string().max(2048).default(""),
  api_key: z.string().max(4096).default(""),
  connection_params: jsonObject.nullable().optional(),
  enabled: z.boolean().default(false),
  chat_visible: z.boolean().default(false),
});

export const providerPatch = z
  .strictObject({
    operation_id: uuid,
    expected_version: z.number().int().min(1),
    catalog_id: z.string().min(1).max(200).optional(),
    api_model: z.string().min(1).max(200).optional(),
    alias: z.string().min(1).max(80).optional(),
    base_url: z.string().max(2048).optional(),
    api_key: z.string().min(1).max(4096).optional(),
    connection_params: jsonObject.optional(),
    enabled: z.boolean().optional(),
    chat_visible: z.boolean().optional(),
  })
  .refine(
    (value) =>
      [
        value.catalog_id,
        value.api_model,
        value.alias,
        value.base_url,
        value.api_key,
        value.connection_params,
        value.enabled,
        value.chat_visible,
      ].some((entry) => entry !== undefined),
    "至少修改一个字段。",
  );

export type ModelQuery = z.infer<typeof modelQuery>;
export type CatalogQuery = z.infer<typeof catalogQuery>;
export type ProviderCreateInput = z.infer<typeof providerCreate>;
export type ProviderPatchInput = z.infer<typeof providerPatch>;
export type TestCallInput = z.infer<typeof testCallInput>;
export type ChatCallInput = z.infer<typeof chatCallInput>;
export type GenerateInput = z.infer<typeof generateInput>;
export type DecisionInput = z.infer<typeof decisionInput>;
export type ModelInput = z.infer<typeof modelInput>;
