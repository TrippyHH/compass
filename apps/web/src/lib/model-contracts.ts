import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import availableSchema from "../generated/model-available.schema.json";
import callRecordSchema from "../generated/model-call-record.schema.json";
import callResponseSchema from "../generated/model-call-response.schema.json";
import catalogPageSchema from "../generated/model-catalog-page.schema.json";
import changePageSchema from "../generated/model-change-page.schema.json";
import eventSchema from "../generated/model-event.schema.json";
import providerPageSchema from "../generated/model-provider-page.schema.json";
import providerViewSchema from "../generated/model-provider-view.schema.json";
import routedCallResponseSchema from "../generated/model-routed-call-response.schema.json";
import routedEventSchema from "../generated/model-routed-event.schema.json";
import savedProviderSchema from "../generated/model-saved-provider.schema.json";
import type {
  AvailableModels,
  CallRecordView,
  CallResponse,
  CatalogPage,
  ModelChangePage,
  ModelEvent,
  ProviderPage,
  ProviderView,
  RoutedCallResponse,
  RoutedModelEvent,
  SavedProvider,
} from "../contracts/models";

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);

function validator<T>(schema: object) {
  const validate = ajv.compile<T>(schema);
  return (value: unknown): T => {
    if (!validate(value)) throw new Error("Incompatible model contract");
    return value;
  };
}

export const parseProviderPage = validator<ProviderPage>(providerPageSchema);
export const parseProviderView = validator<ProviderView>(providerViewSchema);
export const parseProvider = validator<SavedProvider>(savedProviderSchema);
export const parseCatalogPage = validator<CatalogPage>(catalogPageSchema);
export const parseAvailableModels = validator<AvailableModels>(availableSchema);
export const parseCallResponse = validator<CallResponse>(callResponseSchema);
export const parseRoutedCallResponse = validator<RoutedCallResponse>(routedCallResponseSchema);
export const parseCallRecord = validator<CallRecordView>(callRecordSchema);
export const parseModelChangePage = validator<ModelChangePage>(changePageSchema);
export const parseModelEvent = validator<ModelEvent>(eventSchema);
export const parseRoutedModelEvent = validator<RoutedModelEvent>(routedEventSchema);

export type FixedSelection = { mode: "fixed" | "auto"; provider_id?: string | null };

/** The BFF never pre-binds auto, but a fixed request must get the model it asked for. */
export function assertFixedRoute(
  value: {
    status?: string;
    intent?: { scene_id: string } | null;
    routing?: { provider_id: string; reason: string } | null;
  },
  selection: FixedSelection,
): void {
  if (selection.mode !== "fixed" || !selection.provider_id) return;
  if (!value.routing) {
    if (value.status && value.status !== "completed") return;
    throw new Error("Routed response has no selected model");
  }
  if (value.intent?.scene_id === "SC-39") {
    if (value.routing.reason !== "off_topic_flash")
      throw new Error("Off-topic route must use the Flash range prompt");
    return;
  }
  if (value.routing.provider_id !== selection.provider_id)
    throw new Error("Fixed route provider does not match the user selection");
}
