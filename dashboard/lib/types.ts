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
  via?: string;                       // 출처 이름을 따로 적은 결과 (예: DART·KRX = 공시 보유 주식 수 × KRX 종가)
  matched_by?: string;                // ai_pool(공시에서 같은 값), ai_ratio(공시 두 숫자로 계산) 등
  material?: string;                  // 계산 재료를 공시와 대조한 결과면 그 재료 (예: 7,099천주). 숫자 자신의 대조가 아니다
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
  /** 주장 밖 지적의 판사 판정 근거 (2026-11-20 추가, 예전 결과와 주장에 묶인 지적은 null) */
  ruling?: { reason: string; impact: string; fix_part: string | null } | null;
  split?: boolean; claim_id?: string | null; page?: number | null; quote?: string; quote_found?: boolean;
  /** 인용이 든 원문 문장 전체 (앞 + 짚은 구절 + 뒤). 코드가 원문에서 잘라 붙인다. 예전 결과에는 없을 수 있다 */
  quote_ctx?: { before: string; quote: string; after: string; page: number } | null;
  /** 배심원이 추가한 이의를, 이의를 낸 배심원을 뺀 나머지가 2차 표결했는가 */
  round2?: boolean;
  /** 판정 사유(유지: 이의 불인정 / 경미한 지적, 확인 필요: 중요도 의견 갈림 등)와 이의의 중요도(동의표의 다수) */
  verdict_reason?: string; importance?: string | null; importance_split?: boolean;
};
export type Claim = Omit<RawClaim, "objections"> & {
  objections: Objection[]; code?: CodeFinding[];
  dependency?: { from: string[]; verdict: string };   // 결론 주장(목표주가 등)이 근거 주장을 따라 바뀐 경우 (판정 규칙 9장 (가-2))
};

type RawMark = RawResult["marks"][number];
export type Result = Omit<RawResult, "numbers" | "claims" | "marks"> & {
  marks: (RawMark & { pass?: boolean; ctx?: number[][] })[];     // pass: 통과한 곳(초록). ctx: 지적이 걸린 문장 전체 위치(연하게)
  numbers: Omit<RawResult["numbers"], "results"> & { results: NumberCheck[] };
  claims: Claim[];
  external?: Record<string, ExternalSummary | null> | null;
  evidence?: EvidenceTable | null;
  code_findings?: Record<string, CodeFinding[]>;
  other_objections?: Objection[];     // 원문에서 찾았고 추출된 주장과 묶이지 않은 이의
  company?: Company | null;           // 기본 종목정보 (지금 기준 재무·실적·배당·시세). 파이썬 tribunal/external/company.py
};

/* ───────── 기본 종목정보 (2026-11-22) ───────── */
export type FinKey = "revenue" | "op" | "pretax" | "ni" | "ni_owner" | "assets" | "liab" | "equity" | "equity_owner"
  | "cur_assets" | "cur_liab" | "cfo" | "cfi" | "cff" | "cogs" | "gross" | "sga" | "inventory" | "capex"
  | "ins_profit" | "inv_profit" | "div_rev" | "da";   // 보험사 손익 (DART 손익계산서)
/** 한 기간의 핵심 계정(원). 없는 값은 null. derived: 계산으로 만든 값(4분기 = 연간 − 3분기 누적 등), missing: 공시 API 값이 틀려 비운 값 */
export type FinPeriod = { period: string; end: string; rcept_no: string; derived?: string[]; missing?: string[];
  da?: number | null; da_basis?: string; doc?: boolean;
  roe?: number | null; roa?: number | null; nci?: number | null; ni_nci?: number | null; eps_disc?: number | null } & Partial<Record<FinKey, number | null>>;   // roe·roa: Python add_returns (최근 4분기 / 연간)   // da: 감가상각비+상각비 (연간, 주석)
