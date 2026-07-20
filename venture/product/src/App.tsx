const repository = import.meta.env.VITE_REPOSITORY_URL || 'https://github.com/'

const steps = [
  '공개 시장 신호 수집',
  '독립 근거와 문제 품질 검증',
  '비-AI 우선 사업 아이템 평가',
  '첫 사용자 접근 경로 확인',
]

function issueUrl(template: string) {
  return `${repository.replace(/\/$/, '')}/issues/new?template=${template}`
}

export default function App() {
  return (
    <main className="min-h-screen bg-stone-50 text-stone-900">
      <section className="mx-auto flex min-h-[72vh] max-w-5xl flex-col justify-center px-6 py-20 sm:px-10">
        <p className="mb-5 text-sm font-semibold tracking-[0.18em] text-emerald-800 uppercase">
          ZeroFounder · Discovery
        </p>
        <h1 className="max-w-3xl text-4xl leading-tight font-semibold tracking-tight sm:text-6xl">
          아직 제품을 정하지 않았습니다.
        </h1>
        <p className="mt-7 max-w-2xl text-lg leading-8 text-stone-600">
          반복되는 실제 문제와 첫 사용자에게 도달할 방법을 확인한 뒤에만 제품을 선택합니다.
          근거가 충분하지 않다면 서두르지 않고 조사를 계속합니다.
        </p>
        <ol className="mt-12 grid gap-4 sm:grid-cols-2" aria-label="현재 조사 절차">
          {steps.map((step, index) => (
            <li key={step} className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
              <span className="text-sm font-semibold text-emerald-700">0{index + 1}</span>
              <p className="mt-2 font-medium">{step}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="border-y border-stone-200 bg-white">
        <div className="mx-auto grid max-w-5xl gap-8 px-6 py-12 sm:grid-cols-3 sm:px-10">
          <div>
            <h2 className="font-semibold">시장 문제 제보</h2>
            <p className="mt-2 text-sm leading-6 text-stone-600">반복해서 겪는 구체적인 불편과 공개 근거를 알려주세요.</p>
            <a className="mt-4 inline-block font-medium text-emerald-800 underline" href={issueUrl('market-problem.yml')}>문제 제보하기</a>
          </div>
          <div>
            <h2 className="font-semibold">공개 운영 기록</h2>
            <p className="mt-2 text-sm leading-6 text-stone-600">선정 근거, 로드맵, 변경 기록은 저장소에 공개됩니다.</p>
            <a className="mt-4 inline-block font-medium text-emerald-800 underline" href={repository}>저장소 보기</a>
          </div>
          <div>
            <h2 className="font-semibold">인간의 최종 감독</h2>
            <p className="mt-2 text-sm leading-6 text-stone-600">코드와 중요한 사업 결정은 Pull Request와 승인 Issue로 검토합니다.</p>
          </div>
        </div>
      </section>

      <footer className="mx-auto flex max-w-5xl flex-col gap-3 px-6 py-10 text-sm text-stone-600 sm:flex-row sm:justify-between sm:px-10">
        <p>개인정보와 방문 분석을 기본 수집하지 않습니다.</p>
        <nav className="flex gap-5" aria-label="지원 링크">
          <a className="underline" href={issueUrl('bug-report.yml')}>버그 신고</a>
          <a className="underline" href={issueUrl('feature-request.yml')}>기능 요청</a>
        </nav>
      </footer>
    </main>
  )
}

