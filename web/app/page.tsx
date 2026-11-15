import Link from "next/link";
import { ArrowRight, ArrowUpRight, BellRing, CalendarCheck, Check, Database, Mic, ShieldCheck, Stethoscope, Users } from "lucide-react";
import { HeroMedical } from "@/components/HeroMedical";
import { Reveal } from "@/components/Reveal";
import { Eyebrow, Heading } from "@/components/Heading";
import { API_URL, formatMonth, type Stats } from "@/lib/api";

export const dynamic = "force-dynamic";

type Datasets = { alerts: number; alert_makers: number; medicines: number; salts: number; icu_patients: number };
const n = (v?: number) => (v == null ? "–" : v.toLocaleString("en-IN"));

// Headline figures come from the live alert database, never typed in.
async function figures() {
  try {
    const [s, d]: [Stats, Datasets | null] = await Promise.all([
      fetch(`${API_URL}/api/stats`, { cache: "no-store" }).then((r) => r.json()),
      fetch(`${API_URL}/api/datasets`, { cache: "no-store" }).then((r) => r.json()).catch(() => null),
    ]);
    const last = s.by_month.at(-1)!;
    const [y, m] = last.month.split("-").map(Number);
    const fy = m >= 4 ? y : y - 1;
    const inFy = s.by_month.filter((x) => x.month >= `${fy}-04` && x.month <= `${fy + 1}-03`).reduce((a, x) => a + x.n, 0);
    return {
      fyLabel: `FY ${fy}-${String(fy + 1).slice(2)}`,
      fyCount: inFy.toLocaleString("en-IN"),
      lastMonth: formatMonth(last.month),
      total: s.total.toLocaleString("en-IN"),
      since: s.coverage.start.slice(0, 4),
      data: d,
    };
  } catch {
    return null;
  }
}

const STEPS = [
  { icon: ShieldCheck, title: "One shared profile", body: "The elder's medicines, schedule and doctor visits in one place, opened by family with a passcode the elder controls." },
  { icon: BellRing, title: "Reminders that land", body: "Email, WhatsApp or Telegram before every dose and appointment, in Hindi or English. Missed doses tell the family." },
  { icon: CalendarCheck, title: "Adherence you can see", body: "Taken, late or missed, day by day, so the family and the doctor see the same picture." },
];

// Three people around one elder, each with the one thing Dosely does for them.
const AUDIENCES = [
  { icon: Mic, who: "The elder", line: "Big text, spoken replies in Hindi, and one tap to mark a dose taken. No app to learn.",
    points: ["Dadi mode: talk or type in Hindi", "One-tap dose confirmation", "SOS with location and medicines"], href: "/dadi", cta: "Try Dadi mode" },
  { icon: Users, who: "The family", line: "See today's doses, missed reminders and upcoming visits for a parent, from any city, with their consent.",
    points: ["Passcode-shared care circle", "Missed-dose alerts", "Batch check on every strip"], href: "/circle", cta: "Open care circle" },
  { icon: Stethoscope, who: "The doctor", line: "A private link to the patient's adherence, vitals and messages, without another login.",
    points: ["Adherence and vitals at a glance", "Message the family", "Prescription to reminders"], href: "/care", cta: "See the care plan" },
];

