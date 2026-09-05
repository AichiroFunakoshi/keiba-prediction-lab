"use strict";

const byId = (id) => document.getElementById(id);
let currentState = null;
let selectedVenueIndex = 0;
const percent = (value) => `${(value * 100).toFixed(1)}%`;
const smallPercent = (value) => {
  const percentage = value * 100;
  if (percentage === 0) return "0%";
  if (percentage < 0.01) return `${percentage.toFixed(3)}%`;
  if (percentage < 0.1) return `${percentage.toFixed(2)}%`;
  return `${percentage.toFixed(1)}%`;
};
const dateTime = (value) => value ? new Intl.DateTimeFormat("ja-JP", {
  dateStyle: "medium", timeStyle: "short"
}).format(new Date(value)) : "—";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function runnerDisplayMap(runnerDisplay = []) {
  return new Map(runnerDisplay.map((item) => [item.horse_id, item]));
}

function renderTicket(prediction, displayById) {
  const selection = byId("official-selection");
  selection.replaceChildren();
  prediction.actual.selection.forEach((horseId, index) => {
    if (index > 0) selection.append(node("span", "ticket-arrow", "→"));
    const display = displayById.get(horseId);
    const number = node(
      "span", "ticket-number", display ? String(display.horse_number) : horseId
    );
    if (display) number.title = `${display.horse_name}（馬ID: ${horseId}）`;
    selection.append(number);
  });
}

function renderRanking(prediction, displayById) {
  const body = byId("ranking-body");
  body.replaceChildren();
  const visibleRunners = prediction.runners.slice(0, 8);
  byId("ranking-summary").textContent = prediction.runners.length > 8
    ? `全${prediction.runners.length}頭中、1着確率上位8頭を表示`
    : `全${prediction.runners.length}頭`;
  visibleRunners.forEach((runner) => {
    const display = displayById.get(runner.horse_id);
    const row = document.createElement("tr");
    const rank = document.createElement("td");
    rank.append(node("span", "rank-pill", String(runner.predicted_rank)));
    row.append(rank);
    const horseNumber = node(
      "span",
      display ? `runner-number frame-${display.frame_number}` : "runner-number",
      display ? String(display.horse_number) : "—"
    );
    const horseNumberCell = document.createElement("td");
    horseNumberCell.append(horseNumber);
    row.append(horseNumberCell);
    const horseName = node("td", "runner-name", display?.horse_name || runner.horse_id);
    if (display) horseName.title = `馬ID: ${runner.horse_id}`;
    row.append(horseName);
    row.append(node("td", "probability", percent(runner.win_probability)));
    row.append(node("td", "probability", percent(runner.top3_probability)));
    body.append(row);
  });
}

function renderShadows(prediction) {
  const groups = byId("shadow-groups");
  groups.replaceChildren();
  const grouped = new Map();
  prediction.shadow_portfolios.forEach((portfolio) => {
    const portfolios = grouped.get(portfolio.generator) || [];
    portfolios.push(portfolio);
    grouped.set(portfolio.generator, portfolios);
  });
  for (const [generator, portfolios] of grouped) {
    const group = node("section", "shadow-group");
    group.append(node("h3", "", generator === "baseline" ? "ベースライン" : "ペースシナリオ"));
    const strategies = [...new Set(portfolios.map((row) => row.strategy))];
    const ticketCounts = [...new Set(portfolios.map((row) => row.ticket_count))];
    const matrix = node("div", "shadow-matrix");
    matrix.style.setProperty("--strategy-count", String(strategies.length));
    matrix.append(node("span", "matrix-label", "点数"));
    strategies.forEach((strategy) => {
      matrix.append(node("span", "matrix-heading", strategy.replaceAll("_", " ")));
    });
    ticketCounts.forEach((ticketCount) => {
      matrix.append(node("span", "matrix-label", `${ticketCount}点・0円`));
      strategies.forEach((strategy) => {
        const portfolio = portfolios.find(
          (row) => row.ticket_count === ticketCount && row.strategy === strategy
        );
        const cell = node("div", "matrix-cell");
        if (portfolio) {
          const track = node("div", "bar-track");
          const fill = node("div", "bar-fill");
          fill.style.width = `${Math.min(100, portfolio.cumulative_probability * 100)}%`;
          track.append(fill);
          cell.append(track);
          cell.append(node("span", "shadow-value", percent(portfolio.cumulative_probability)));
        } else {
          cell.append(node("span", "shadow-value", "—"));
        }
        matrix.append(cell);
      });
    });
    group.append(matrix);
    groups.append(group);
  }
}

