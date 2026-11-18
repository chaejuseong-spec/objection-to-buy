// 상세 설명서: 한 페이지. 색의 뜻, 판정 규칙, 탭별 보는 법, 문제가 생겼을 때.
// 판정 규칙은 tribunal/rules.py 와 같아야 한다. 규칙을 바꾸면 여기도 고친다.
import type { Metadata } from "next";
import Link from "next/link";
import s from "./guide.module.css";

export const metadata: Metadata = { title: "설명서 · 매수에 이의 있습니다!" };

const C = {
  red: { ink: "#B42318", soft: "#FDF1F0", line: "#D92D20" },
  orange: { ink: "#B54708", soft: "#FDF6EC", line: "#DC6803" },
  green: { ink: "#067647", soft: "#EFF8F3", line: "#17B26A" },
};

function Chip({ tone, children }: { tone: keyof typeof C; children: React.ReactNode }) {
  return (
    <span className={s.chip} style={{ color: C[tone].ink }}>
      <i style={{ background: C[tone].line }} aria-hidden="true" />
      {children}
    </span>
  );
}

const TOC = [
  ["what", "무엇을 하나"],
  ["flow", "쓰는 순서"],
  ["color", "색이 뜻하는 것"],
  ["verdict", "판정은 어떻게 정해지나"],
  ["tabs", "탭별로 보는 법"],
  ["words", "자주 나오는 말"],
  ["trouble", "문제가 생겼을 때"],
  ["note", "알아둘 것"],
] as const;