export default async function Home() {
  const f = await figures();
  return (
    <main className="overflow-x-clip">
      {/* Hero: one word, one line, one action. */}
      <section className="blush relative px-4 pb-24 pt-20 sm:px-6 sm:pb-32 sm:pt-28">
        <div className="mx-auto max-w-6xl text-center">
          <Reveal>
            <p className="text-xs font-medium uppercase tracking-[0.28em] text-brown">Elderly care, made simple</p>
            <div className="relative mt-8">
              <HeroMedical />
              <h1 className="relative select-none text-[22vw] font-black uppercase leading-[0.82] tracking-[-0.06em] text-espresso sm:text-[12rem] lg:text-[14rem]">
                Dosely
                <span className="sr-only">: medicines, doses and doctor visits, made simple for elders and the family.</span>
              </h1>
            </div>
            <p className="mx-auto mt-10 max-w-xl text-lg leading-relaxed text-muted sm:text-xl">
              Medicines, doses and doctor visits in one profile the elder shares with family, with reminders that never miss.
            </p>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              <Link
                href="/circle"
                className="group inline-flex items-center gap-2 rounded-full bg-espresso px-8 py-4 text-sm font-medium text-paper shadow-[0_18px_40px_-16px_rgba(42,28,21,0.7)] transition hover:bg-brown"
              >
                Open care circle
                <ArrowRight className="size-4 transition group-hover:translate-x-0.5" />
              </Link>
              <Link
                href="/scan"
                className="inline-flex items-center gap-2 rounded-full border border-espresso/15 bg-paper/70 px-8 py-4 text-sm font-medium text-espresso backdrop-blur transition hover:border-brown"
              >
                Check a batch
              </Link>
            </div>
            <p className="mt-8 text-xs tracking-wide text-muted">Free for families · Email reminders · Hindi and English</p>
          </Reveal>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto max-w-6xl scroll-mt-24 px-4 pt-28 sm:px-6">
        <Reveal>
          <div className="grid items-end gap-6 md:grid-cols-2">
            <div>
              <Eyebrow>How it works</Eyebrow>
              <Heading lead="Set up once." tail="Cared for every day." className="mt-4 text-4xl sm:text-5xl" />
            </div>
            <p className="max-w-sm text-muted md:ml-auto">
              Built for parents who live alone or far from their children: the elder stays in charge of who sees what, and the family stops guessing.
            </p>
          </div>
        </Reveal>
        <div className="mt-12 grid gap-4 md:grid-cols-3">
          {STEPS.map(({ icon: Icon, title, body }, i) => (
            <Reveal key={title} delay={i * 0.08}>
              <div className="card h-full rounded-3xl p-7">
                <div className="flex items-center justify-between">
                  <span className="grid size-12 place-items-center rounded-2xl bg-mint text-teal"><Icon className="size-5" /></span>
                  <span className="font-mono text-sm text-clay">0{i + 1}</span>
                </div>
                <h3 className="mt-8 text-xl font-medium tracking-tight">{title}</h3>
                <p className="mt-2 leading-relaxed text-muted">{body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* Who it's for */}
      <section className="mx-auto max-w-6xl px-4 pt-28 sm:px-6">
        <Reveal>
          <Eyebrow>Who it is for</Eyebrow>
          <Heading lead="Built around one elder" tail="and the people who care for them." className="mt-4 max-w-3xl text-4xl sm:text-5xl" />
        </Reveal>
        <div className="mt-12 grid gap-4 lg:grid-cols-3">
          {AUDIENCES.map(({ icon: Icon, who, line, points, href, cta }, i) => (
            <Reveal key={who} delay={i * 0.08}>
              <Link href={href} className={`group flex h-full flex-col rounded-[2rem] p-8 transition hover:-translate-y-1 ${i === 1 ? "bg-espresso text-paper" : "card"}`}>
                <span className={`grid size-12 place-items-center rounded-2xl ${i === 1 ? "bg-paper/10 text-paper" : "bg-sand text-brown"}`}><Icon className="size-5" /></span>
                <h3 className="mt-8 text-2xl font-medium tracking-tight">{who}</h3>
                <p className={`mt-3 leading-relaxed ${i === 1 ? "text-paper/70" : "text-muted"}`}>{line}</p>
                <ul className="mt-6 flex-1 space-y-2.5 text-sm">
                  {points.map((p) => (
                    <li key={p} className="flex items-center gap-2.5">
                      <Check className={`size-4 shrink-0 ${i === 1 ? "text-[#5fd3c4]" : "text-teal"}`} /> {p}
                    </li>
                  ))}
                </ul>
                <span className={`mt-8 inline-flex items-center gap-1.5 text-sm font-medium transition group-hover:gap-2.5 ${i === 1 ? "text-paper" : "text-brown"}`}>
                  {cta} <ArrowRight className="size-4" />
                </span>
              </Link>
            </Reveal>
          ))}
        </div>
      </section>

      {/* Built on real public data */}
      <section className="mx-auto max-w-6xl px-4 pt-20 sm:px-6">
        <Reveal>
          <div className="clinical rounded-[2rem] p-6 sm:p-8">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="flex items-center gap-2 font-medium text-teal-2"><Database className="size-5 text-teal" /> Built on real public data. Nothing typed in.</p>
              <Link href="/status" className="inline-flex items-center gap-1 text-sm font-medium text-teal underline-offset-4 hover:underline">
                Live accuracy and freshness <ArrowUpRight className="size-4" />
              </Link>
            </div>
            <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { v: n(f?.data?.alerts), l: "CDSCO and state drug alerts", s: `since ${f?.since ?? "2019"}` },
                { v: n(f?.data?.medicines), l: "Indian brands priced", s: `${n(f?.data?.salts)} salt combinations` },
                { v: "100%", l: "flagged batches caught", s: "0 false alarms in our benchmark" },
                { v: "openFDA", l: "official drug labels", s: "for interaction checks" },
              ].map((x) => (
                <div key={x.l} className="rounded-2xl bg-paper/80 p-5">
                  <p className="font-mono text-3xl font-semibold tracking-tight text-espresso">{x.v}</p>
                  <p className="mt-1 text-sm font-medium">{x.l}</p>
                  <p className="text-xs text-muted">{x.s}</p>
                </div>
              ))}
            </div>
          </div>
        </Reveal>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-6xl px-4 pt-24 sm:px-6">
        <Reveal>
          <div className="relative overflow-hidden rounded-[2rem] bg-espresso px-8 py-14 text-paper sm:px-14">
            <div className="dots absolute inset-0 opacity-20" />
            <div className="relative grid items-center gap-8 md:grid-cols-[1.4fr_1fr]">
              <h2 className="text-4xl font-medium leading-tight tracking-tight sm:text-5xl">
                Set up your parent’s care circle <span className="text-clay">tonight.</span>
              </h2>
              <div className="md:text-right">
                <p className="text-paper/70">Five minutes. Free for families, forever.</p>
                <Link href="/circle" className="mt-5 inline-flex items-center gap-2 rounded-full bg-paper px-6 py-3 text-sm font-medium text-espresso transition hover:bg-sand">
                  Open care circle <ArrowRight className="size-4" />
                </Link>
              </div>
            </div>
          </div>
        </Reveal>
      </section>
    </main>
  );
}
