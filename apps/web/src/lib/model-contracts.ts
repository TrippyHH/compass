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
export const parseCallRecord = validator<CallRecordView>(callRecordSchema);
export const parseModelChangePage = validator<ModelChangePage>(changePageSchema);
export const parseModelEvent = validator<ModelEvent>(eventSchema);
