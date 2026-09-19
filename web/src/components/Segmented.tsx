"use client";

type Option<T extends string | number> = { value: T; label: string; hint?: string; disabled?: boolean };

export default function Segmented<T extends string | number>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Option<T>[];
  onChange: (v: T) => void;
}) {
  return (
    <div>
      <span className="label block pb-1.5">{label}</span>
      <div role="radiogroup" aria-label={label} className="flex border border-rule-strong">
        {options.map((o) => {
          const on = o.value === value;
          return (
            <button
              key={String(o.value)}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={o.disabled}
              onClick={() => onChange(o.value)}
              className={`flex min-h-10 flex-1 flex-col items-start justify-center border-r border-rule-strong px-3 py-1.5 text-left last:border-r-0 disabled:opacity-40 ${
                on ? "bg-ink text-paper" : "bg-transparent text-ink hover:bg-paper-2"
              }`}
            >
              <span className="text-[13px] font-medium leading-tight">{o.label}</span>
              {o.hint && <span className={`num text-[10.5px] leading-tight ${on ? "text-paper/70" : "text-muted"}`}>{o.hint}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