export default function GuidePage() {
  return (
    <div className={s.page}>
      <header className={s.top}>
        <Link href="/" className={s.back}>← 작업대로 돌아가기</Link>
      </header>

      <main className={s.body}>
        <p className={s.kicker}>설명서</p>
        <h1 className={s.title}>매수에 이의 있습니다! 사용법</h1>
        <p className={s.lead}>처음 쓰는 분이 이 한 페이지만 읽으면 결과를 읽을 수 있도록 정리했습니다.</p>

        <nav className={s.toc} aria-label="목차">
          {TOC.map(([id, label], i) => (
            <a key={id} href={`#${id}`}><span>{i + 1}</span>{label}</a>
          ))}
        </nav>

        <section id="what" className={s.section}>
          <h2>1. 무엇을 하나</h2>
          <p>
            증권사 리서치 리포트 PDF 한 편을 넣으면, AI 여러 명이 법정처럼 역할을 나눠 리포트의 주장을 따져 묻고 판정을 냅니다.
            리포트를 고치지는 않고 평가만 합니다. 매수, 보유, 매도는 사람이 정합니다.
          </p>
          <table className={s.table}>
            <thead><tr><th>순서</th><th>하는 일</th></tr></thead>
            <tbody>
              <tr><td>리포트 입력</td><td>PDF에서 주장 3~7개와 숫자 20~40개를 원문 그대로 옮겨 적습니다. 원문에 글자 그대로 없는 것은 코드가 버립니다.</td></tr>
              <tr><td>리스크 검토자</td><td>주장마다 약한 고리를 찾아 이의를 0~2개 냅니다. 못 찾으면 &ldquo;이의 없음&rdquo;입니다.</td></tr>
              <tr><td>배심원 5명</td><td>회계사, 산업 전문가, 공매도 투자자, 통계학자, 행동경제학자가 서로의 표를 보지 않고 이의마다 투표합니다. 놓친 이의가 있으면 1개까지 더할 수 있습니다.</td></tr>
              <tr><td>판사</td><td>코드가 정한 판정을 받아 핵심 쟁점과 판정 근거를 글로 정리합니다. 판정을 바꾸지는 않습니다.</td></tr>
              <tr><td>코드</td><td>표를 세어 판정을 정하고, 리포트에 적힌 식으로 숫자를 다시 계산합니다. AI는 판정과 숫자를 정하지 않습니다.</td></tr>
            </tbody>
          </table>
        </section>

        <section id="flow" className={s.section}>
          <h2>2. 쓰는 순서</h2>
          <ol className={s.steps}>
            <li><strong>PDF 올리기</strong>왼쪽 위 <b>새 리포트 올리기</b>를 누르거나, 화면 아무 곳에나 PDF를 끌어다 놓습니다. 올리기만 해서는 비용이 들지 않습니다. 글자를 고를 수 있는 PDF여야 하고, 스캔한 이미지 PDF는 읽지 못합니다.</li>
            <li><strong>비용 확인</strong>쪽수, AI 호출 수(보통 8번), 사용 모델, 최대 사용량이 나옵니다. 최대 사용량은 넉넉하게 잡은 상한이라 실제로는 대부분 더 적게 씁니다.</li>
            <li><strong>분석 시작</strong><b>비용을 확인했고, 분석을 시작합니다</b>를 누르면 그때부터 AI를 부르고 비용이 발생합니다. 보통 3분에서 10분 걸립니다.</li>
            <li><strong>기다리기</strong>진행 단계가 하나씩 체크됩니다. 창을 닫거나 다른 리포트를 봐도 분석은 계속되고, 다시 열면 이어서 보입니다. 분석은 한 번에 하나만 돌릴 수 있습니다.</li>
            <li><strong>결과 읽기</strong>끝나면 탭으로 결과를 봅니다. 아래 <a href="#tabs">탭별로 보는 법</a>을 참고하세요.</li>
            <li><strong>내 판단 남기기</strong><b>나의 판단 기록</b> 탭에 내 의견을 적어 두고 CSV로 내보낼 수 있습니다.</li>
          </ol>
        </section>

        <section id="color" className={s.section}>
          <h2>3. 색이 뜻하는 것</h2>
          <p>화면의 색은 판정에만 씁니다. 세 가지 색만 기억하면 됩니다. 그 밖의 강조는 모두 검은색입니다.</p>
          <div className={s.colors}>
            <div style={{ background: C.red.soft, borderColor: C.red.line }}>
              <strong style={{ color: C.red.ink }}>빨강: 고쳐야 함</strong>
              <p><Chip tone="red">수정</Chip> 이의가 맞고 근거도 있음<br />
                 <Chip tone="red">불일치</Chip> 다시 계산한 숫자가 다름<br />
                 <Chip tone="red">재심리</Chip> 핵심 주장에 수정이 있음<br />
                 <Chip tone="red">타당</Chip> 배심원이 이의에 찬성</p>
            </div>
            <div style={{ background: C.orange.soft, borderColor: C.orange.line }}>
              <strong style={{ color: C.orange.ink }}>주황: 직접 확인할 것</strong>
              <p><Chip tone="orange">확인 필요</Chip> 맞을 수도 있으나 근거가 부족함<br />
                 <Chip tone="orange">판정 보류</Chip> 표가 모자라 판정을 못 함<br />
                 <Chip tone="orange">확인 불가</Chip> 받은 자료로는 판단할 수 없음<br />
                 <Chip tone="orange">검증 불가</Chip> 공시와 대조할 자료나 기준이 부족함<br />
                 <Chip tone="orange">조건부 통과</Chip> 큰 문제는 없으나 확인할 곳이 남음</p>
            </div>
            <div style={{ background: C.green.soft, borderColor: C.green.line }}>
              <strong style={{ color: C.green.ink }}>초록: 문제없음</strong>
              <p><Chip tone="green">유지</Chip> 이의가 없거나 영향이 작음<br />
                 <Chip tone="green">일치</Chip> 다시 계산한 숫자가 같음<br />
                 <Chip tone="green">통과</Chip> 수정할 곳을 찾지 못함<br />
                 <Chip tone="green">과장</Chip> 배심원이 이의의 영향이 작다고 봄</p>
            </div>
          </div>
          <p className={s.muted}><b>원문에서 보기</b> 탭에서는 PDF 위 문장이 이 색으로 칠해집니다. 빨간 칠은 수정, 주황 칠은 확인 필요, 초록 칠은 유지된 주장입니다.</p>
        </section>

        <section id="verdict" className={s.section}>
          <h2>4. 판정은 어떻게 정해지나</h2>
          <p>판정은 AI가 고르지 않고 코드가 아래 규칙대로 계산합니다. &ldquo;확인 불가&rdquo;는 &lsquo;내 분야 밖이라 판단을 맡긴다&rsquo;는 표라서 판정을 막지 않습니다. 실제로 판단한 표(타당·과장)가 3표 이상일 때 그 가운데 과반으로 정합니다.</p>

          <h3>이의 하나의 판정</h3>
          <table className={s.table}>
            <thead><tr><th>배심원 표</th><th>판정</th></tr></thead>
            <tbody>
              <tr><td>판단한 표 가운데 &ldquo;타당&rdquo;이 과반이고, 리스크 검토자가 반대 근거를 댐</td><td><Chip tone="red">수정</Chip></td></tr>
              <tr><td>판단한 표 가운데 &ldquo;타당&rdquo;이 과반인데, 반대 근거가 없음</td><td><Chip tone="orange">확인 필요</Chip></td></tr>
              <tr><td>판단한 표 가운데 &ldquo;과장&rdquo;이 과반</td><td><Chip tone="green">유지</Chip></td></tr>
              <tr><td>판단한 표가 3표 미만 (대부분 &ldquo;확인 불가&rdquo;)</td><td><Chip tone="orange">확인 필요</Chip></td></tr>
              <tr><td>판단한 표가 타당과 과장으로 똑같이 갈림</td><td><Chip tone="orange">확인 필요</Chip></td></tr>
              <tr><td>낸 표(기권 제외)가 과반에 못 미침</td><td><Chip tone="orange">판정 보류</Chip></td></tr>
              <tr><td>배심원이 직접 추가한 이의</td><td><Chip tone="orange">확인 필요</Chip> 까지만</td></tr>
            </tbody>
          </table>

          <h3>주장 하나의 판정</h3>
          <p>이의가 없으면 <Chip tone="green">유지</Chip>입니다. 이의가 여러 개면 그중 가장 엄한 판정을 따릅니다. 엄한 순서는 수정 → 확인 필요 → 판정 보류 → 유지입니다.</p>

          <h3>리포트 전체 평가</h3>
          <table className={s.table}>
            <thead><tr><th>조건</th><th>평가</th><th>뜻</th></tr></thead>
            <tbody>
              <tr><td>핵심 주장(목표주가, 밸류에이션 등) 중 하나라도 수정</td><td><Chip tone="red">재심리</Chip></td><td>투자의견을 떠받치는 주장이 흔들립니다. 리포트를 다시 따져봐야 합니다.</td></tr>
              <tr><td>그 밖에 수정, 확인 필요, 판정 보류가 하나라도 있음</td><td><Chip tone="orange">조건부 통과</Chip></td><td>결론은 유지되지만, 표시된 곳을 직접 확인한 뒤 쓰세요.</td></tr>
              <tr><td>모든 주장이 유지</td><td><Chip tone="green">통과</Chip></td><td>주장과 숫자에서 수정할 곳을 찾지 못했습니다.</td></tr>
            </tbody>
          </table>

          <h3>숫자 검증</h3>
          <p>리포트가 식을 밝힌 숫자(예: 목표주가 = BPS × 목표 PBR)는 코드가 그 식으로 다시 계산합니다.
            허용 오차 안이면 <Chip tone="green">일치</Chip>, 벗어나면 <Chip tone="red">불일치</Chip>,
            &ldquo;약 70%&rdquo;처럼 값이 하나로 정해지지 않으면 <Chip tone="orange">확인 불가</Chip>입니다.
            같은 항목이 쪽마다 다르게 적혔는지도 확인합니다.</p>

          <h3>공시 대조 (OpenDART)</h3>
          <p>리포트에 적힌 과거 실적(매출액, 영업이익, 순이익, 자산·부채·자본 총계 등)을 금융감독원 전자공시의 사업보고서 재무제표와 코드가 대조합니다.
            회사, 연도, 연결/별도, 금액 단위를 맞춘 뒤, 리포트 표시 자릿수 1단위 안이면 <Chip tone="green">일치</Chip>,
            리포트를 쓸 때 이미 공시된 값과 다르면 <Chip tone="red">불일치</Chip>입니다.
            자료가 없거나 기준을 특정할 수 없거나, 리포트를 쓴 뒤에 나온 공시와만 다르면 짐작하지 않고 <Chip tone="orange">검증 불가</Chip>로 둡니다.
            2026E 같은 추정치와 비율은 대조하지 않습니다. 오른쪽 칸에서 출처, 공시 접수일, 조회 시점, DART 원문 링크를 볼 수 있습니다.</p>
        </section>

        <section id="tabs" className={s.section}>
          <h2>5. 탭별로 보는 법</h2>
          <dl className={s.defs}>
            <div><dt>리포트 요약</dt><dd>리포트 전체 평가와 한 줄 설명, 주장 목록과 각 판정이 나옵니다. 처음에는 여기서 빨간 주장부터 보세요.</dd></div>
            <div><dt>수치와 도표</dt><dd>리포트의 핵심 숫자, 연도별 표, 원문 도표와 숫자 검증 결과가 나옵니다. 쪽 번호를 누르면 원문 그 쪽으로 갑니다.</dd></div>
            <div><dt>원문에서 보기</dt><dd>PDF 원문 위에 판정 색이 칠해집니다. 칠한 곳을 누르면 오른쪽 칸에 그 주장의 이의와 배심원 표가 열립니다. 위쪽에서 색별로 거르거나 확대할 수 있습니다.</dd></div>
            <div><dt>반대신문 과정</dt><dd>주장마다 리스크 검토자의 이의, 배심원 5명의 표와 이유, 판사의 정리가 차례로 나옵니다. 판정이 왜 그렇게 나왔는지 여기서 확인합니다.</dd></div>
            <div><dt>채점</dt><dd>에이전트가 잘하고 있는지 점수로 재는 곳입니다. 리포트를 읽고 내가 찾은 문제를 적은 뒤, 에이전트 이의 중 같은 것을 체크하고 남은 이의와 숫자 검증을 맞음/틀림으로 고릅니다. 찾은 비율, 헛짚은 비율, 숫자 정확도가 나오고, 기록해 두면 에이전트를 고친 뒤의 점수와 비교할 수 있습니다. 같은 PDF를 다시 올려도 내가 적은 문제 목록은 그대로 이어집니다.</dd></div>
            <div><dt>나의 판단 기록</dt><dd>내 결론과 이유를 적어 두는 곳입니다. 저장한 기록은 오른쪽에 쌓이고 CSV로 내보낼 수 있습니다.</dd></div>
          </dl>
          <p className={s.muted}>오른쪽 위 <b>평가기록지 받기</b>를 누르면 결과 전체를 문서 파일(.md)로 받습니다. 왼쪽, 가운데, 오른쪽 칸은 따로 스크롤됩니다.</p>
        </section>

        <section id="words" className={s.section}>
          <h2>6. 자주 나오는 말</h2>
          <dl className={s.defs}>
            <div><dt>사실 오류</dt><dd>숫자, 날짜, 정의가 틀렸거나 리포트 안에서 서로 맞지 않는 경우입니다. 확인하면 맞고 틀림이 갈립니다.</dd></div>
            <div><dt>해석 차이</dt><dd>같은 사실을 다르게 읽은 경우입니다. 정답이 없을 수 있어 판단은 사람 몫입니다.</dd></div>
            <div><dt>의심 유형</dt><dd>숫자의 기준, 기간 선택, 가정의 근거, 논리 연결, 빠진 위험, 근거의 독립성. 리스크 검토자는 이의마다 이 중 하나를 붙입니다.</dd></div>
            <div><dt>타당 / 과장 / 확인 불가</dt><dd>배심원의 세 가지 표입니다. 타당은 &ldquo;이의가 맞고 결론이 달라질 수 있다&rdquo;, 과장은 &ldquo;맞지만 영향이 작다&rdquo;, 확인 불가는 &ldquo;받은 자료로는 모른다&rdquo;입니다.</dd></div>
            <div><dt>기권</dt><dd>배심원 AI가 답을 못 했거나 그 이의에 표를 내지 않은 경우입니다. 기권이 많으면 판정 보류가 됩니다.</dd></div>
            <div><dt>핵심 주장</dt><dd>투자의견을 직접 떠받치는 주장(목표주가, 밸류에이션)입니다. 여기에 수정이 나오면 리포트 전체가 재심리가 됩니다.</dd></div>
            <div><dt>투자 전에 직접 확인할 사항</dt><dd>확인 필요, 판정 보류, 핵심 주장의 수정 가운데 최대 5개를 골라 둔 목록입니다. 사실 오류가 앞에 오고, 확인한 것은 체크해 둘 수 있습니다.</dd></div>
          </dl>
        </section>

        <section id="trouble" className={s.section}>
          <h2>7. 문제가 생겼을 때</h2>
          <table className={s.table}>
            <thead><tr><th>이럴 때</th><th>이렇게 하세요</th></tr></thead>
            <tbody>
              <tr><td>보관함에 <b>멈춤</b>이 뜸</td><td>그 리포트를 누르면 이유가 나옵니다. <b>다시 시도</b>를 누르면 끝난 단계는 건너뛰고 멈춘 곳부터 이어서 합니다. 남은 단계만 비용이 듭니다.</td></tr>
              <tr><td>&ldquo;서버가 바쁨&rdquo;, 503, 500 오류</td><td>AI 회사 서버의 일시적인 문제입니다. 프로그램이 5초, 10초, 20초 기다리며 세 번 다시 부르고, 그래도 안 되면 예비 모델로 바꿔 봅니다. 그래도 멈췄다면 몇 분 뒤 다시 시도하세요. 실패한 호출은 보통 요금이 청구되지 않습니다.</td></tr>
              <tr><td>429 오류, 사용 한도</td><td>짧은 시간에 너무 많이 불렀거나 한도를 넘었습니다. 잠시 기다리거나 Google AI Studio에서 한도를 확인하세요.</td></tr>
              <tr><td>401, 403 오류, 키 문제</td><td>프로젝트 맨 위 <code>.env</code> 파일의 <code>ANTHROPIC_API_KEY</code>(Claude) 또는 <code>GEMINI_API_KEY</code>(Gemini)가 맞는지 확인하세요.</td></tr>
              <tr><td>&ldquo;PDF를 읽지 못했습니다&rdquo;</td><td>파일이 깨졌거나 스캔한 이미지 PDF일 수 있습니다. 글자를 고를 수 있는 PDF로 다시 올리세요.</td></tr>
              <tr><td>시작 단추가 눌리지 않음</td><td>다른 리포트를 분석 중입니다. 분석은 한 번에 하나만 됩니다. 끝나면 누를 수 있습니다.</td></tr>
              <tr><td>화면이 안 뜸</td><td>바탕화면 아이콘을 다시 누르세요. 아이콘이 연 검은 창을 닫으면 화면도 꺼지니, 쓰는 동안에는 열어 두세요.</td></tr>
              <tr><td>분석이 계속 실패함</td><td>프로젝트 안 <code>library</code> 폴더에서 그 리포트 폴더의 <code>log.txt</code>를 열면 자세한 이유가 있습니다.</td></tr>
            </tbody>
          </table>
        </section>

        <section id="note" className={s.section}>
          <h2>8. 알아둘 것</h2>
          <ul className={s.list}>
            <li>결과는 AI와 코드가 만든 평가 초안이며 투자 권유가 아닙니다.</li>
            <li>AI는 리포트 날짜 이후의 일을 쓰지 않도록 되어 있고, 외부 공시와 대조하지 않습니다. 리포트 안의 숫자와 논리가 서로 맞는지를 봅니다.</li>
            <li>보관함의 데모 리포트는 시연용입니다. 숫자 검증과 원문 대조는 실제 계산이지만, 리스크 검토자, 배심원, 판사의 답은 정해진 가짜 답입니다.</li>
            <li>올린 PDF와 결과는 프로젝트 안 <code>library</code> 폴더에 저장됩니다. 보관함에서 지우면 파일도 함께 지워집니다.</li>
            <li>나의 판단 기록과 체크 표시는 이 컴퓨터의 이 브라우저에만 저장됩니다.</li>
          </ul>
        </section>

        <footer className={s.foot}>
          <Link href="/" className={s.backBtn}>작업대로 돌아가기</Link>
        </footer>
      </main>
    </div>
  );
}
