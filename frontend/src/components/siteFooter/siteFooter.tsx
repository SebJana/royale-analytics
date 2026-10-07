import { useContext } from "react";
import { Cookie } from "lucide-react";
import { StorageSettingsContext } from "../../contexts/StorageSettingsContext";
import "./siteFooter.css";

/**
 * Footer on every page. Keeps the storage settings one click away, so
 * revoking consent is as easy as giving it.
 */
export function SiteFooter() {
  const openStorageSettings = useContext(StorageSettingsContext);

  return (
    <footer className="site-footer">
      {/* TODO Add links to a privacy policy (Datenschutzerklärung/Privacy Policy, covering
      server logs and IP-based rate limits too + hosting on whatever
      platform it is hosted + possible third party deps (fonts))
      and an Impressum/Contact (§ 5 DDG) before the site goes public. */}
      <button
        type="button"
        className="site-footer-link"
        onClick={openStorageSettings}
      >
        <Cookie aria-hidden="true" />
        Storage settings
      </button>
      {/* NOTE The first two sentences are the notice Supercell's Fan Content
      Policy requires, word for word and in a legible size. Keep them intact. */}
      <p className="site-footer-disclaimer">
        This material is unofficial and is not endorsed by Supercell. For more
        information see Supercell's Fan Content Policy:{" "}
        <a
          href="https://www.supercell.com/fan-content-policy"
          target="_blank"
          rel="noopener noreferrer"
        >
          www.supercell.com/fan-content-policy
        </a>
        . This site is not affiliated with, associated with or connected to
        Supercell or Clash Royale in any way. It is a fan project, made for
        passionate players who want deeper insight into their battles and how
        Clash Royale is played.
      </p>
    </footer>
  );
}
