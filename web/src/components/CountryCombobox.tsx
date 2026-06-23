import { useCombobox } from "downshift";
import { useEffect, useMemo, useState } from "react";
import { countryCodeForName, filterCountries } from "../lib/countries";

interface CountryComboboxProps {
  id: string;
  label: string;
  value: string;
  countryCode: string;
  onChange: (countryName: string, isoCode: string) => void;
  help?: string;
}

export function CountryCombobox({
  id,
  label,
  value,
  countryCode,
  onChange,
  help,
}: CountryComboboxProps) {
  const [inputValue, setInputValue] = useState(value);

  useEffect(() => {
    setInputValue(value);
  }, [value]);

  const items = useMemo(
    () => filterCountries(inputValue, 14),
    [inputValue],
  );

  const {
    isOpen,
    getMenuProps,
    getInputProps,
    getItemProps,
    highlightedIndex,
    selectedItem,
    openMenu,
  } = useCombobox({
    items,
    inputValue,
    selectedItem: value || null,
    itemToString: (item) => item ?? "",
    onInputValueChange: ({ inputValue: next }) => {
      setInputValue(next ?? "");
      if (!next?.trim()) onChange("", "");
    },
    onSelectedItemChange: ({ selectedItem: item }) => {
      if (item) {
        onChange(item, countryCodeForName(item));
        setInputValue(item);
      }
    },
  });

  return (
    <div className="mb-4 relative">
      <label htmlFor={id} className="block text-sm font-medium text-brand-dark mb-1.5">
        {label}
      </label>
      {help && <p className="text-sm text-gray-600 mb-2">{help}</p>}
      <input
        className="input-field"
        {...getInputProps({
          id,
          placeholder: "e.g. United Kingdom",
          onFocus: () => openMenu(),
        })}
      />
      {countryCode && selectedItem && (
        <p className="text-xs text-gray-500 mt-1">
          ISO code <strong>{countryCode}</strong> (from your selection)
        </p>
      )}
      <ul
        {...getMenuProps()}
        className={`absolute z-20 mt-1 w-full max-h-56 overflow-auto rounded-lg border border-gray-200 bg-white shadow-lg ${
          isOpen && items.length ? "block" : "hidden"
        }`}
      >
        {isOpen &&
          items.map((item, index) => (
            <li
              key={item}
              {...getItemProps({ item, index })}
              className={`cursor-pointer px-4 py-2 text-sm ${
                highlightedIndex === index ? "bg-blue-50 text-brand-dark" : "text-gray-800"
              }`}
            >
              {item}
            </li>
          ))}
      </ul>
    </div>
  );
}
