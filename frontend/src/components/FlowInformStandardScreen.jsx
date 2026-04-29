import React from "react";

const navGroups = [
  {
    label: "Data",
    active: false,
    items: ["데이터 브라우저", "스플릿 테이블", "대시보드", "ET 레포트"],
  },
  {
    label: "Work",
    active: true,
    items: ["인폼 로그", "이슈 추적", "회의 관리", "변경점 관리"],
  },
  {
    label: "Knowledge",
    active: false,
    items: ["에이전트", "사전지식", "워크플로우"],
  },
  {
    label: "Admin",
    active: false,
    items: ["관리자", "테이블 맵", "개발자 가이드"],
  },
];

const filters = ["전체", "확인중", "대기", "완료", "긴급"];
const tabs = ["전체 로그", "내 담당", "제품별", "최근 변경"];

const informs = [
  {
    id: "INF-24091",
    product: "PROD_A",
    time: "2026-04-29 14:32",
    author: "kimjh",
    status: "확인중",
    tone: "warning",
    title: "AZAAAB.1 WF6 ET drift 확인 요청",
    summary:
      "SplitTable 기준 WF6 edge zone에서 plan 대비 +8.4% 편차가 반복됩니다. 최근 recipe 변경점과 product rule 연결을 확인해야 합니다.",
    tags: ["fab_lot_id: AZAAAB.1", "wafer_id: 6", "module: ET", "root_lot_id: A12B3"],
  },
  {
    id: "INF-24088",
    product: "PROD_B",
    time: "2026-04-29 11:06",
    author: "parkms",
    status: "완료",
    tone: "success",
    title: "CD SEM 재측정 결과 정상 범위 복귀",
    summary:
      "재측정 lot 4건 모두 control limit 안으로 복귀했습니다. RCA 지식 항목은 기존 Item-17과 연결했습니다.",
    tags: ["fab_lot_id: BZ9912", "tool: CDSEM-03", "item: RCA-17"],
  },
  {
    id: "INF-24079",
    product: "PROD_C",
    time: "2026-04-28 18:44",
    author: "choiyr",
    status: "긴급",
    tone: "danger",
    title: "P2 hold lot 다수 발생, owner 확인 필요",
    summary:
      "동일 chamber 이력 lot에서 hold가 집중되어 있습니다. 원본 DB는 읽기 전용으로 유지하고 Files 입력 자료만 비교합니다.",
    tags: ["priority: P2", "chamber: CH-08", "owner: pending", "scope: Files only"],
  },
];

const tableRows = [
  ["Root Lot", "Fab Lot", "Wafer", "Status", "Owner"],
  ["A12B3", "AZAAAB.1", "WF6", "확인중", "kimjh"],
  ["B77C1", "BZ9912", "WF2", "완료", "parkms"],
  ["C19D4", "CA1208", "WF11", "긴급", "choiyr"],
];

const statusClass = {
  danger: "border-red-200 bg-red-50 text-red-700",
  warning: "border-orange-200 bg-orange-50 text-orange-700",
  info: "border-blue-200 bg-blue-50 text-blue-700",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700",
  neutral: "border-slate-200 bg-slate-50 text-slate-600",
};

function StatusBadge({ tone = "neutral", children }) {
  return (
    <span
      className={`inline-flex h-6 items-center rounded-md border px-2 text-xs font-semibold ${statusClass[tone] || statusClass.neutral}`}
    >
      {children}
    </span>
  );
}

function ContentTab({ active, children }) {
  return (
    <button
      type="button"
      className={
        active
          ? "h-8 rounded-md bg-orange-500 px-3 text-sm font-semibold text-white shadow-sm"
          : "h-8 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50"
      }
    >
      {children}
    </button>
  );
}

function FilterButton({ active, children }) {
  return (
    <button
      type="button"
      className={
        active
          ? "rounded-md border border-orange-300 bg-orange-50 px-3 py-1.5 text-xs font-semibold text-orange-700"
          : "rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
      }
    >
      {children}
    </button>
  );
}

function GnbDropdown({ group }) {
  return (
    <div className="group relative">
      <button
        type="button"
        className={
          group.active
            ? "h-9 rounded-md bg-orange-50 px-3 text-sm font-bold text-orange-700"
            : "h-9 rounded-md px-3 text-sm font-bold text-slate-600 hover:bg-slate-100"
        }
      >
        {group.label}
      </button>
      <div className="invisible absolute left-0 top-10 z-20 w-48 rounded-md border border-slate-200 bg-white p-1 opacity-0 shadow-lg transition group-hover:visible group-hover:opacity-100">
        {group.items.map((item) => (
          <button
            key={item}
            type="button"
            className={
              item === "인폼 로그"
                ? "flex w-full items-center rounded-md bg-orange-50 px-3 py-2 text-left text-sm font-semibold text-orange-700"
                : "flex w-full items-center rounded-md px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-50"
            }
          >
            {item}
          </button>
        ))}
      </div>
    </div>
  );
}

