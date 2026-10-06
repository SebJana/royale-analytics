import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { copyToClipboard } from "../../utils/clipboard";
import "./copyableTag.css";

// How long the check mark confirms a copy
const COPIED_FEEDBACK_MS = 1500;

/**
 * A player tag that copies itself to the clipboard on click.
 *
 * @param tag - The player tag, e.g. "#YYRJQY28"
 * @param className - Extra classes for the text styling of the caller
 */
export function CopyableTag({
  tag,
  className,
}: Readonly<{ tag: string; className?: string }>) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timeout = setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
    return () => clearTimeout(timeout);
  }, [copied]);

  const handleCopy = async () => {
    if (await copyToClipboard(tag)) setCopied(true);
  };

  return (
    <button
      type="button"
      className={`copyable-tag${className ? ` ${className}` : ""}`}
      onClick={handleCopy}
      title={copied ? "Copied" : "Copy tag"}
    >
      {tag}
      {copied ? (
        <Check className="copyable-tag-icon is-copied" aria-hidden="true" />
      ) : (
        <Copy className="copyable-tag-icon" aria-hidden="true" />
      )}
      {/* Announces the copy to screen readers, which do not see the icon */}
      <span className="copyable-tag-status" aria-live="polite">
        {copied ? "Copied" : ""}
      </span>
    </button>
  );
}
