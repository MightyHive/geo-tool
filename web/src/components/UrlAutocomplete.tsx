import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import { autocomplete, type AutocompleteApi } from "@algolia/autocomplete-js";
import "@algolia/autocomplete-theme-classic/dist/theme.css";
import { fetchDomainSuggest } from "../api/client";

const MIN_QUERY_LENGTH = 3;
const PROMPT_MESSAGE = "Searching for matching sites...";

function normalizeTypedWebsite(raw: string): string {
  let s = raw.trim();
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = `https://${s}`;
  return s;
}

function looksLikeWebsiteQuery(raw: string): boolean {
  const t = raw.trim();
  if (t.length < 4) return false;
  return t.includes(".") || /^https?:\/\//i.test(t);
}

type DomainItem = {
  label: string;
  url: string;
  isPrompt?: boolean;
};

export interface UrlAutocompleteHandle {
  /** Current text in the search box (typed or selected). */
  getQuery: () => string;
}

interface UrlAutocompleteProps {
  id: string;
  label: string;
  value: string;
  onChange: (url: string) => void;
  placeholder?: string;
  help?: string;
}

export const UrlAutocomplete = forwardRef<UrlAutocompleteHandle, UrlAutocompleteProps>(
  function UrlAutocomplete(
    { id, label, value, onChange, placeholder = "Type to search popular sites…", help },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const apiRef = useRef<AutocompleteApi<DomainItem> | null>(null);
    const queryRef = useRef(value);
    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;

    useImperativeHandle(ref, () => ({
      getQuery: () => queryRef.current,
    }));

    useEffect(() => {
      const el = containerRef.current;
      if (!el) return;

      const api = autocomplete<DomainItem>({
        container: el,
        placeholder,
        openOnFocus: true,
        initialState: { query: value },
        onReset() {
          queryRef.current = "";
          onChangeRef.current("");
        },
        onSubmit({ state }) {
          const q = (state.query || "").trim();
          queryRef.current = q;
          if (q) onChangeRef.current(q);
        },
        onStateChange({ state, prevState }) {
          if (state.query !== prevState.query) {
            queryRef.current = state.query;
            onChangeRef.current(state.query);
          }
        },
        getSources({ query }) {
          const q = query.trim();

          if (q.length < MIN_QUERY_LENGTH) {
            return [
              {
                sourceId: "prompt",
                getItems() {
                  return [
                    {
                      label: PROMPT_MESSAGE,
                      url: "",
                      isPrompt: true,
                    },
                  ];
                },
                onSelect() {
                  /* non-selectable hint */
                },
                templates: {
                  item({ item, html }) {
                    return html`<div class="aa-PromptMessage" style="padding:0.5rem 1rem;color:#6b7280;font-size:0.875rem;">
                      ${item.label}
                    </div>`;
                  },
                },
              },
            ];
          }

          return [
            {
              sourceId: "domains",
              async getItems() {
                const rows = await fetchDomainSuggest(q);
                const items = rows.map((r) => ({ label: r.label, url: r.url }));
                const typed = q.trim();
                if (looksLikeWebsiteQuery(typed)) {
                  const customUrl = normalizeTypedWebsite(typed);
                  const norm = customUrl.replace(/\/$/, "");
                  const dup = items.some((i) => i.url.replace(/\/$/, "") === norm);
                  if (customUrl && !dup) {
                    items.unshift({
                      label: `Use “${typed}”`,
                      url: customUrl,
                    });
                  }
                }
                return items;
              },
              onSelect({ item }) {
                if (item?.url && !item.isPrompt) {
                  queryRef.current = item.url;
                  onChangeRef.current(item.url);
                  apiRef.current?.setQuery(item.url);
                }
              },
              templates: {
                item({ item, html }) {
                  return html`<div>
                    <strong>${item.label}</strong>
                    <div style="font-size:0.75rem;color:#6b7280">${item.url}</div>
                  </div>`;
                },
              },
            },
          ];
        },
      });

      apiRef.current = api;
      return () => {
        api.destroy();
        apiRef.current = null;
      };
    }, [placeholder]);

    useEffect(() => {
      queryRef.current = value;
      apiRef.current?.setQuery(value);
    }, [value]);

    return (
      <div className="url-autocomplete-field mb-4">
        <label htmlFor={id} className="block text-sm font-medium text-brand-dark mb-1.5">
          {label}
        </label>
        {help && <p className="text-sm text-gray-600 mb-2">{help}</p>}
        <div ref={containerRef} className="url-autocomplete-container" />
      </div>
    );
  },
);
