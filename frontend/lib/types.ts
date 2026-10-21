export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";

export interface VersionMetadata {
  title: string | null;
  author: string | null;
  subject: string | null;
  keywords: string | null;
  language: string | null;
  encrypted: boolean;
  has_signatures: boolean;
  has_forms: boolean;
  has_annotations: boolean;
  has_attachments: boolean;
  has_javascript: boolean;
  page_sizes: [number, number, number][] | null;
  warnings: string[];
  signatures: { field: string; signed: boolean; sub_filter?: string; signing_time?: string; covers_whole_file?: boolean }[];
  needs_password: boolean;
  pdf_version?: string | null;
  outline_count?: number | null;
  revisions?: number | null;
}

export interface Version {
  id: string;
  document_id: string;
  version_number: number;
  is_original: boolean;
  operation: string;
  label: string;
  mime_type: string;
  ext: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  parent_version_id: string | null;
  produced_by_job_id: string | null;
  created_at: string | null;
  expired: boolean;
  validation_passed: boolean | null;
  metadata?: VersionMetadata;
}

export interface DocumentSummary {
  id: string;
  title: string;
  original_filename: string;
  kind: "pdf" | "epub" | "office" | "image";
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
  latest_version: Version | null;
  version_count: number | null;
}

export interface JobOutput {
  key: string;
  version_id: string;
  document_id: string;
  sha256: string;
  size_bytes: number;
  label: string;
}

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  document_id: string | null;
  batch_id: string | null;
  progress: { stage?: string; current?: number | null; total?: number | null };
  warnings: string[];
  error: { code: string | null; message: string | null } | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  attempts: number;
  outputs: JobOutput[];
  resumable: boolean;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  steps?: { name: string; status: string; started_at: string | null; finished_at: string | null }[];
  inputs?: { position: number; version_id: string | null; sha256: string }[];
}

export interface DocumentDetail extends DocumentSummary {
  versions: Version[];
  jobs: Job[];
}

export interface Region {
  id: string;
  role: string;
  order: number;
  bbox: [number, number, number, number];
  text: string;
  corrected_text: string | null;
  excluded: boolean;
  source: "ocr" | "text_layer";
  conf: number;
  heading_level: number | null;
  low_confidence: boolean;
  lines: { bbox: [number, number, number, number]; text: string; conf: number }[];
  caption_for?: string;
  figure?: { kind: string; asset: string; width: number; height: number; labels_text: string; fallback: boolean; method: string };
}

export interface OcrPageSummary {
  page_index: number;
  status: string;
  mode: string;
  kind: string | null;
  mean_confidence: number | null;
  needs_review: boolean;
  reviewed: boolean;
  low_confidence_regions: number;
  fallback_recommended: boolean;
  error: string | null;
}

export interface OcrSession {
  id: string;
  document_id: string;
  version_id: string;
  job_id: string | null;
  status: "processing" | "review" | "approved" | "failed";
  languages: string;
  settings: Record<string, unknown>;
  page_count: number;
  summary: Record<string, unknown>;
  book_metadata: { title?: string; author?: string; language?: string; chapters?: { title: string; page: number }[] };
  created_at: string | null;
  approved_at: string | null;
  pages?: OcrPageSummary[];
  job?: Job | null;
}

export interface OcrPage {
  page_index: number;
  status: string;
  classification: Record<string, unknown>;
  preprocessing: Record<string, unknown>;
  regions: Region[];
  mean_confidence: number | null;
  needs_review: boolean;
  reviewed: boolean;
  image_size: [number, number] | null;
  error: string | null;
  has_processed_image: boolean;
}

export interface AuditEvent {
  seq: number;
  ts: string;
  event_type: string;
  status: string;
  document_id: string | null;
  version_id: string | null;
  job_id: string | null;
  operation: string | null;
  input_sha256: string | null;
  output_sha256: string | null;
  delivery_outcome: string | null;
  details: Record<string, unknown>;
  event_hash?: string;
}

export interface ToolStatus {
  key: string;
  name: string;
  available: boolean;
  version: string | null;
  required_for: string[];
  install_hint: string;
  detail: string | null;
}
