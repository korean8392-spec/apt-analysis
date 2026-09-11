const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const statusEl = $("#status");
const resultEl = $("#result");
const keyWarningEl = $("#key-warning");
const landingHeroEl = $("#landing-hero");

let currentData = null;

function fmtWon(v10k) {
  if (v10k === null || v10k === undefined || Number.isNaN(v10k)) return "-";
  const eok = Math.floor(v10k / 10000);
  const man = Math.round(v10k % 10000);
  if (eok > 0) return `${eok}억 ${man.toLocaleString()}만원`;
  return `${man.toLocaleString()}만원`;
}

// 서울/경기 공통 법정 상한요율 (2021년 개정 기준, 주택 매매)
function brokerFee(price10k) {
  const price = price10k * 10000;
  let rate, cap;
  if (price < 50000000) { rate = 0.006; cap = 250000; }
  else if (price < 200000000) { rate = 0.005; cap = 800000; }
  else if (price < 900000000) { rate = 0.004; cap = null; }
  else if (price < 1200000000) { rate = 0.005; cap = null; }
  else if (price < 1500000000) { rate = 0.006; cap = null; }
  else { rate = 0.007; cap = null; }
  let fee = price * rate;
  if (cap !== null) fee = Math.min(fee, cap);
  return { rate, fee: Math.round(fee), vat: Math.round(fee * 0.1) };
}

// 1주택자 유상취득 기준 취득세(+지방교육세+농특세)
function acquisitionTax(price10k, exclusiveArea) {
  const price = price10k * 10000;
  const priceEok = price / 100000000;
  let taxRate;
  if (priceEok <= 6) taxRate = 1.0;
  else if (priceEok <= 9) taxRate = 1 + ((priceEok - 6) / 3) * 2;
  else taxRate = 3.0;

  const eduTaxRate = taxRate * 0.1;
  const ruralTaxRate = exclusiveArea && exclusiveArea > 85 ? 0.2 : 0;
  const totalRate = taxRate + eduTaxRate + ruralTaxRate;

  return {
    taxRate,
    eduTaxRate,
    ruralTaxRate,
    totalRate,
    acquisitionTax: Math.round((price * taxRate) / 100),
    eduTax: Math.round((price * eduTaxRate) / 100),
    ruralTax: Math.round((price * ruralTaxRate) / 100),
    total: Math.round((price * totalRate) / 100),
  };
}

function judgementInfo(v, askMin, askMax, householdCnt) {
  const fairPrice10k = v.fair_price_10k;
  if (!fairPrice10k || (!askMin && !askMax)) return null;
  const ask = askMin && askMax ? (askMin + askMax) / 2 : askMin || askMax;
  const diffPct = ((fairPrice10k - ask) / fairPrice10k) * 100;

  let cls, headline;
  if (diffPct >= 5) {
    cls = "hot-deal";
    headline = `추정 적정가보다 약 ${diffPct.toFixed(1)}% 낮은 가격 — 데이터상 급매에 가깝습니다.`;
  } else if (diffPct >= -3) {
    cls = "fair";
    const sign = diffPct >= 0 ? "-" : "+";
    headline = `추정 적정가와 비슷한 수준(${sign}${Math.abs(diffPct).toFixed(1)}%) — 무리한 고가도 눈에 띄는 저가도 아닙니다.`;
  } else {
    cls = "high";
    headline = `추정 적정가보다 약 ${Math.abs(diffPct).toFixed(1)}% 높은 가격 — 데이터상 협상 여지를 검토해볼 만합니다.`;
  }

  // 판단의 근거가 된 구체적인 수치를 문장으로 풀어, 왜 이런 결론이 나왔는지 알 수 있게 한다.
  const reasons = [];
  reasons.push(`추정 적정가 ${fmtWon(fairPrice10k)} · 입력 호가 ${fmtWon(ask)}`);

  const confLabel = { high: "표본이 충분해 신뢰도가 높은", medium: "표본이 보통 수준인", low: "표본이 적어 신뢰도가 낮은" }[
    v.confidence
  ];
  if (confLabel) {
    reasons.push(`최근 1년 ${v.sample_count_1y}건 · 3년 ${v.sample_count}건의 실거래로 산출한, ${confLabel} 추정치입니다`);
  }

  if (householdCnt) {
    const liquidityLabel =
      householdCnt >= 1000 ? "대단지라 거래가 비교적 활발한 편입니다" : householdCnt >= 300 ? "중형 단지입니다" : "소규모 단지라 거래량이 적을 수 있습니다";
    reasons.push(`총 ${householdCnt.toLocaleString()}세대 규모로, ${liquidityLabel}`);
  }

  if (v.peak_price_10k) {
    const gapPeak = ((ask - v.peak_price_10k) / v.peak_price_10k) * 100;
    reasons.push(
      ask >= v.peak_price_10k
        ? `입력 호가가 최근 3년 전고점(${fmtWon(v.peak_price_10k)}) 이상입니다`
        : `최근 3년 전고점(${fmtWon(v.peak_price_10k)}) 대비 약 ${gapPeak.toFixed(1)}% 낮은 가격입니다`
    );
  }

  if (v.jeonse_ratio_pct != null) {
    const gapLabel = v.jeonse_ratio_pct < 50 ? "매매가 대비 갭이 커 투자 목적이라면 자기자본 부담이 큰 편입니다" : "매매가 대비 갭이 상대적으로 작은 편입니다";
    reasons.push(`전세가율 약 ${v.jeonse_ratio_pct}%로 ${gapLabel}`);
  }

  return { cls, headline, reasons };
}

