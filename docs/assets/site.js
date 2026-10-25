(() => {
  "use strict";
  const root = document.documentElement;
  const preferenceKey = "coimbra-reading-style-v1";
  const progressKey = "coimbra-reading-chapter-v1";
  const readStored = (key) => {
    try { return localStorage.getItem(key); } catch { return null; }
  };
  const writeStored = (key, value) => {
    try { localStorage.setItem(key, value); } catch { return; }
  };
  let saved = {};
  try { saved = JSON.parse(readStored(preferenceKey) || "{}"); } catch { saved = {}; }
  if (!saved || typeof saved !== "object") saved = {};
  const style = {
    theme: saved.theme === "dark" ? "dark" : "light",
    size: Number.isFinite(saved.size) ? Math.min(30, Math.max(17, saved.size)) : 21
  };
  const applyStyle = () => {
    root.dataset.theme = style.theme;
    root.style.setProperty("--reader-size", `${style.size}px`);
    for (const button of document.querySelectorAll('[data-action="theme"]')) {
      button.textContent = style.theme === "dark" ? "日间" : "夜间";
      button.setAttribute("aria-label", style.theme === "dark" ? "切换到日间阅读" : "切换到夜间阅读");
    }
  };
  applyStyle();
  for (const button of document.querySelectorAll("[data-action]")) {
    button.addEventListener("click", () => {
      if (button.dataset.action === "theme") style.theme = style.theme === "dark" ? "light" : "dark";
      if (button.dataset.action === "increase-font") style.size = Math.min(30, style.size + 1);
      if (button.dataset.action === "decrease-font") style.size = Math.max(17, style.size - 1);
      applyStyle();
      writeStored(preferenceKey, JSON.stringify(style));
    });
  }
  const chapterCount = Number(document.body.dataset.chapterCount);
  const current = Number(document.body.dataset.chapter);
  if (Number.isInteger(current) && current >= 1 && current <= chapterCount) writeStored(progressKey, String(current));
  const previous = Number(readStored(progressKey));
  const resume = document.querySelector("[data-resume]");
  if (resume && Number.isInteger(previous) && previous >= 1 && previous <= chapterCount) {
    resume.href = `chapters/${String(previous).padStart(3, "0")}.html`;
    resume.textContent = `继续阅读 · 第${String(previous).padStart(3, "0")}章`;
    resume.hidden = false;
  }
  const expandVolume = () => {
    if (/^#volume-\d+$/.test(location.hash)) {
      const volume = document.getElementById(location.hash.slice(1));
      if (volume) volume.open = true;
    }
  };
  expandVolume();
  window.addEventListener("hashchange", expandVolume);
  const search = document.querySelector("[data-search]");
  const initialSearchStatus = document.querySelector("[data-search-status]")?.textContent || "";
  if (search) {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLocaleLowerCase();
      let matches = 0;
      for (const volume of document.querySelectorAll("[data-volume]")) {
        let volumeMatches = 0;
        for (const item of volume.querySelectorAll("[data-chapter-item]")) {
          const match = item.textContent.toLocaleLowerCase().includes(query);
          item.hidden = !match;
          if (match) volumeMatches += 1;
        }
        volume.hidden = volumeMatches === 0;
        if (query && volumeMatches) volume.open = true;
        matches += volumeMatches;
      }
      const status = document.querySelector("[data-search-status]");
      if (status) status.textContent = query ? `找到 ${matches} 章` : initialSearchStatus;
    });
  }
})();
