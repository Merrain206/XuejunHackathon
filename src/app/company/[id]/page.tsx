import Link from "next/link";
import { CompanyExperience } from "@/components/company-experience";
import { companies, getCompany } from "@/lib/companies";

export default async function CompanyPage({ params }: PageProps<"/company/[id]">) {
  const { id } = await params;
  const company = getCompany(id);

  if (!company) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#f4f6f3] px-6 text-slate-950">
        <section className="w-full max-w-xl border border-slate-200 bg-white p-8 shadow-sm">
          <p className="eyebrow">Unsupported company</p>
          <h1 className="mt-3 text-2xl font-semibold">暂不支持公司代码 {id}</h1>
          <p className="mt-3 text-sm leading-6 text-slate-500">请选择当前已接入 Evidence 数据的公司，不会自动套用思看科技数据。</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {companies.map((item) => <Link className="question-chip" href={`/company/${item.id}`} key={item.id}>{item.name} · {item.id}</Link>)}
          </div>
        </section>
      </main>
    );
  }

  return <CompanyExperience company={company} key={company.id} />;
}