export interface CoreCell {
  v: number | null;
  /** reported 그 해 보고서 값 / derived 계산 / restated 다음 해 보고서의 비교 숫자 / na 해당 없음 */
  st: "reported" | "derived" | "restated" | "na" | "old_none" | "new_none" | "absent";
  why?: string;
  name?: string; from?: string; old_basis?: boolean; verified?: boolean;
  /** 회사가 양식을 바꾼 해 (그 앞 해 값이면 '양식 변경 전'), old_none 칸은 같은 뜻의 옛 줄이 없음 */
  old_form?: number; form_year?: number;
  /** 처음 공시 값(같은 묶음을 나중 보고서 값으로 맞췄을 때), 다음 해 보고서가 다시 적은 값, 정의 변경 전 */
  first_v?: number | null; later_v?: number; later_from?: string; old_def?: boolean; def_change?: boolean;
}
export interface CoreStatements {
  template: string; basis: string; years: number[];
  items: { key: string; label: string; sj: "IS" | "BS" | "CF"; need: boolean; parent: string | null }[];
  cells: Record<string, Record<string, CoreCell>>;
  checks: Record<string, { ok: number; bad: number }>;
  form_year?: number | null;
  sources: Record<string, string>;
}
export interface StatementTable {
  accounts: { id: string; name: string; key: string | null }[];
  annual: Record<string, Record<string, number | null>>;
  quarter: Record<string, Record<string, number | null>>;
  missing?: [string, string][];
  /** [기간, 계정]: 그 기간 보고서에 없어 다음 해 보고서의 비교(전기·전전기) 숫자로 채운 칸 */
  prior?: [string, string][];
  doc?: [string, string][];          // 그 분기 보고서 원문 표에서 읽은 칸 (재무 API 에 없는 금융회사 2023년 1·2분기 재무상태)
}
export interface PriceDay {
  date: string; close: number | null; high: number | null; low: number | null; open: number | null;
  change: number | null; rate: number | null; mktcap: number | null; shares: number | null; market: string;
}
/** 동종사 추이 비교용: 체크한 회사 하나의 재무 추이 (/api/reports/[id]/peer) */
export interface PeerSeries {
  error?: string; made_at: string; ticker: string; name: string; financial: boolean; fs?: string | null;
  annual: FinPeriod[]; quarter: FinPeriod[];
  insurance?: Company["insurance"]; sector?: Pick<Sector, "part" | "sector" | "company" | "month" | "metrics" | "series"> | null;
  valuation?: Company["valuation"];                     // 상장사: 분기 말 종가·시가총액·PER·PBR (추이 비교)
}
/** 동종사 추이 비교용: 금감원 FISIS 에 있는 회사 하나(비상장 포함)의 업종 지표·CSM 분기 추이 (회사 개별 기준) */
export interface FisisPeer {
  error?: string; made_at: string; finance_cd: string; part: string; name: string; full_name: string; sector: string;
  metrics: Sector["metrics"]; series: ({ period: string } & Record<string, number | string | null>)[];
  dart?: PeerSeries;                  // DART 에 정기보고서를 내는 비상장사면 재무제표 (매출·순이익·ROE 등)
  fs_quarter?: FinPeriod[]; fs_annual?: FinPeriod[];   // 보험사 금감원 재무 요약 (DART 정기보고서가 없는 회사용, 회사 개별 기준)
}
export interface Company {
  error?: string;
  v?: number;                          // 자료 형식 번호 (company.py CO_V)
  made_at: string; ticker: string; name: string; financial: boolean;
  info: Partial<Record<"corp_name" | "corp_name_eng" | "ceo_nm" | "est_dt" | "hm_url" | "induty_code" | "acc_mt" | "adres", string>>;
  price: PriceDay | null;
  range52: { high: number; low: number; days: number } | null;
  price_days: number;
  statements: { fs: string | null; annual: FinPeriod[]; quarter: FinPeriod[]; tables: Partial<Record<"IS" | "BS" | "CF", StatementTable>>;
                sources?: { period: string; rcept_no: string; url: string; doc?: boolean }[];
                /** 금융회사 옛 해(재무 API 없는 해)를 사업보고서 원문 재무제표 표로 채운 결과 */
                doc_check?: { year: number; ok: boolean | null; why?: string; filled?: string[] };
                /** 보험사: 이 해 이전은 옛 회계기준 */
                old_rule?: { before: string; label: string } };
  dividends: { year: number; rcept_no: string; dps?: number; yield?: number; payout?: number }[];
  /** 보험사만: CSM 잔액·신계약 CSM·CSM 상각액(원, 분기 3개월 값), 지급여력비율(%) */
  insurance?: { period: string; end: string; rcept_no: string; csm?: number | null; csm_new?: number | null; csm_amort?: number | null;
                kics?: number | null; kics_from?: string; derived?: string[]; src?: { csm?: string | null; kics?: string | null } }[] | null;
  metrics: { eps: number | null; bps: number | null; per: number | null; pbr: number | null; roe: number | null;
    shares?: number | null; div_yield?: number | null; div_year?: number | null; basis: Record<string, string> };
  shares_float?: { n: number; label: string; rcept_no: string } | null;
  /** 최대주주 (최근 사업보고서): 이름, 보통주 지분율, 특수관계인 포함 지분율 */
  holder?: { name: string; share: number | null; group: number | null; year: number } | null;
  /** 분기 말 PER·PBR 추이: 분기 마지막 거래일 시가총액(KRX) ÷ 최근 4분기 지배주주 순이익 / 분기 말 지배주주 자본(DART) */
  valuation?: { period: string; date: string; close: number | null; mktcap: number | null; per: number | null; pbr: number | null;
    shares?: number | null; eps?: number | null; eps_y?: number | null; bps_calc?: boolean; divy?: number | null; div_from?: number | null; known?: string | null; per_from?: string | null; bps?: number | null; split?: number; split_at?: number }[] | null;
  /** 사업보고서 본문 표: 연구개발비(원, 매출액 대비 %는 공시값), 수주잔고(원, 연말) */
  business?: { rnd: { year: number; amount: number; ratio: number | null; rcept_no: string }[];
               backlog: { year: number; amount: number; basis: string; rcept_no: string }[]; source: string } | null;
  /** 핵심 재무제표 (금융회사, 업권별 20~30줄, 연간 10년). cells[항목][연도] */
  core?: CoreStatements | null;
  sources: Record<string, string>;
  price_checked_at?: string;          // 시세를 마지막으로 확인한 시각 (화면이 하루 한 번 새로 받는다)
  peers?: Peers | null;
  sector?: Sector | null;
  /** 매출 구성 (사업보고서 '사업의 내용'의 제품·서비스별 매출 비중 표, 보험사는 보험종목별 수입보험료). 못 찾으면 null */
  sales_mix?: { items: { name: string; amount: number | null; share: number }[]; title: string; year: number; rcept_no: string; table: string;
                adjust?: number | null; total?: number | null; total_label?: string;
                profit?: { items: { name: string; amount: number }[]; total: number | null } | null } | null;             // 금융회사: 금감원 FISIS 업종 지표 (분기 추이 + 업계 전체 비교, 비상장 포함)
}
export interface Sector {
  error?: string; part: string; sector: string; company: string; month: string | null;
  metrics: { key: string; label: string; group: string; unit: string; term?: string; desc?: string }[];
  series: ({ period: string } & Record<string, number | string | null>)[];
  peers: ({ finance_cd: string; name: string; full_name: string; me: boolean } & Record<string, number | string | boolean | null>)[];
}
export interface IndustryRow {
  finance_cd: string; name: string; full_name: string; me: boolean;
  kics: number | null; kics_basis: string; kics_after: number | null;
  assets: number | null; equity: number | null; ins_profit: number | null; inv_profit: number | null; ni: number | null;
  op_margin: number | null; inv_yield: number | null; new_rate: number | null; lapse_rate: number | null; roa: number | null;
}
export interface Industry { error?: string; made_at?: string; part?: string; part_name?: string; month?: string; rows?: IndustryRow[]; source?: string; basis?: string }
export interface PeerRow {
  ticker: string | null; corp_code?: string; listed?: boolean; assets?: number | null;   // 금융 경쟁사는 비상장(ticker 없음)도 (금감원 업권 목록)
  name: string; error?: string; induty?: string; close?: number | null; mktcap?: number | null; rate?: number | null; day?: string;
  fs?: string | null; period?: string | null; per?: number | null; pbr?: number | null; roe?: number | null; debt?: number | null;
  op?: number | null; ni4?: number | null; equity?: number | null; eps?: number | null; bps?: number | null;
  csm?: number | null; csm_new?: number | null; kics?: number | null; kics_src?: string;
}
export interface Peers { error?: string; made_at?: string; induty?: string; insurer?: boolean; basis?: string; rows?: PeerRow[];
  sector?: { id: string; name: string; path?: string[] } | null;
  fin?: { part: string; name: string; count: number } }   // 금융: 금감원 업권 회사로 고른 경쟁사   // 경쟁사 표를 만든 토스 섹터 (전면 개편 1차)
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