const VERDICT_LABELS = {
  "hot-deal": "🟢 매수 긍정적 (급매 가능성)",
  fair: "🔵 적정가 근접",
  high: "🟡 신중 검토 (고평가 가능성)",
};

function renderQuickCheck(data) {
  const select = $("#quick-check-pyeong");
  const minInput = $("#quick-check-min");
  const maxInput = $("#quick-check-max");
  const resultBox = $("#quick-check-result");

  if (!data.valuations.length) {
    select.innerHTML = "";
    resultBox.innerHTML = `<p class="hint" style="margin:0">이 단지는 평형 데이터가 없어 판단할 수 없습니다.</p>`;
    return;
  }

  const pyeongLabelFor = (v) => `${v.pyeong}평 (전용 ${v.avg_exclusive_area}㎡)`;

  select.innerHTML = data.valuations
    .map((v, i) => `<option value="${i}">${pyeongLabelFor(v)}</option>`)
    .join("");

  function computeVerdict() {
    const v = data.valuations[select.value];
    if (!v) return;
    const askMin = numOrNull(minInput.value);
    const askMax = numOrNull(maxInput.value);
    if (!askMin && !askMax) {
      resultBox.innerHTML = `<p class="hint" style="margin:0">호가를 입력하면 매수 판단이 여기 표시됩니다.</p>`;
      return;
    }
    const householdCnt = data.manual_complex_info?.household_cnt ?? data.basis_info?.household_cnt;
    const info = judgementInfo(v, askMin, askMax, householdCnt);
    if (!info) {
      resultBox.innerHTML = `<p class="hint" style="margin:0">이 평형은 적정가 추정치가 부족해 판단하기 어렵습니다.</p>`;
      return;
    }
    resultBox.innerHTML = `
      <div class="verdict-banner ${info.cls}">${VERDICT_LABELS[info.cls]}<br /><span style="font-weight:400">${info.headline}</span></div>
      <ul class="verdict-reasons">${info.reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
    `;
  }

  function loadFromManual() {
    const v = data.valuations[select.value];
    const label = pyeongLabelFor(v);
    const existing = (data.manual_listings || []).find((m) => m.pyeong_label === label);
    minInput.value = existing?.ask_price_min ?? "";
    maxInput.value = existing?.ask_price_max ?? "";
    computeVerdict();
  }

  async function saveQuickCheck() {
    const v = data.valuations[select.value];
    if (!v) return;
    const label = pyeongLabelFor(v);
    const body = {
      complex_key: data.complex_key,
      pyeong_label: label,
      exclusive_area: v.avg_exclusive_area,
      ask_price_min: numOrNull(minInput.value),
      ask_price_max: numOrNull(maxInput.value),
    };
    await fetch("/api/manual-listings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    data.manual_listings = data.manual_listings || [];
    const idx = data.manual_listings.findIndex((m) => m.pyeong_label === label);
    if (idx >= 0) data.manual_listings[idx] = { ...data.manual_listings[idx], ...body };
    else data.manual_listings.push(body);
  }

  select.onchange = loadFromManual;
  minInput.oninput = computeVerdict;
  maxInput.oninput = computeVerdict;
  minInput.onblur = saveQuickCheck;
  maxInput.onblur = saveQuickCheck;

  loadFromManual();
}

function buildCalcNote(v) {
  const method = v.used_trend_regression
    ? "최근 3년 실거래가에 최근 거래일수록 더 큰 가중치(반감기 180일 지수감쇠)를 주는 가중회귀직선을 적합해 오늘 시점 추세가를 추정"
    : "표본이 3건 미만이라 회귀분석 대신 최근일수록 가중치를 더 주는 가중평균으로 추정";
  return `산출 근거: 이 평형의 최근 3년 실거래 ${v.sample_count}건(최근 1년 ${v.sample_count_1y}건)을 바탕으로, ${method}했습니다. 통계적 참고치이며 투자 자문이 아닙니다.`;
}

function bandBoxContent(v) {
  const band = v.price_band_10k;
  const fair = v.fair_price_10k;
  if (!band || !fair) return `<div class="value">-</div>`;

  const p25Pct = ((band.p25 - fair) / fair) * 100;
  const p75Pct = ((band.p75 - fair) / fair) * 100;
  const fmtPct = (p) => (p >= 0 ? `+${p.toFixed(1)}%` : `${p.toFixed(1)}%`);

  const span = band.p75 - band.p25;
  const markerPct = span > 0 ? Math.min(100, Math.max(0, ((fair - band.p25) / span) * 100)) : 50;

  return `
    <div class="value">${fmtPct(p25Pct)} ~ ${fmtPct(p75Pct)}</div>
    <div class="band-sub">${fmtWon(band.p25)} ~ ${fmtWon(band.p75)}</div>
    <div class="band-bar">
      <div class="band-marker" style="left:${markerPct}%" title="추정 적정가 위치"></div>
    </div>
  `;
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    keyWarningEl.hidden = !!data.molit_key_configured;
  } catch (e) {
    // 서버 자체가 안 뜬 경우는 검색 시 에러로 알림
  }
}