function InformRow({ item }) {
  return (
    <article className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-xs text-slate-500">
          <span className="rounded-md bg-slate-100 px-2 py-1 font-semibold text-slate-700">
            {item.product}
          </span>
          <span>{item.id}</span>
          <span>{item.time}</span>
          <span>{item.author}</span>
        </div>
        <StatusBadge tone={item.tone}>{item.status}</StatusBadge>
      </div>

      <div className="mt-3">
        <h2 className="text-base font-bold leading-6 text-slate-950">{item.title}</h2>
        <p className="mt-1 text-sm leading-6 text-slate-600">{item.summary}</p>
      </div>

      <div className="mt-3 flex max-h-16 flex-wrap gap-1.5 overflow-hidden">
        {item.tags.map((tag) => (
          <span
            key={tag}
            className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-medium text-slate-600"
          >
            {tag}
          </span>
        ))}
      </div>
    </article>
  );
}

export default function FlowInformStandardScreen() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-30 flex h-14 items-center border-b border-slate-200 bg-white px-4">
        <div className="mr-5 text-lg font-black tracking-normal text-slate-950">
          <span className="text-orange-500">flow</span>.
        </div>
        <nav className="flex items-center gap-1">
          {navGroups.map((group) => (
            <GnbDropdown key={group.label} group={group} />
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm text-slate-600">
          <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-medium">
            admin
          </span>
          <button
            type="button"
            className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Settings
          </button>
        </div>
      </header>

      <div className="grid min-h-[calc(100vh-56px)] grid-cols-[280px_minmax(0,1fr)]">
        <aside className="border-r border-slate-200 bg-white p-4">
          <div className="space-y-4">
            <div>
              <div className="text-xs font-bold uppercase tracking-normal text-slate-500">
                Search
              </div>
              <input
                className="mt-2 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
                placeholder="Lot, product, owner"
              />
            </div>

            <div>
              <div className="mb-2 text-xs font-bold uppercase tracking-normal text-slate-500">
                Status
              </div>
              <div className="flex flex-wrap gap-2">
                {filters.map((filter, index) => (
                  <FilterButton key={filter} active={index === 1}>
                    {filter}
                  </FilterButton>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="text-xs font-bold text-slate-700">Active Context</div>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-slate-500">Product</dt>
                  <dd className="font-semibold text-slate-800">PROD_A</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-slate-500">DB Root</dt>
                  <dd className="font-semibold text-slate-800">Read only</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-slate-500">Files</dt>
                  <dd className="font-semibold text-slate-800">Writable</dd>
                </div>
              </dl>
            </div>
          </div>
        </aside>

        <main className="min-w-0 p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-xl font-black tracking-normal text-slate-950">인폼 로그</h1>
              <p className="mt-1 text-sm text-slate-500">
                Lot, wafer, owner, RCA 연결 상태를 같은 행 구조로 확인합니다.
              </p>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Export
              </button>
              <button
                type="button"
                className="h-9 rounded-md bg-orange-500 px-3 text-sm font-semibold text-white shadow-sm hover:bg-orange-600"
              >
                새 인폼
              </button>
            </div>
          </div>

          <section className="mb-4 rounded-md border border-slate-200 bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                {tabs.map((tab, index) => (
                  <ContentTab key={tab} active={index === 0}>
                    {tab}
                  </ContentTab>
                ))}
              </div>
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <StatusBadge tone="warning">확인중 12</StatusBadge>
                <StatusBadge tone="danger">긴급 3</StatusBadge>
                <StatusBadge tone="success">완료 48</StatusBadge>
              </div>
            </div>
          </section>

          <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-4">
            <section className="min-w-0 space-y-3">
              {informs.map((item) => (
                <InformRow key={item.id} item={item} />
              ))}
            </section>

            <aside className="space-y-4">
              <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
                <h2 className="text-sm font-bold text-slate-950">RCA 연결 요약</h2>
                <div className="mt-3 space-y-3 text-sm">
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">Knowledge Items</span>
                    <span className="font-bold text-slate-900">37</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">Open Questions</span>
                    <span className="font-bold text-orange-600">4</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">DB Write</span>
                    <span className="font-bold text-slate-900">Blocked</span>
                  </div>
                </div>
              </section>

              <section className="overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-200 bg-slate-50 px-4 py-3">
                  <h2 className="text-sm font-bold text-slate-950">SplitTable Preview</h2>
                </div>
                <div className="p-3">
                  <table className="w-full border-collapse text-left text-sm">
                    <thead>
                      <tr>
                        {tableRows[0].map((header) => (
                          <th
                            key={header}
                            className="border-b border-slate-200 px-2 py-2 text-xs font-bold text-slate-500"
                          >
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {tableRows.slice(1).map((row) => (
                        <tr key={row.join("-")} className="hover:bg-slate-50">
                          {row.map((cell) => (
                            <td key={cell} className="border-b border-slate-100 px-2 py-2 text-xs text-slate-700">
                              {cell}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </aside>
          </div>
        </main>
      </div>
    </div>
  );
}
