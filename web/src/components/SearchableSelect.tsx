import { useCombobox } from "downshift";
import { useEffect, useMemo, useState } from "react";

export interface SearchableSelectOption {
  id: string;
  label: string;
  sublabel?: string;
}

interface SearchableSelectProps {
  id: string;
  label: string;
  placeholder: string;
  options: SearchableSelectOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  help?: string;
  emptyHint?: string;
}

function sortOptions(options: SearchableSelectOption[]): SearchableSelectOption[] {
  return [...options].sort((a, b) =>
    a.label.localeCompare(b.label, undefined, { sensitivity: "base" }),
  );
}

function filterOptions(
  options: SearchableSelectOption[],
  query: string,
): SearchableSelectOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return options;
  return options.filter((o) => {
    const hay = `${o.label} ${o.sublabel ?? ""} ${o.id}`.toLowerCase();
    return hay.includes(q);
  });
}

export function SearchableSelect({
  id,
  label,
  placeholder,
  options,
  value,
  onChange,
  disabled = false,
  help,
  emptyHint = "No matches — try a different search.",
}: SearchableSelectProps) {
  const sorted = useMemo(() => sortOptions(options), [options]);
  const selected = useMemo(
    () => sorted.find((o) => o.id === value) ?? null,
    [sorted, value],
  );

  const [inputValue, setInputValue] = useState(selected?.label ?? "");

  useEffect(() => {
    setInputValue(selected?.label ?? "");
  }, [selected?.label, value]);

  const items = useMemo(
    () => filterOptions(sorted, inputValue),
    [sorted, inputValue],
  );

  const {
    isOpen,
    getMenuProps,
    getInputProps,
    getItemProps,
    highlightedIndex,
    openMenu,
    closeMenu,
  } = useCombobox({
    items,
    inputValue,
    selectedItem: selected,
    itemToString: (item) => item?.label ?? "",
    onInputValueChange: ({ inputValue: next }) => {
      setInputValue(next ?? "");
      if (!(next ?? "").trim()) onChange("");
    },
    onSelectedItemChange: ({ selectedItem: item }) => {
      if (item) {
        onChange(item.id);
        setInputValue(item.label);
      }
    },
  });

  return (
    <div className={`mb-4 relative ${disabled ? "opacity-60 pointer-events-none" : ""}`}>
      <label htmlFor={id} className="block text-sm font-medium text-brand-dark mb-1.5">
        {label}
      </label>
      {help ? <p className="text-sm text-gray-600 mb-2">{help}</p> : null}
      <input
        className="input-field"
        disabled={disabled}
        {...getInputProps({
          id,
          placeholder,
          onFocus: () => {
            if (!disabled) openMenu();
          },
          onBlur: () => {
            closeMenu();
            setInputValue(selected?.label ?? "");
          },
        })}
      />
      {selected?.sublabel ? (
        <p className="text-xs text-gray-500 mt-1">{selected.sublabel}</p>
      ) : null}
      <ul
        {...getMenuProps()}
        className={`absolute z-20 mt-1 w-full max-h-56 overflow-auto rounded-lg border border-gray-200 bg-white shadow-lg ${
          isOpen && !disabled ? "block" : "hidden"
        }`}
      >
        {isOpen && items.length === 0 ? (
          <li className="px-4 py-2 text-sm text-gray-500">{emptyHint}</li>
        ) : null}
        {isOpen
          ? items.map((item, index) => (
              <li
                key={item.id}
                {...getItemProps({ item, index })}
                className={`cursor-pointer px-4 py-2 text-sm ${
                  highlightedIndex === index ? "bg-blue-50 text-brand-dark" : "text-gray-800"
                }`}
              >
                <span className="font-medium">{item.label}</span>
                {item.sublabel ? (
                  <span className="block text-xs text-gray-500 mt-0.5">{item.sublabel}</span>
                ) : null}
              </li>
            ))
          : null}
      </ul>
    </div>
  );
}