const candidateSection = $("#candidate-section");
const candidateListEl = $("#candidate-list");

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sigungu = $("#sigungu").value.trim();
  const aptName = $("#apt-name").value.trim();
  if (!sigungu || !aptName) return;

  candidateSection.hidden = true;
  resultEl.hidden = true;
  landingHeroEl.hidden = true;
  statusEl.textContent = "단지 찾는 중...";

  try {
    const url = `/api/find-complex?sigungu=${encodeURIComponent(sigungu)}&apt_name=${encodeURIComponent(aptName)}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      statusEl.innerHTML = `<span style="color:#dc2626">${data.detail || "조회 실패"}</span>`;
      return;
    }
    statusEl.textContent = "";
    if (!data.candidates.length) {
      // 공식 단지목록에 없는 경우 실거래가 이름 매칭만으로 바로 조회를 시도한다.
      await runSearch(sigungu, aptName, null);
      return;
    }
    renderCandidates(sigungu, aptName, data.candidates);
  } catch (err) {
    statusEl.innerHTML = `<span style="color:#dc2626">네트워크 오류: ${err}</span>`;
  }
});

function renderCandidates(sigungu, aptName, candidates) {
  candidateSection.hidden = false;
  candidateListEl.innerHTML = "";
  for (const c of candidates) {
    const item = document.createElement("div");
    item.className = "candidate-item";
    item.innerHTML = `
      <div class="info">
        <div class="name">${c.name}</div>
        <div class="address">${c.address || "-"}</div>
      </div>
      <button type="button">이 단지 맞음</button>
    `;
    item.querySelector("button").addEventListener("click", () => {
      candidateSection.hidden = true;
      runSearch(sigungu, aptName, c.kapt_code);
    });
    candidateListEl.appendChild(item);
  }
}

async function runSearch(sigungu, aptName, kaptCode) {
  statusEl.textContent = "조회 중... (국토부 API에서 최근 36개월 실거래가를 가져오는 중이라 몇 초 걸릴 수 있어요)";
  resultEl.hidden = true;

  try {
    let url = `/api/search?sigungu=${encodeURIComponent(sigungu)}&apt_name=${encodeURIComponent(aptName)}`;
    if (kaptCode) url += `&kapt_code=${encodeURIComponent(kaptCode)}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      statusEl.innerHTML = `<span style="color:#dc2626">${data.detail || "조회 실패"}</span>`;
      return;
    }
    statusEl.textContent = "";
    renderResult(data);
  } catch (err) {
    statusEl.innerHTML = `<span style="color:#dc2626">네트워크 오류: ${err}</span>`;
  }
}