function renderPrediction(prediction, runnerDisplay = []) {
  if (!prediction) {
    byId("prediction-view").hidden = true;
    byId("empty-prediction").hidden = false;
    return;
  }
  byId("prediction-view").hidden = false;
  byId("empty-prediction").hidden = true;
  byId("race-title").textContent = prediction.race_id;
  byId("scheduled-at").textContent = dateTime(prediction.scheduled_at);
  byId("context-scheduled").textContent = dateTime(prediction.scheduled_at);
  byId("context-frozen").textContent = dateTime(prediction.frozen_at);
  byId("context-model").textContent = prediction.model_version;
  byId("context-input").textContent = prediction.input_data_version;
  const displayById = runnerDisplayMap(runnerDisplay);
  renderTicket(prediction, displayById);
  const winner = prediction.runners[0];
  const winnerDisplay = displayById.get(winner.horse_id);
  const winnerNumber = byId("winner-number");
  winnerNumber.className = winnerDisplay
    ? `horse-number frame-${winnerDisplay.frame_number}`
    : "horse-number";
  winnerNumber.textContent = winnerDisplay ? winnerDisplay.horse_number : winner.predicted_rank;
  byId("winner-label").textContent = winnerDisplay ? "予測1位の馬" : "予測1位の馬ID";
  byId("winner-id").textContent = winnerDisplay?.horse_name || winner.horse_id;
  byId("winner-id").title = winnerDisplay ? `馬ID: ${winner.horse_id}` : "";
  byId("winner-probability").textContent = percent(winner.win_probability);
  renderRanking(prediction, displayById);
  renderShadows(prediction);
}

function compactTicket(selection, target, displayById = new Map()) {
  target.replaceChildren();
  selection.forEach((horseId, index) => {
    if (index > 0) target.append(node("span", "compact-arrow", "→"));
    const display = displayById.get(horseId);
    const number = node("span", "compact-number", display ? String(display.horse_number) : horseId);
    if (display) {
      number.title = `${display.horse_name}（馬ID: ${horseId}）`;
    }
    target.append(number);
  });
}

function showDetail(prediction, runnerDisplay = []) {
  byId("dashboard").hidden = true;
  byId("detail-app").hidden = false;
  byId("back-overview").hidden = !currentState?.race_day;
  renderPrediction(prediction, runnerDisplay);
  renderValidation(currentState?.walk_forward || null);
  window.scrollTo(0, 0);
}

function renderVenue(raceDay, venueIndex) {
  selectedVenueIndex = venueIndex;
  const venue = raceDay.venues[venueIndex];
  byId("race-title").textContent = `${venue.venue} 全レース`;
  byId("venue-tabs").querySelectorAll("button").forEach((button, index) => {
    button.setAttribute("aria-selected", String(index === venueIndex));
    button.tabIndex = index === venueIndex ? 0 : -1;
  });
  const rows = byId("race-rows");
  rows.replaceChildren();
  venue.races.forEach((race) => {
    const prediction = race.prediction;
    const winner = prediction.runners[0];
    const displayById = runnerDisplayMap(race.runner_display || []);
    const winnerDisplay = displayById.get(winner.horse_id);
    const row = node("button", "ledger-row");
    row.type = "button";
    row.setAttribute("aria-label", `${venue.venue} ${race.race_number}Rの詳細を見る`);
    row.append(node("strong", "race-number", `${race.race_number}R`));
    row.append(node("time", "race-time", new Intl.DateTimeFormat("ja-JP", {
      hour: "2-digit", minute: "2-digit"
    }).format(new Date(prediction.scheduled_at))));
    const winnerCell = node("span", "ledger-winner");
    const winnerMark = node(
      "b", winnerDisplay ? `winner-mark frame-${winnerDisplay.frame_number}` : "winner-mark",
      winnerDisplay ? String(winnerDisplay.horse_number) : `${winner.predicted_rank}位`
    );
    winnerCell.append(winnerMark);
    const winnerName = node("strong", "", winnerDisplay?.horse_name || winner.horse_id);
    if (winnerDisplay) winnerName.title = `馬ID: ${winner.horse_id}`;
    winnerCell.append(winnerName);
    row.append(winnerCell);
    row.append(node("b", "ledger-probability", percent(winner.win_probability)));
    const ticket = node("span", "compact-ticket ledger-ticket");
    compactTicket(prediction.actual.selection, ticket, displayById);
    row.append(ticket);
    const detail = node("span", "detail-link", "詳細を見る");
    row.append(detail);
    row.addEventListener("click", () => {
      showDetail(prediction, race.runner_display || []);
    });
    rows.append(row);
  });
}

