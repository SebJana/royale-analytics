import { ChevronUp, ChevronDown } from "lucide-react";
import "./sortByContainer.css";

const optionNamesMap = {
  battleCount: "Battles",
  usage: "Battles",
  wins: "Wins",
  winRate: "Win Rate",
  usageRate: "Usage Rate",
  lastSeen: "Last Seen",
};

export function SortByContainer<T extends Record<string, unknown>>({
  options,
  selectedOption,
  ascending,
  disableSort,
  onSelectedOptionChange,
  children,
}: Readonly<{
  options: (keyof T)[];
  selectedOption: keyof T;
  disableSort?: boolean; // Give option to disable the sort by option
  ascending: boolean; // Sort direction
  onSelectedOptionChange: (next: keyof T) => void;
  // Further control groups of the page, e.g. a sort-by-container-group,
  // shown next to the sort
  children?: React.ReactNode;
}>) {
  const handleSortChange = (option: keyof T) => {
    onSelectedOptionChange(option);
  };

  const handleToggleDirection = () => {
    onSelectedOptionChange(selectedOption);
  };

  return (
    <>
      {!disableSort && (
        <div className="sort-by-container">
          <div className="sort-by-container-groups">
            <div className="sort-by-container-group">
              <h2 className="sort-by-container-header">Sort by</h2>
              <div className="sort-by-container-content">
                <select
                  className="sort-by-container-select"
                  value={String(selectedOption)}
                  onChange={(e) => {
                    const value = e.target.value;
                    // Find the matching option from the provided options array
                    const matchingOption = options.find(
                      (opt) => String(opt) === value,
                    );
                    if (matchingOption) {
                      handleSortChange(matchingOption);
                    }
                  }}
                >
                  {options.map((option) => {
                    // Get display name from mapping or fallback to key string
                    const optionKey = String(
                      option,
                    ) as keyof typeof optionNamesMap;
                    const displayName =
                      optionNamesMap[optionKey] || String(option);

                    return (
                      <option key={String(option)} value={String(option)}>
                        {displayName}
                      </option>
                    );
                  })}
                </select>
                <button
                  onClick={handleToggleDirection}
                  className={`sort-by-container-direction-button ${
                    ascending ? "ascending" : "descending"
                  }`}
                >
                  {ascending ? (
                    <ChevronUp size={24} />
                  ) : (
                    <ChevronDown size={24} />
                  )}
                </button>
              </div>
            </div>
            {children}
          </div>
        </div>
      )}
    </>
  );
}