function renderResult(data) {
  resultEl.hidden = false;
  currentData = data;
  $("#complex-title").textContent = `${data.sigungu.sido} ${data.sigungu.sigungu} · ${data.matched_apt_name}`;
  renderQuickCheck(data);

  const summaryCard = $("#summary-card");
  if (data.summary_text) {
    summaryCard.hidden = false;
    $("#summary-text").textContent = data.summary_text;
  } else {
    summaryCard.hidden = true;
  }

  const info = data.official_info;
  const basis = data.basis_info;
  const building = data.building_info;
  const manual = data.manual_complex_info;
  const grid = $("#official-info-grid");
  grid.innerHTML = "";

  const subway = data.subway_info;
  const subwayLabel = subway
    ? `${subway.station_name}${subway.lines?.length ? ` (${subway.lines.join(", ")})` : ""} · 도보 약 ${subway.walk_minutes}분`
    : "인근 2km 내 역 없음";

  const school = data.school_info;
  const schoolNote = ' <span style="font-weight:400;color:var(--muted);font-size:11px">(배정 학교 아님, 최단거리 기준)</span>';
  const schoolLabel = (s) => (s ? `${s.name} · 도보 약 ${Math.round(s.distance_m / 67)}분${schoolNote}` : "-");

  const items = [
    ["주소", basis?.address || info?.address || "-"],
    ["인근 지하철", subwayLabel],
    ["가까운 초등학교", schoolLabel(school?.elementary)],
    ["가까운 중학교", schoolLabel(school?.middle)],
    ["세대수", manual?.household_cnt ?? basis?.household_cnt ?? "-"],
    ["동수", basis?.dong_cnt ?? "-"],
    ["최고층", basis?.top_floor ?? "-"],
    ["사용승인일", basis?.use_approval_date ?? (manual?.use_approval_year ? `${manual.use_approval_year}년` : "-")],
    ["난방방식", basis?.heat_type ?? "-"],
    ["건폐율(%)", manual?.building_coverage_ratio ?? building?.building_coverage_ratio ?? "직접입력 필요"],
    ["용적률(%)", manual?.floor_area_ratio ?? building?.floor_area_ratio ?? "직접입력 필요"],
    ["총 주차대수", building?.total_parking_cnt ?? "-"],
    ["실거래 표본 수(36개월)", data.trade_sample_total],
  ];
  for (const msg of [data.basis_info_error, data.building_info_error]) {
    if (!msg) continue;
    const warn = document.createElement("div");
    warn.className = "banner warning";
    warn.style.gridColumn = "1 / -1";
    warn.textContent = msg;
    grid.appendChild(warn);
  }
  for (const [label, value] of items) {
    const div = document.createElement("div");
    div.className = "info-item";
    div.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
    grid.appendChild(div);
  }

  renderMap(data);
  renderCommercialDensity(data.commercial_info, data.geocode);

  const manualForm = $("#manual-complex-form");
  manualForm.building_coverage_ratio.value =
    manual?.building_coverage_ratio ?? building?.building_coverage_ratio ?? "";
  manualForm.floor_area_ratio.value =
    manual?.floor_area_ratio ?? building?.floor_area_ratio ?? "";
  manualForm.household_cnt.value = manual?.household_cnt ?? "";
  manualForm.use_approval_year.value = manual?.use_approval_year ?? "";
  manualForm.onsubmit = async (e) => {
    e.preventDefault();
    const body = {
      complex_key: data.complex_key,
      building_coverage_ratio: numOrNull(manualForm.building_coverage_ratio.value),
      floor_area_ratio: numOrNull(manualForm.floor_area_ratio.value),
      household_cnt: numOrNull(manualForm.household_cnt.value),
      use_approval_year: numOrNull(manualForm.use_approval_year.value),
    };
    await fetch("/api/manual-complex-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    manualForm.closest("details").open = false;
  };

  renderPyeongList(data);
}

let kakaoMapReadyPromise = null;
function ensureKakaoMapLoaded() {
  if (kakaoMapReadyPromise) return kakaoMapReadyPromise;
  kakaoMapReadyPromise = new Promise((resolve) => {
    if (window.kakao && window.kakao.maps) {
      window.kakao.maps.load(resolve);
    } else {
      resolve(); // SDK 로드 실패(차단/도메인 미등록 등) — 호출부에서 kakao 존재 여부로 다시 확인
    }
  });
  return kakaoMapReadyPromise;
}

let kakaoMap = null;
let kakaoMarker = null;

async function renderMap(data) {
  const mapEl = $("#map");
  const noteEl = $("#map-note");
  const geo = data.geocode;

  if (!geo) {
    mapEl.hidden = true;
    noteEl.hidden = false;
    noteEl.textContent = data.geocode_query
      ? `지도에 표시할 위치를 찾지 못했습니다 (검색 주소: ${data.geocode_query}).`
      : "지도에 표시할 주소 정보가 없습니다.";
    return;
  }

  await ensureKakaoMapLoaded();

  if (!window.kakao || !window.kakao.maps) {
    mapEl.hidden = true;
    noteEl.hidden = false;
    noteEl.textContent = "카카오맵을 불러오지 못했습니다. 네트워크 상태를 확인해주세요.";
    return;
  }

  mapEl.hidden = false;
  noteEl.hidden = true;

  const { lat, lon } = geo;
  const position = new kakao.maps.LatLng(lat, lon);

  try {
    if (!kakaoMap) {
      kakaoMap = new kakao.maps.Map(mapEl, { center: position, level: 3 });
      kakaoMarker = new kakao.maps.Marker({ position, map: kakaoMap });
    } else {
      kakaoMap.setCenter(position);
      kakaoMarker.setPosition(position);
      kakao.maps.event.trigger(kakaoMap, "resize");
    }
    const infowindow = new kakao.maps.InfoWindow({
      content: `<div style="padding:6px 10px;font-size:12px;white-space:nowrap;">${data.matched_apt_name}</div>`,
    });
    infowindow.open(kakaoMap, kakaoMarker);
  } catch (e) {
    mapEl.hidden = true;
    noteEl.hidden = false;
    noteEl.textContent = "카카오맵 표시 중 오류가 발생했습니다. 앱의 플랫폼 도메인 등록을 확인해주세요.";
  }
}

let commercialCircle = null;

function renderCommercialDensity(info, geo) {
  const el = $("#commercial-density");
  const detailsEl = $("#commercial-density-details");
  if (!el) return;

  if (commercialCircle) {
    commercialCircle.setMap(null);
    commercialCircle = null;
  }
  if (detailsEl) detailsEl.ontoggle = null;

  if (!info) {
    el.innerHTML = `<p class="hint" style="margin:0">조회된 상권 정보가 없습니다.</p>`;
    return;
  }

  el.innerHTML = `
    <div class="cc-meta">
      <div><div class="label">음식점</div><div class="value">${info.restaurant}곳</div></div>
      <div><div class="label">카페</div><div class="value">${info.cafe}곳</div></div>
      <div><div class="label">편의점</div><div class="value">${info.convenience}곳</div></div>
    </div>
  `;

  // 지도에는 기본으로 안 그리고, 이 섹션을 펼쳤을 때만 반경 원을 보여준다 —
  // 지도를 평소엔 깔끔하게 유지하되(사용자 요청), 밀집도가 지도에도 반영되게 한다.
  if (detailsEl) {
    detailsEl.ontoggle = () => {
      if (commercialCircle) {
        commercialCircle.setMap(null);
        commercialCircle = null;
      }
      if (!detailsEl.open || !geo || !kakaoMap || !window.kakao) return;
      const total = (info.restaurant || 0) + (info.cafe || 0) + (info.convenience || 0);
      const intensity = Math.min(0.55, Math.max(0.15, total / 400));
      commercialCircle = new kakao.maps.Circle({
        center: new kakao.maps.LatLng(geo.lat, geo.lon),
        radius: info.radius_m || 500,
        strokeWeight: 1,
        strokeColor: "#2F5FD6",
        strokeOpacity: 0.5,
        fillColor: "#2F5FD6",
        fillOpacity: intensity,
      });
      commercialCircle.setMap(kakaoMap);
    };
  }
}

function numOrNull(v) {
  if (v === "" || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

function renderPyeongFilter(valuations) {
  const el = $("#pyeong-filter");
  el.innerHTML = "";
  if (valuations.length <= 1) return; // 평형이 하나뿐이면 필터가 의미 없음

  const pyeongs = [...new Set(valuations.map((v) => v.pyeong))].sort((a, b) => a - b);

  const makeButton = (label, pyeongValue, active) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    if (active) btn.classList.add("active");
    btn.addEventListener("click", () => {
      $$(".pyeong-filter button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      $$(".pyeong-card").forEach((card) => {
        const show = pyeongValue === null || card.dataset.pyeong === String(pyeongValue);
        card.hidden = !show;
      });
    });
    return btn;
  };

  el.appendChild(makeButton("전체", null, true));
  for (const p of pyeongs) {
    el.appendChild(makeButton(`${p}평`, p, false));
  }
}

function renderPyeongList(data) {
  const container = $("#pyeong-list");
  container.innerHTML = "";
  const template = $("#pyeong-card-template");
  const manualListings = data.manual_listings || [];

  if (!data.valuations.length) {
    $("#pyeong-filter").innerHTML = "";
    container.innerHTML = `<p class="status">최근 36개월간 매칭되는 실거래 내역이 없습니다.</p>`;
    return;
  }

  renderPyeongFilter(data.valuations);

  for (const v of data.valuations) {
    const node = template.content.cloneNode(true);
    const pyeongLabel = `${v.pyeong}평 (전용 ${v.avg_exclusive_area}㎡)`;

    node.querySelector(".pyeong-card").dataset.pyeong = String(v.pyeong);
    node.querySelector("h3").textContent = pyeongLabel;
    const badge = node.querySelector(".confidence-badge");
    badge.textContent = { high: "신뢰도 높음", medium: "신뢰도 보통", low: "신뢰도 낮음" }[v.confidence];
    badge.classList.add(v.confidence);

    const breakoutBadge = node.querySelector(".breakout-badge");
    if (v.is_breakout === true) {
      breakoutBadge.textContent = "전고점 돌파";
      breakoutBadge.className = "breakout-badge new-high";
    } else if (v.is_breakout === false && v.gap_to_peak_pct >= -5) {
      breakoutBadge.textContent = `전고점 근접 (${v.gap_to_peak_pct}%)`;
      breakoutBadge.className = "breakout-badge near-high";
    } else if (v.is_breakout === false) {
      breakoutBadge.textContent = `전고점 대비 ${v.gap_to_peak_pct}%`;
      breakoutBadge.className = "breakout-badge below-high";
    } else {
      breakoutBadge.hidden = true;
    }

    const summary = node.querySelector(".valuation-summary");
    summary.innerHTML = `
      <div class="box">
        <div class="label">추정 적정가</div>
        <div class="value accent">${fmtWon(v.fair_price_10k)}</div>
      </div>
      <div class="box">
        <div class="label">직전거래가 (${v.last_deal_date ?? "-"})</div>
        <div class="value">${fmtWon(v.last_deal_price_10k)}</div>
      </div>
      <div class="box">
        <div class="label">전고점 (최근 3년, ${v.peak_date ?? "-"})</div>
        <div class="value">${fmtWon(v.peak_price_10k)}</div>
      </div>
      <div class="box band-box">
        <div class="label">최근 거래 가격대 (적정가 대비)</div>
        ${bandBoxContent(v)}
      </div>
      <div class="box">
        <div class="label">표본 수 (1년 / 3년)</div>
        <div class="value">${v.sample_count_1y} / ${v.sample_count_3y}</div>
      </div>
      <div class="box">
        <div class="label">전세가 중앙값 (최근 1년)</div>
        <div class="value">${fmtWon(v.jeonse_median_10k)}</div>
      </div>
      <div class="box">
        <div class="label">전세 최고가 (최근 1년)</div>
        <div class="value">${fmtWon(v.jeonse_max_10k)}</div>
      </div>
      <div class="box">
        <div class="label">전세가율 (중앙값 기준)</div>
        <div class="value">${v.jeonse_ratio_pct != null ? `${v.jeonse_ratio_pct}%` : "-"}
          ${v.jeonse_sample_count_1y ? `<span style="font-weight:400;color:var(--muted);font-size:11px"> (표본 ${v.jeonse_sample_count_1y})</span>` : ""}
        </div>
      </div>
    `;

    const calcNoteEl = node.querySelector(".calc-note");
    calcNoteEl.textContent = buildCalcNote(v);

    const judgementEl = node.querySelector(".judgement-banner");

    const canvas = node.querySelector(".trade-chart");
    setTimeout(() => renderChart(canvas, v), 0);

    const listingForm = node.querySelector(".manual-listing-form");
    const currentDiv = node.querySelector(".manual-listing-current");
    const calcPriceInput = node.querySelector(".cost-calc-price");
    const calcResultEl = node.querySelector(".cost-calc-result");

    function refreshJudgement(askMin, askMax) {
      const householdCnt = data.manual_complex_info?.household_cnt ?? data.basis_info?.household_cnt;
      const info = judgementInfo(v, askMin, askMax, householdCnt);
      if (!info) {
        judgementEl.hidden = true;
        judgementEl.className = "judgement-banner";
        return;
      }
      judgementEl.hidden = false;
      judgementEl.className = `judgement-banner ${info.cls}`;
      judgementEl.innerHTML = `${info.headline}<ul class="verdict-reasons">${info.reasons.map((r) => `<li>${r}</li>`).join("")}</ul>`;
    }

    function refreshCostCalc() {
      const price10k = numOrNull(calcPriceInput.value);
      if (!price10k) {
        calcResultEl.innerHTML = "";
        return;
      }
      const fee = brokerFee(price10k);
      const tax = acquisitionTax(price10k, v.avg_exclusive_area);
      calcResultEl.innerHTML = `
        <div class="box">
          <div class="label">중개수수료 상한 (요율 ${(fee.rate * 100).toFixed(2)}%)</div>
          <div class="value">${fmtWon(Math.round(fee.fee / 10000))}</div>
        </div>
        <div class="box">
          <div class="label">부가세(10%) 별도</div>
          <div class="value">${fmtWon(Math.round(fee.vat / 10000))}</div>
        </div>
        <div class="box">
          <div class="label">취득세 (세율 ${tax.taxRate.toFixed(2)}%)</div>
          <div class="value">${fmtWon(Math.round(tax.acquisitionTax / 10000))}</div>
        </div>
        <div class="box">
          <div class="label">지방교육세+농특세</div>
          <div class="value">${fmtWon(Math.round((tax.eduTax + tax.ruralTax) / 10000))}</div>
        </div>
        <div class="box">
          <div class="label">취득 관련 세금 합계</div>
          <div class="value accent">${fmtWon(Math.round(tax.total / 10000))}</div>
        </div>
      `;
    }

    const existing = manualListings.find((m) => m.pyeong_label === pyeongLabel);
    if (existing) {
      listingForm.listing_count.value = existing.listing_count ?? "";
      listingForm.ask_price_min.value = existing.ask_price_min ?? "";
      listingForm.ask_price_max.value = existing.ask_price_max ?? "";
      currentDiv.textContent = `마지막 입력: 매물 ${existing.listing_count ?? "-"}건, 호가 ${fmtWon(existing.ask_price_min)} ~ ${fmtWon(existing.ask_price_max)}`;
    }

    const initialAskMin = existing?.ask_price_min;
    const initialAskMax = existing?.ask_price_max;
    refreshJudgement(initialAskMin, initialAskMax);
    calcPriceInput.value = Math.round(
      initialAskMin && initialAskMax
        ? (initialAskMin + initialAskMax) / 2
        : initialAskMin || initialAskMax || v.fair_price_10k || 0
    ) || "";
    refreshCostCalc();
    calcPriceInput.addEventListener("input", refreshCostCalc);

    listingForm.onsubmit = async (e) => {
      e.preventDefault();
      const body = {
        complex_key: data.complex_key,
        pyeong_label: pyeongLabel,
        exclusive_area: v.avg_exclusive_area,
        listing_count: numOrNull(listingForm.listing_count.value),
        ask_price_min: numOrNull(listingForm.ask_price_min.value),
        ask_price_max: numOrNull(listingForm.ask_price_max.value),
      };
      await fetch("/api/manual-listings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      currentDiv.textContent = `마지막 입력: 매물 ${body.listing_count ?? "-"}건, 호가 ${fmtWon(body.ask_price_min)} ~ ${fmtWon(body.ask_price_max)}`;
      refreshJudgement(body.ask_price_min, body.ask_price_max);
      if (body.ask_price_min || body.ask_price_max) {
        calcPriceInput.value = Math.round(
          body.ask_price_min && body.ask_price_max
            ? (body.ask_price_min + body.ask_price_max) / 2
            : body.ask_price_min || body.ask_price_max
        );
        refreshCostCalc();
      }
    };

    container.appendChild(node);
  }
}

function renderChart(canvas, v) {
  // 매매 실거래(3년) + 전세 실거래(1년) + 추정 적정가 기준선을 한 화면에서 비교한다.
  const saleTrades = v.trades_3y || [];
  const jeonseTrades = v.jeonse_trades_1y || [];
  const allDates = [...saleTrades, ...jeonseTrades].map((t) => t.date).sort();
  if (!allDates.length) return;

  const epoch = new Date(allDates[0]);
  const dayOffset = (dateStr) => Math.round((new Date(dateStr) - epoch) / 86400000);
  const fmtDate = (days) => {
    const d = new Date(epoch);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  };

  const salePoints = saleTrades.map((t) => ({ x: dayOffset(t.date), y: t.price_10k / 10000, date: t.date }));
  const jeonsePoints = jeonseTrades.map((t) => ({ x: dayOffset(t.date), y: t.price_10k / 10000, date: t.date }));

  const minX = 0;
  const maxX = dayOffset(allDates[allDates.length - 1]);

  const datasets = [
    {
      label: "매매 실거래(억원)",
      data: salePoints,
      backgroundColor: "#2563eb",
      showLine: false,
    },
    {
      label: "전세 실거래(억원)",
      data: jeonsePoints,
      backgroundColor: "#16a34a",
      showLine: false,
    },
  ];

  if (v.fair_price_10k) {
    datasets.push({
      label: "추정 적정가",
      data: [
        { x: minX, y: v.fair_price_10k / 10000 },
        { x: maxX, y: v.fair_price_10k / 10000 },
      ],
      type: "line",
      borderColor: "#dc2626",
      borderDash: [6, 4],
      borderWidth: 1.5,
      pointRadius: 0,
      fill: false,
    });
  }

  if (v.peak_price_10k) {
    datasets.push({
      label: "전고점(3년)",
      data: [
        { x: minX, y: v.peak_price_10k / 10000 },
        { x: maxX, y: v.peak_price_10k / 10000 },
      ],
      type: "line",
      borderColor: "#9333ea",
      borderDash: [2, 3],
      borderWidth: 1.5,
      pointRadius: 0,
      fill: false,
    });
  }

  new Chart(canvas, {
    type: "scatter",
    data: { datasets },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true, labels: { boxWidth: 10, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: (items) => items[0].raw.date || fmtDate(items[0].parsed.x),
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          min: minX,
          max: maxX,
          ticks: {
            callback: (value) => fmtDate(value),
            maxTicksLimit: 6,
          },
        },
        y: { title: { display: true, text: "억원" } },
      },
    },
  });
}

