import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  sectionId: string;
  sectionLabel?: string;
  children: ReactNode;
};

type State = {
  hasError: boolean;
  message: string;
};

/**
 * Isolates report-section render failures so one heavy pane (Citations, Prompts,
 * etc.) cannot take down the whole report shell. Recovery remounts the subtree.
 */
export class ReportSectionErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: "" };

  static getDerivedStateFromError(error: unknown): State {
    const message =
      error instanceof Error
        ? error.message
        : typeof error === "string"
          ? error
          : "Something went wrong while rendering this section.";
    return { hasError: true, message };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(
      `[ReportSectionErrorBoundary:${this.props.sectionId}]`,
      error,
      info.componentStack,
    );
  }

  private handleRetry = () => {
    this.setState({ hasError: false, message: "" });
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const label = this.props.sectionLabel ?? this.props.sectionId;
    return (
      <div className="max-w-[1200px] mx-auto px-6 py-8" role="alert">
        <div className="rounded-xl border border-red-200 bg-red-50 px-5 py-4">
          <p className="text-sm font-semibold text-red-900">
            Couldn’t load {label}
          </p>
          <p className="mt-1 text-sm text-red-800/90">
            This section hit an error. The rest of the report is still available —
            try again, or switch to another section.
          </p>
          {this.state.message ? (
            <p className="mt-2 text-xs font-mono text-red-700/80 break-words">
              {this.state.message}
            </p>
          ) : null}
          <button
            type="button"
            onClick={this.handleRetry}
            className="mt-4 inline-flex items-center rounded-lg bg-[#0d0d0d] px-3 py-1.5 text-xs font-semibold text-white hover:bg-black/80"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}
