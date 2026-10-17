import { onBeforeUnmount, onMounted, ref } from "vue";

// Breakpoints of the responsive shell (see src/styles/theme.css):
//   mobile  < 768px
//   tablet  768px - 1199px
//   desktop >= 1200px
const MOBILE = "(max-width: 767.98px)";
const TABLET = "(min-width: 768px) and (max-width: 1199.98px)";
const DESKTOP = "(min-width: 1200px)";

/**
 * Reactive media-query state shared by the shell and the views.
 *
 * Initial values are read synchronously during setup so the first render is
 * already correct (no flash of a desktop table on a phone); the change
 * listeners only keep it in sync afterwards.
 */
export function useBreakpoint() {
  const query = (mq) => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(mq) : null);

  const mobile = query(MOBILE);
  const tablet = query(TABLET);
  const desktop = query(DESKTOP);

  const isMobile = ref(Boolean(mobile?.matches));
  const isTablet = ref(Boolean(tablet?.matches));
  const isDesktop = ref(Boolean(desktop?.matches));

  const sync = () => {
    isMobile.value = Boolean(mobile?.matches);
    isTablet.value = Boolean(tablet?.matches);
    isDesktop.value = Boolean(desktop?.matches);
  };

  const media = [mobile, tablet, desktop].filter(Boolean);

  onMounted(() => media.forEach((item) => item.addEventListener("change", sync)));
  onBeforeUnmount(() => media.forEach((item) => item.removeEventListener("change", sync)));

  return { isMobile, isTablet, isDesktop };
}