function renderLiquidityRanking(data) {
  const resultEl = $("#liquidity-result");
  if (!data.ranking.length) {
    resultEl.innerHTML = `<p class="hint" style="margin:8px 0 0">조건에 맞는 거래가 없습니다. 가격대를 넓혀보세요.</p>`;
    return;
  }
  const rows = data.ranking
    .map(
      (r, i) => `
      <tr>
        <td class="rank">${i + 1}</td>
        <td>${r.apt_name} <span style="color:var(--muted);font-weight:400">(${r.dong})</span></td>
        <td class="count">${r.trade_count}건</td>
        <td>${fmtWon(r.avg_price_10k)}</td>
        <td>${r.avg_pyeong ? `${r.avg_pyeong}평` : "-"}</td>
      </tr>`
    )
    .join("");
  resultEl.innerHTML = `
    <p class="hint" style="margin:8px 0 0">
      ${data.sigungu.sido} ${data.sigungu.sigungu} · 최근 ${data.period_months}개월 기준, 조건에 맞는 단지 ${data.total_matched_complexes}곳 중 상위 ${data.ranking.length}곳
    </p>
    <div class="liquidity-table-wrap">
      <table class="liquidity-table">
        <thead><tr><th>순위</th><th>단지 (동)</th><th>거래건수</th><th>평균가</th><th>평균평형</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

$("#liquidity-search-btn").addEventListener("click", async () => {
  const resultEl = $("#liquidity-result");
  const sigungu = $("#liquidity-sigungu").value.trim();
  if (!sigungu) {
    resultEl.innerHTML = `<p class="hint" style="margin:8px 0 0;color:#dc2626">시군구를 입력하세요.</p>`;
    return;
  }
  const priceMin = numOrNull($("#liquidity-price-min").value);
  const priceMax = numOrNull($("#liquidity-price-max").value);

  resultEl.innerHTML = `<p class="hint" style="margin:8px 0 0">조회 중...</p>`;
  try {
    const params = new URLSearchParams({ sigungu });
    if (priceMin) params.set("price_min", priceMin);
    if (priceMax) params.set("price_max", priceMax);
    const res = await fetch(`/api/liquidity-ranking?${params}`);
    const data = await res.json();
    if (!res.ok) {
      resultEl.innerHTML = `<p class="hint" style="margin:8px 0 0;color:#dc2626">${data.detail || "조회 실패"}</p>`;
      return;
    }
    renderLiquidityRanking(data);
  } catch (err) {
    resultEl.innerHTML = `<p class="hint" style="margin:8px 0 0;color:#dc2626">네트워크 오류: ${err}</p>`;
  }
});

function estimateLoanCapacity10k(incomeMan, ratePct, years) {
  const annualCapacity = incomeMan * 0.4; // DSR 40% 간이 기준
  const monthlyCapacity = annualCapacity / 12;
  const monthlyRate = ratePct / 100 / 12;
  const n = years * 12;
  if (monthlyRate === 0) return monthlyCapacity * n;
  return (monthlyCapacity * (1 - Math.pow(1 + monthlyRate, -n))) / monthlyRate;
}

const BUDGET_METRIC_TABS = [
  { key: "trade_count_1y", label: "거래량순", dir: -1 },
  { key: "momentum_pct", label: "최근 1년 상승률순", dir: -1 },
  { key: "subway_distance", label: "지하철 인접순", dir: 1 },
];

let budgetCandidates = [];

function candidateCardHtml(c, i) {
  const subway = c.subway_info
    ? `${c.subway_info.station_name}${c.subway_info.lines?.length ? ` (${c.subway_info.lines.join(", ")})` : ""} · 도보 ${c.subway_info.walk_minutes}분`
    : "인근 역 없음";
  const school = c.school_info;
  const elem = school?.elementary ? `${school.elementary.name} (${Math.round(school.elementary.distance_m / 67)}분)` : "-";
  const mid = school?.middle ? `${school.middle.name} (${Math.round(school.middle.distance_m / 67)}분)` : "-";
  const momentumCls = c.momentum_pct > 0 ? "up" : c.momentum_pct < 0 ? "down" : "";
  const momentumSign = c.momentum_pct > 0 ? "+" : "";

  return `
    <div class="candidate-card">
      <div class="cc-head">
        <span class="cc-rank">${i + 1}</span>
        <span class="cc-name">${c.apt_name}</span>
        <span class="cc-dong">${c.dong}${c.avg_pyeong ? ` · ${c.avg_pyeong}평` : ""}</span>
      </div>
      <div class="cc-meta">
        <div><div class="label">평균가</div><div class="value">${fmtWon(c.avg_price_10k)}</div></div>
        <div><div class="label">최근 1년 거래</div><div class="value">${c.trade_count_1y}건</div></div>
        <div><div class="label">최근 1년 상승률</div><div class="value ${momentumCls}">${momentumSign}${c.momentum_pct}%</div></div>
        <div><div class="label">인근 지하철</div><div class="value">${subway}</div></div>
        <div><div class="label">가까운 초등학교</div><div class="value">${elem}</div></div>
        <div><div class="label">가까운 중학교</div><div class="value">${mid}</div></div>
      </div>
    </div>
  `;
}

function renderBudgetCandidates(sortKey) {
  const listEl = $("#budget-candidate-list");
  if (!listEl) return;
  const sorted = [...budgetCandidates];
  if (sortKey === "subway_distance") {
    sorted.sort((a, b) => {
      const da = a.subway_info?.distance_m ?? Infinity;
      const db = b.subway_info?.distance_m ?? Infinity;
      return da - db;
    });
  } else if (sortKey) {
    const tab = BUDGET_METRIC_TABS.find((t) => t.key === sortKey);
    sorted.sort((a, b) => (a[sortKey] - b[sortKey]) * tab.dir);
  }
  listEl.innerHTML = sorted.map((c, i) => candidateCardHtml(c, i)).join("");
}

async function runBudgetScreening() {
  const resultEl = $("#budget-result");
  const sigungu = $("#budget-sigungu").value.trim();
  const cash = numOrNull($("#budget-cash").value) || 0;
  const income = numOrNull($("#budget-income").value);
  const rate = numOrNull($("#budget-rate").value) || 4.5;
  const years = numOrNull($("#budget-years").value) || 30;

  if (!sigungu || !income) {
    resultEl.innerHTML = `<p class="hint" style="margin:8px 0 0;color:#dc2626">시군구와 연소득을 입력하세요.</p>`;
    return;
  }

  const loanCapacity = estimateLoanCapacity10k(income, rate, years);
  const budget = cash + loanCapacity;

  resultEl.innerHTML = `
    <div class="budget-summary">
      추정 대출 한도(DSR 40% 기준) <b>${fmtWon(Math.round(loanCapacity))}</b> · 보유 현금 ${fmtWon(cash)}
      → 총 예산 <b>${fmtWon(Math.round(budget))}</b>
    </div>
    <p class="hint" style="margin:4px 0 0">후보 조회 중...</p>
  `;

  try {
    const params = new URLSearchParams({ sigungu, max_price: Math.round(budget) });
    const res = await fetch(`/api/budget-screening?${params}`);
    const data = await res.json();
    if (!res.ok) {
      resultEl.innerHTML += `<p class="hint" style="margin:8px 0 0;color:#dc2626">${data.detail || "조회 실패"}</p>`;
      return;
    }
    budgetCandidates = data.candidates;
    if (!budgetCandidates.length) {
      resultEl.innerHTML = `
        <div class="budget-summary">
          추정 대출 한도(DSR 40% 기준) <b>${fmtWon(Math.round(loanCapacity))}</b> · 보유 현금 ${fmtWon(cash)}
          → 총 예산 <b>${fmtWon(Math.round(budget))}</b>
        </div>
        <p class="hint" style="margin:8px 0 0">이 예산 내에서 최근 1년 거래가 충분한 단지를 찾지 못했습니다. 시군구를 바꾸거나 예산을 조정해보세요.</p>
      `;
      return;
    }
    const tabsHtml = `
      <div class="metric-tabs">
        <button type="button" class="active" data-sort="">전체</button>
        ${BUDGET_METRIC_TABS.map((t) => `<button type="button" data-sort="${t.key}">${t.label}</button>`).join("")}
      </div>
    `;
    resultEl.innerHTML = `
      <div class="budget-summary">
        추정 대출 한도(DSR 40% 기준) <b>${fmtWon(Math.round(loanCapacity))}</b> · 보유 현금 ${fmtWon(cash)}
        → 총 예산 <b>${fmtWon(Math.round(budget))}</b>
      </div>
      <p class="hint" style="margin:8px 0 0">${data.sigungu.sido} ${data.sigungu.sigungu} · 예산 내 최근 1년 거래 있는 단지 ${data.total_matched_complexes}곳 중 거래량 상위 ${budgetCandidates.length}곳</p>
      ${tabsHtml}
      <div id="budget-candidate-list" class="candidate-card-list"></div>
    `;
    $$(".metric-tabs button", resultEl).forEach((btn) => {
      btn.addEventListener("click", () => {
        $$(".metric-tabs button", resultEl).forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        renderBudgetCandidates(btn.dataset.sort);
      });
    });
    renderBudgetCandidates("");
  } catch (err) {
    resultEl.innerHTML += `<p class="hint" style="margin:8px 0 0;color:#dc2626">네트워크 오류: ${err}</p>`;
  }
}

$("#budget-search-btn").addEventListener("click", runBudgetScreening);

checkHealth();
