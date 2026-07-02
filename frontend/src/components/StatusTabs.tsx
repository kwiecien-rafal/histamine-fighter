import { type KeyboardEvent, useRef } from "react";

interface StatusTabsProps<T extends string> {
  tabs: readonly T[];
  active: T;
  label: (tab: T) => string;
  onSelect: (tab: T) => void;
  // Accessible name of the tablist, and the ids wiring the tabs to their panel.
  ariaLabel: string;
  panelId: string;
  // Prefix for each tab's id, so the panel can label itself by the active tab.
  idPrefix: string;
}

// The WAI-ARIA tabs pattern: one tab is in the tab order (roving tabindex), and the
// arrow/Home/End keys move selection and focus between them. The selected tab controls
// the panel the caller renders with id={panelId}. Generic over the tab union so the
// caller keeps its own status type.
export function StatusTabs<T extends string>({
  tabs,
  active,
  label,
  onSelect,
  ariaLabel,
  panelId,
  idPrefix,
}: StatusTabsProps<T>) {
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = tabs.length - 1;
    let next: number;
    switch (event.key) {
      case "ArrowRight":
        next = index === last ? 0 : index + 1;
        break;
      case "ArrowLeft":
        next = index === 0 ? last : index - 1;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = last;
        break;
      default:
        return;
    }
    event.preventDefault();
    onSelect(tabs[next]);
    tabRefs.current[next]?.focus();
  };

  return (
    <div role="tablist" aria-label={ariaLabel} className="flex gap-2 mb-4 text-sm">
      {tabs.map((tab, index) => {
        const selected = tab === active;
        return (
          <button
            key={tab}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-${tab}`}
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(tab)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={
              selected
                ? "rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-emerald-800 cursor-pointer"
                : "rounded-full border border-stone-300 px-3 py-1 text-stone-600 hover:bg-stone-100 cursor-pointer"
            }
          >
            {label(tab)}
          </button>
        );
      })}
    </div>
  );
}
