"use client";

import Link from "next/link";
import { ArrowUpRight, Boxes } from "lucide-react";
import type { ExtensionInfoPanelSchema } from "@/lib/types";

export default function ExtensionInfoPanel({
  schema,
}: {
  schema: ExtensionInfoPanelSchema;
}) {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-6">
      <section className="rounded-[28px] bg-white/72 p-6 shadow-[0_18px_48px_rgba(42,41,51,0.055)]">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[18px] bg-accent/10 text-accent">
            <Boxes size={21} />
          </div>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-secondary/60">
              Domain Extension
            </p>
            <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-text-primary">
              {schema.title}
            </h1>
            {schema.description && (
              <p className="mt-3 max-w-3xl text-sm leading-6 text-text-secondary">
                {schema.description}
              </p>
            )}
          </div>
        </div>
      </section>

      {schema.cards && schema.cards.length > 0 && (
        <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {schema.cards.map((card, index) => (
            <article
              key={`${card.title}-${index}`}
              className="min-h-[132px] rounded-[24px] bg-white/72 p-5 shadow-[0_12px_28px_rgba(42,41,51,0.045)] transition duration-200 ease-out hover:-translate-y-0.5 hover:bg-white/90 hover:shadow-[0_18px_42px_rgba(42,41,51,0.09)]"
            >
              <p className="text-[11px] font-medium text-text-secondary">
                {card.title}
              </p>
              <div className="mt-3 text-2xl font-semibold tracking-[-0.03em] text-text-primary">
                {card.value}
              </div>
              {card.description && (
                <p className="mt-3 text-xs leading-5 text-text-secondary/75">
                  {card.description}
                </p>
              )}
            </article>
          ))}
        </section>
      )}

      {schema.actions && schema.actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {schema.actions.map((action) => (
            <Link
              key={`${action.label}-${action.href}`}
              href={action.href}
              className={`inline-flex h-10 items-center gap-2 rounded-full px-4 text-sm font-medium transition ${
                action.variant === "primary"
                  ? "bg-accent text-white shadow-[0_16px_34px_rgba(87,73,244,0.22)] hover:bg-accent/90"
                  : "bg-bg-primary text-text-secondary hover:bg-bg-tertiary hover:text-text-primary"
              }`}
            >
              {action.label}
              <ArrowUpRight size={14} />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
