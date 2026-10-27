import { useEffect, useState } from "react";
import { ChevronUp } from "lucide-react";
import "./scrollToTop.css";

// Scrolling a page without a hiding panel needs before the button shows. In
// screen heights: a fixed pixel distance is a different amount of scrolling
// on a phone than on a wide desktop.
const MIN_SCROLL_SCREENS = 2;

export function ScrollToTopButton({
  onScrollStart,
}: Readonly<{ onScrollStart?: () => void }>) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const toggleVisible = () => {
      // A panel that keeps a bar at the screen's bottom (the filters' sticky
      // Apply row) shares that corner. It is looked up on every scroll, since
      // it can mount after this button, e.g. once a page has loaded.
      const blocker = document.querySelector("[data-hides-scroll-to-top]");
      if (blocker) {
        // Once the panel's end has left the screen at the top, its bar can no
        // longer cover the button
        setVisible(blocker.getBoundingClientRect().bottom < 0);
        return;
      }
      setVisible(window.scrollY > MIN_SCROLL_SCREENS * window.innerHeight);
    };

    toggleVisible();
    // A resize changes the screen height and can reflow the panel, e.g.
    // when a phone's browser toolbar collapses or the device rotates
    window.addEventListener("scroll", toggleVisible);
    window.addEventListener("resize", toggleVisible);
    return () => {
      window.removeEventListener("scroll", toggleVisible);
      window.removeEventListener("resize", toggleVisible);
    };
  }, []);

  const scrollToTop = () => {
    onScrollStart?.();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <button
      onClick={scrollToTop}
      className={`scroll-to-top-button ${visible ? "visible" : ""}`}
    >
      <ChevronUp size={30} />
    </button>
  );
}
