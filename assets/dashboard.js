"use strict";

const get = (id) => document.getElementById(id);
const day = get("day");
const portions = get("portions");
const buffer = get("buffer");
const statusText = get("status");
const refresh = get("refresh");
const table = get("table-container");
const rows = get("reservation-rows");
const empty = get("empty");
const api = new URL(document.querySelector('meta[name="dashboard-api"]').content, window.location.href);
const viewKey = new URLSearchParams(window.location.hash.slice(1)).get("view");
const dailyCaption = new Intl.DateTimeFormat("zh-TW", { dateStyle: "full", timeZone: "UTC" });
const taipeiDate = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
});
const periods = { lunch: "午餐", dinner: "晚餐" };
let counts = null;
let currentRequest = null;

function todayInTaipei() {
  const parts = Object.fromEntries(taipeiDate.formatToParts().map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function addDays(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function showCounts(reservations) {
  counts = reservations === null ? null : { lunch: { guests: 0, bookings: 0 }, dinner: { guests: 0, bookings: 0 } };
  for (const reservation of reservations || []) {
    const group = counts[reservation.period];
    group.guests += reservation.people;
    group.bookings += 1;
  }
  for (const period of Object.keys(periods)) {
    get(`${period}-guests`).textContent = counts?.[period].guests ?? "—";
    get(`${period}-bookings`).textContent = counts?.[period].bookings ?? "—";
  }
  get("total-guests").textContent = counts ? counts.lunch.guests + counts.dinner.guests : "—";
  get("total-bookings").textContent = counts ? counts.lunch.bookings + counts.dinner.bookings : "—";
  showEstimates();
}

function showEstimates() {
  const valid = [portions, buffer].every((input) => input.value !== "" && input.validity.valid);
  get("estimate-error").hidden = valid;
  const storedPortions = Math.round(portions.valueAsNumber * 10);
  const multiplier = 100 + buffer.valueAsNumber;
  const estimate = (period) => Math.ceil(counts[period].guests * storedPortions * multiplier / 1000);
  const lunch = valid && counts ? estimate("lunch") : null;
  const dinner = valid && counts ? estimate("dinner") : null;
  get("lunch-portions").textContent = lunch ?? "—";
  get("dinner-portions").textContent = dinner ?? "—";
  get("total-portions").textContent = lunch === null ? "—" : lunch + dinner;
}

function showReservations(reservations) {
  rows.replaceChildren();
  for (const booking of reservations) {
    const row = rows.insertRow();
    row.insertCell().textContent = `${booking.start}–${booking.end}`;
    const period = document.createElement("span");
    period.className = `meal-tag meal-${booking.period}`;
    period.textContent = periods[booking.period];
    row.insertCell().append(period);
    row.insertCell().textContent = booking.reference;
    row.insertCell().textContent = `${booking.people} 位`;
    row.insertCell().textContent = booking.groups.join("＋") || "—";
  }
  table.hidden = reservations.length === 0;
  empty.hidden = reservations.length !== 0;
}

async function loadDay() {
  currentRequest?.abort();
  const controller = new AbortController();
  currentRequest = controller;
  rows.replaceChildren();
  table.hidden = true;
  empty.hidden = true;
  showCounts(null);
  statusText.classList.remove("status-error");
  if (!day.value) {
    statusText.textContent = "請選擇營業日期。";
    refresh.disabled = false;
    return;
  }
  const selectedDay = day.value;
  get("date-caption").textContent = dailyCaption.format(new Date(`${selectedDay}T00:00:00Z`));
  if (!viewKey) {
    get("access-note").hidden = false;
    statusText.textContent = "等待專屬檢視連結";
    refresh.disabled = false;
    return;
  }
  statusText.textContent = "載入訂位中…";
  refresh.disabled = true;
  const url = new URL(api);
  url.searchParams.set("day", selectedDay);
  try {
    const response = await fetch(url, {
      cache: "no-store", credentials: "omit", signal: controller.signal,
      headers: { Authorization: `Bearer ${viewKey}` },
    });
    if (response.status === 401) throw new Error("這個專屬連結已失效，請向管理者索取新連結。");
    if (!response.ok) throw new Error("讀取訂位失敗，請重新整理。");
    const result = await response.json();
    if (currentRequest !== controller) return;
    showCounts(result.reservations);
    showReservations(result.reservations);
    statusText.textContent = `已載入 ${result.reservations.length} 筆訂位`;
  } catch (error) {
    if (currentRequest !== controller) return;
    statusText.classList.add("status-error");
    statusText.textContent = error instanceof TypeError ? "無法連上訂位伺服器，請重新整理。" : error.message;
  } finally {
    if (currentRequest === controller) refresh.disabled = false;
  }
}

function changeDay(offset) {
  day.value = addDays(day.value || todayInTaipei(), offset);
  loadDay();
}

get("previous").addEventListener("click", () => changeDay(-1));
get("next").addEventListener("click", () => changeDay(1));
get("today").addEventListener("click", () => { day.value = todayInTaipei(); loadDay(); });
refresh.addEventListener("click", loadDay);
day.addEventListener("change", loadDay);
portions.addEventListener("input", showEstimates);
buffer.addEventListener("input", showEstimates);
day.value = todayInTaipei();
loadDay();
