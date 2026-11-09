// Сцена звонка, общая для учителя и ученика

const { Track } = window.LivekitClient;

const TILE_RATIO = { mobile: 4 / 3, desktop: 16 / 9 };
const TILE_MIN_W = { mobile: 150, desktop: 260 };
const TILE_MAX_W = 960;
const GAP = 8;
const MAX_TILES = 16;

export const mobile = matchMedia("(max-width: 700px)");

export function tileRatio() {
  return mobile.matches ? TILE_RATIO.mobile : TILE_RATIO.desktop;
}

export function layout(n, width, height) {
  const ratio = tileRatio();
  const minW = Math.min(mobile.matches ? TILE_MIN_W.mobile : TILE_MIN_W.desktop, width);
  const span = (total, k) => (total - (k - 1) * GAP) / k;
  const maxCols = Math.max(1, Math.floor((width + GAP) / (minW + GAP)));
  let best = null;
  if (n <= MAX_TILES) {
    for (let cols = 1; cols <= Math.min(n, maxCols); cols++) {
      const w = Math.min(span(width, cols), span(height, Math.ceil(n / cols)) * ratio, TILE_MAX_W);
      if (w >= minW && (!best || w > best.w)) best = { cols, w, size: n };
    }
  }
  if (best || n === 0) return best ?? { cols: 1, w: 0, size: 1 };
  const w = Math.max(0, Math.min(span(width, maxCols), height * ratio, TILE_MAX_W));
  const rows = Math.max(1, Math.floor((height + GAP) / (w / ratio + GAP)));
  return { cols: maxCols, w, size: Math.min(MAX_TILES, maxCols * rows) };
}

export function createStage({ tile, name, redraw }) {
  const $ = (id) => document.getElementById(id);
  const videos = new Map();
  let page = 1;

  function video(pub) {
    const track = pub?.track;
    if (!track || pub.isMuted) return null;
    if (!videos.has(track.sid)) videos.set(track.sid, track.attach());
    return videos.get(track.sid);
  }

  function dot(on) {
    const el = document.createElement("span");
    if (on) el.className = "on";
    return el;
  }

  function moreTile(n) {
    const el = document.createElement("div");
    el.className = "tile more-tile";
    const inner = document.createElement("div");
    inner.append(`+${n}`, Object.assign(document.createElement("small"), { textContent: "ещё в звонке" }));
    el.append(inner);
    return el;
  }

  function render(all, { self, reconnecting = false }) {
    const live = new Set();
    for (const p of all) for (const pub of p.trackPublications.values()) if (pub.track) live.add(pub.track.sid);
    for (const sid of videos.keys()) if (!live.has(sid)) videos.delete(sid);

    const sharer = all.find((p) => p.getTrackPublication(Track.Source.ScreenShare)?.track);
    $("share").hidden = !sharer;
    $("tiles").hidden = Boolean(sharer);
    $("stage").classList.toggle("dim", reconnecting);
    $("banner").hidden = !reconnecting;

    if (sharer) {
      const screen = video(sharer.getTrackPublication(Track.Source.ScreenShare));
      $("shared").replaceChildren(...(screen ? [screen] : []), $("sharedBy"));
      $("sharedBy").textContent = sharer === self ? "Вы показываете экран" : `${name(sharer)} показывает экран`;
      const strip = mobile.matches || all.length <= 4
        ? all.map(tile)
        : [...all.slice(0, 3).map(tile), moreTile(all.length - 3)];
      $("strip").replaceChildren(...strip);
      $("pager").hidden = $("prev").hidden = $("next").hidden = true;
      return;
    }
    const tiles = $("tiles");
    const { size, cols: maxCols, w } = layout(all.length, tiles.clientWidth, tiles.clientHeight);
    const pages = Math.max(1, Math.ceil(all.length / size));
    page = Math.min(page, pages);
    const visible = all.slice((page - 1) * size, page * size);
    const cols = Math.max(1, Math.min(maxCols, visible.length));
    tiles.style.gridTemplateColumns = `repeat(${cols}, ${w}px)`;
    tiles.style.gridTemplateRows = `repeat(${Math.ceil(visible.length / cols)}, ${w / tileRatio()}px)`;
    tiles.replaceChildren(...visible.map((p, i) => tile(p, (page - 1) * size + i)));
    $("pager").hidden = pages === 1;
    $("pager").replaceChildren(...Array.from({ length: pages }, (_, i) => dot(i + 1 === page)));
    $("prev").hidden = $("next").hidden = pages === 1;
    $("prev").disabled = page === 1;
    $("next").disabled = page === pages;
  }

  function go(next) {
    const pages = $("pager").children.length || 1;
    if (next >= 1 && next <= pages) { page = next; redraw(); }
  }

  $("prev").onclick = () => go(page - 1);
  $("next").onclick = () => go(page + 1);

  let lastBox = "";
  new ResizeObserver(([e]) => {
    const key = `${Math.round(e.contentRect.width)}x${Math.round(e.contentRect.height)}`;
    if (key !== lastBox) { lastBox = key; redraw(); }
  }).observe($("tiles"));

  let swipeX = null;
  $("tiles").addEventListener("pointerdown", (e) => { swipeX = e.clientX; });
  $("tiles").addEventListener("pointerup", (e) => {
    if (swipeX === null) return;
    const dx = e.clientX - swipeX;
    swipeX = null;
    if (Math.abs(dx) >= 50) go(page + (dx < 0 ? 1 : -1));
  });

  return { render, video };
}
