// 화면과 서버가 같이 쓰는 타입. 분석 결과(result.json)의 모양은 파이썬 export_dashboard.py 가 정한다.
// result-shape.json 은 실제 결과에서 글자와 숫자를 모두 지우고 모양만 남긴 파일이다 (데모 리포트 내용을 저장소에 올리지 않기 위해).
type RawResult = typeof import("./result-shape.json");

/** 공식 데이터 대조(OpenDART, KRX …)의 출처. 파이썬 tribunal/external/ 이 만든다. */
export interface ExternalSource {
  provider: string;
  display: [string, string][];   // 오른쪽 칸에 보일 [이름, 값] 줄
  short: string;                 // 표 안에 보일 한 줄
  url: string;                   // 사람이 볼 원문 (DART 공시 원문, KRX 정보데이터시스템)
  api_url?: string;              // 실제로 부른 주소 (키는 들어 있지 않다)
  fetched_at: string; report_date: string; timing: string;
}
export type NumberCheck = Omit<RawResult["numbers"]["results"][number], "computed"> & {
  computed: number | string | null;   // 다시 계산한 값(숫자) 또는 공시 값(단위 붙은 글자)
  kind?: string; hue?: string; source?: ExternalSource; timing?: string;
};
export interface ExternalSummary {
  provider: string | null; enabled: boolean | null; error: string | null;
  count: Record<string, number>; skipped: { rid: string; item: string; why: string }[]; checked_at: string;
}
/** 주장의 판정을 올린 코드 검증 (공식 데이터 불일치 등). 파이썬 rules.code_findings */
export interface CodeFinding { check_id: string; name: string; verdict: string; reason: string; kind: string }

/** 근거 종합표. 파이썬 tribunal/evidence.py */
export interface EvidenceNumber {
  rid: string; item: string; value: string; page: number | null;
  nature: string;          // 사실 / 계산 / 추정 / 가정
  status: string;          // 공식 일치 / 계산 일치 / 불일치 / 검증 불가 / 근거 미확인
  checks: { check_id: string; kind: string; name: string; status: string; computed: number | string | null; reason: string;
            source: string; url: string; fetched_at: string; timing: string }[];
  report_source: string; own_source?: boolean;
}
export interface EvidenceClaim {
  claim_id: string; no: number; text: string; page: number | null; is_core: boolean;
  kinds: string[]; numbers: EvidenceNumber[];
  status: string;          // 실제 수치 불일치 / 근거 확인 / 일부 확인 / 근거 미확인 / 숫자 근거 없음
  confidence: string; confidence_why: string; logic_verdict: string | null; objections: number; split: number; todo: string[];
}
export interface EvidenceTable { claims: EvidenceClaim[]; count: Record<string, number>; note: string }

type RawClaim = RawResult["claims"][number];
type RawObjection = RawClaim["objections"][number];
/** 이의. page·quote: 리스크 검토자가 원문에서 짚은 문장 (quote_found: 코드가 원문에 있다고 확인함) */
export type Objection = RawObjection & {
  split?: boolean; claim_id?: string | null; page?: number | null; quote?: string; quote_found?: boolean;
};
export type Claim = Omit<RawClaim, "objections"> & { objections: Objection[]; code?: CodeFinding[] };

type RawMark = RawResult["marks"][number];
export type Result = Omit<RawResult, "numbers" | "claims" | "marks"> & {
  marks: (RawMark & { pass?: boolean })[];     // pass: 통과한 곳(초록). 화면에서 켤 때만 보인다
  numbers: Omit<RawResult["numbers"], "results"> & { results: NumberCheck[] };
  claims: Claim[];
  external?: Record<string, ExternalSummary | null> | null;
  evidence?: EvidenceTable | null;
  code_findings?: Record<string, CodeFinding[]>;
  other_objections?: Objection[];     // 원문에서 찾았고 추출된 주장과 묶이지 않은 이의
};
export type Mark = Result["marks"][number];
export type Figure = Result["figures"][number];
export type Table = Result["tables"][number];
export type ChecklistItem = Result["checklist"][number];

export type JobState = "ready" | "running" | "done" | "failed";

export interface Estimate {
  calls?: number;
  inputTokens?: number;
  outputTokens?: number;
  provider?: string;
  model?: string;
  pages?: number;
  error?: string;
}

export interface Stage {
  label: string;
  done: boolean;
}

/** 보관함의 리포트 한 건 */
export interface LibraryEntry {
  id: string;
  fileName: string;
  uploadedAt: string;
  size: number;
  demo: boolean;
  mock: boolean;
  estimate: Estimate | null;
  state: JobState;
  error?: string;
  startedAt?: string;
  finishedAt?: string;
  stages: Stage[];
  hasRecord: boolean;
  company?: string;
  ticker?: string;
  reportDate?: string;
  title?: string;
  grade?: string;
  note?: string;          // 사람이 적은 메모
}
