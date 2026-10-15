/** Outcome of an action, shown by StatusMessage. */
export type Status = {
  // "wait" for failures that pass on their own (rate limits, outages)
  tone: "success" | "error" | "wait";
  title: string;
  message: string;
  /** Follow-up action, shown as a button under the message. */
  link?: { to: string; label: string };
};