function renderDashboard(raceDay) {
  byId("detail-app").hidden = true;
  byId("dashboard").hidden = false;
  byId("dashboard-toolbar").hidden = false;
  byId("race-ledger").hidden = false;
  byId("back-overview").hidden = true;
  byId("dashboard-date").textContent = new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "full"
  }).format(new Date(`${raceDay.race_date}T00:00:00+09:00`));
  byId("scheduled-at").textContent = "開催日予測";
  const tabs = byId("venue-tabs");
  tabs.replaceChildren();
  raceDay.venues.forEach((venue, index) => {
    const tab = node("button", "venue-tab", venue.venue);
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", "race-ledger");
    tab.addEventListener("click", () => renderVenue(raceDay, index));
    tab.addEventListener("keydown", (event) => {
      const lastIndex = raceDay.venues.length - 1;
      let nextIndex = null;
      if (event.key === "ArrowLeft") nextIndex = index === 0 ? lastIndex : index - 1;
      if (event.key === "ArrowRight") nextIndex = index === lastIndex ? 0 : index + 1;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = lastIndex;
      if (nextIndex === null) return;
      event.preventDefault();
      renderVenue(raceDay, nextIndex);
      tabs.children[nextIndex].focus();
    });
    tabs.append(tab);
  });
  renderVenue(raceDay, Math.min(selectedVenueIndex, raceDay.venues.length - 1));
}

function renderWin5Only() {
  byId("detail-app").hidden = true;
  byId("dashboard").hidden = false;
  byId("dashboard-toolbar").hidden = true;
  byId("race-ledger").hidden = true;
  byId("back-overview").hidden = true;
  byId("race-title").textContent = "WIN5影予測";
  byId("scheduled-at").textContent = "研究用・0円";
}

function renderValidation(walkForward) {
  byId("validation").hidden = !walkForward;
  byId("empty-validation").hidden = Boolean(walkForward);
  if (!walkForward) return;
  byId("metric-races").textContent = String(walkForward.evaluation_race_count);
  byId("metric-model").textContent = percent(walkForward.model.top1_accuracy);
  byId("metric-uniform").textContent = percent(walkForward.uniform.top1_accuracy);
  byId("metric-ece").textContent = walkForward.expected_calibration_error.toFixed(3);
}

function findRunnerDisplay(raceDay, raceId, horseId) {
  if (!raceDay) return null;
  for (const venue of raceDay.venues) {
    const race = venue.races.find((item) => item.prediction.race_id === raceId);
    if (!race) continue;
    return (race.runner_display || []).find((item) => item.horse_id === horseId) || null;
  }
  return null;
}

function renderWin5(win5, raceDay = null) {
  const section = byId("win5");
  section.hidden = !win5;
  if (!win5) return;
  const legs = byId("win5-legs");
  legs.replaceChildren();
  win5.legs.forEach((leg, index) => {
    const item = node("article", "win5-leg");
    item.append(node("span", "win5-leg-number", `対象${index + 1}`));
    const display = findRunnerDisplay(raceDay, leg.race_id, leg.selected_horse_id);
    const selection = node("div", "win5-selection");
    if (display) {
      selection.append(node(
        "span", `winner-mark frame-${display.frame_number}`,
        String(display.horse_number)
      ));
    }
    const horseName = node("strong", "", display?.horse_name || leg.selected_horse_id);
    if (display) horseName.title = `馬ID: ${leg.selected_horse_id}`;
    selection.append(horseName);
    item.append(selection);
    item.append(node("small", "", leg.race_id));
    item.append(node("b", "", percent(leg.selected_win_probability)));
    legs.append(item);
  });
  byId("win5-probability").textContent = smallPercent(win5.joint_probability);
  byId("win5-assumption").textContent = win5.independence_assumption;
}

async function loadState() {
  const reload = byId("reload");
  reload.disabled = true;
  byId("error").hidden = true;
  try {
    const response = await fetch("/api/v1/state", {cache: "no-store"});
    if (!response.ok) throw new Error(`読み込みに失敗しました（${response.status}）`);
    const state = await response.json();
    if (!state.is_valid) throw new Error("監査済みデータではありません");
    currentState = state;
    byId("context-policy").textContent = state.actual_purchase_policy;
    if (state.race_day) {
      renderDashboard(state.race_day);
      renderWin5(state.win5, state.race_day);
    } else if (state.prediction) {
      showDetail(state.prediction);
    } else if (state.win5) {
      renderWin5Only();
      renderWin5(state.win5);
    } else {
      showDetail(null);
    }
    byId("loading").hidden = true;
  } catch (error) {
    byId("loading").hidden = true;
    byId("dashboard").hidden = true;
    byId("detail-app").hidden = true;
    byId("error").textContent = error instanceof Error ? error.message : "読み込みに失敗しました";
    byId("error").hidden = false;
  } finally {
    reload.disabled = false;
  }
}

byId("reload").addEventListener("click", loadState);
byId("back-overview").addEventListener("click", () => {
  if (currentState?.race_day) renderDashboard(currentState.race_day);
});
loadState();
